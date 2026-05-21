"""Resilience tests for _fetch_identities: the scheduler must keep running
through any combination of identifiable miners, unidentifiable miners, and
collector failures. No exception escapes; bad miners are skipped, good miners
are still in the returned dict.
"""

from __future__ import annotations

import logging
from typing import Any

from wright_telemetry.collectors.base import MinerCollector
from wright_telemetry.models import CoolingData, MinerIdentity
from wright_telemetry.scheduler import _fetch_identities


class _Stub(MinerCollector):
    """Minimal stub for _fetch_identities tests — only fetch_identity used."""

    def __init__(self, url: str, identity: MinerIdentity | None = None,
                 raise_exc: Exception | None = None):
        super().__init__(url)
        self._identity = identity
        self._raise_exc = raise_exc

    def authenticate(self) -> None:
        pass

    def fetch_identity(self) -> MinerIdentity:
        if self._raise_exc is not None:
            raise self._raise_exc
        assert self._identity is not None
        return self._identity

    # Unused but required by abstract base
    def fetch_cooling(self) -> CoolingData: raise NotImplementedError
    def fetch_hashrate(self): raise NotImplementedError
    def fetch_uptime(self): raise NotImplementedError
    def fetch_hashboards(self): raise NotImplementedError
    def fetch_errors(self): raise NotImplementedError


GOOD_ID = MinerIdentity(
    uid="real-uid-001",
    serial_number="SN-001",
    hostname="miner-1",
    mac_address="AA:BB:CC:DD:EE:01",
)
BLANK_ID = MinerIdentity(
    uid="",
    serial_number="",
    hostname="",
    mac_address="",
)
BLANK_WITH_MAC = MinerIdentity(
    uid="",
    serial_number="",
    hostname="",
    mac_address="AA:BB:CC:DD:EE:02",
)


def _cfg(url: str) -> dict[str, Any]:
    return {"url": url, "name": f"miner@{url}"}


class TestNoMinerCrashesAgent:
    def test_unidentifiable_miner_is_skipped_not_synthesized(self, caplog):
        """The old behavior fabricated uid='unknown' on collector failure or
        blank identity. That produced ghost rows server-side. New behavior:
        skip cleanly, return without that URL in the dict."""
        collectors = [
            (_cfg("http://10.0.0.1"), _Stub("http://10.0.0.1", identity=BLANK_ID)),
        ]
        with caplog.at_level(logging.WARNING):
            identities = _fetch_identities(collectors, facility_id="fac_a")

        assert identities == {}
        assert any("no usable identifier" in r.message for r in caplog.records)

    def test_collector_exception_is_caught_and_logged(self, caplog):
        collectors = [
            (_cfg("http://10.0.0.1"),
             _Stub("http://10.0.0.1", raise_exc=ConnectionError("boom"))),
        ]
        with caplog.at_level(logging.WARNING):
            # Must not raise — agent must keep running.
            identities = _fetch_identities(collectors, facility_id="fac_a")

        assert identities == {}
        assert any("Could not fetch identity" in r.message for r in caplog.records)

    def test_mix_of_good_bad_and_throwing_miners(self, caplog):
        """The classic scenario: 3 miners, one returns valid identity, one
        returns blanks, one throws. Good miner must still be present; the
        function must return cleanly."""
        collectors = [
            (_cfg("http://10.0.0.1"), _Stub("http://10.0.0.1", identity=GOOD_ID)),
            (_cfg("http://10.0.0.2"), _Stub("http://10.0.0.2", identity=BLANK_ID)),
            (_cfg("http://10.0.0.3"),
             _Stub("http://10.0.0.3", raise_exc=ConnectionError("auth fail"))),
        ]
        with caplog.at_level(logging.WARNING):
            identities = _fetch_identities(collectors, facility_id="fac_a")

        # Good miner present, others skipped.
        assert set(identities.keys()) == {"http://10.0.0.1"}
        assert identities["http://10.0.0.1"].uid == "real-uid-001"

    def test_blank_uid_with_valid_mac_resolves_to_derived(self):
        """Blank uid but a valid MAC should NOT be skipped — the resolver
        derives <facility>:<lower-mac> and the miner gets a stable id."""
        collectors = [
            (_cfg("http://10.0.0.1"),
             _Stub("http://10.0.0.1", identity=BLANK_WITH_MAC)),
        ]
        identities = _fetch_identities(collectors, facility_id="fac_a")

        assert "http://10.0.0.1" in identities
        assert identities["http://10.0.0.1"].uid == "fac_a:aa:bb:cc:dd:ee:02"

    def test_facility_id_propagates_into_derived_uid(self):
        # Build fresh identity instances per call: _fetch_identities mutates
        # the returned MinerIdentity (sets ip_address, uid) so sharing one
        # across calls would carry state.
        def _fresh_blank_with_mac() -> MinerIdentity:
            return MinerIdentity(
                uid="", serial_number="", hostname="",
                mac_address="AA:BB:CC:DD:EE:02",
            )

        a = _fetch_identities(
            [(_cfg("http://10.0.0.1"),
              _Stub("http://10.0.0.1", identity=_fresh_blank_with_mac()))],
            facility_id="fac_a",
        )
        b = _fetch_identities(
            [(_cfg("http://10.0.0.1"),
              _Stub("http://10.0.0.1", identity=_fresh_blank_with_mac()))],
            facility_id="fac_b",
        )
        assert a["http://10.0.0.1"].uid == "fac_a:aa:bb:cc:dd:ee:02"
        assert b["http://10.0.0.1"].uid == "fac_b:aa:bb:cc:dd:ee:02"

    def test_empty_collector_list_returns_empty_dict(self):
        # Edge case: no miners configured. Must not crash.
        identities = _fetch_identities([], facility_id="fac_a")
        assert identities == {}

    def test_back_propagates_resolved_uid_into_miner_cfg(self):
        """The function back-fills miner_cfg with resolved fields so the
        re-discovery loop can dedup by MAC. Verify the canonical uid (not
        the blank original) is what gets written back."""
        cfg = _cfg("http://10.0.0.1")
        collectors = [(cfg, _Stub("http://10.0.0.1", identity=BLANK_WITH_MAC))]

        _fetch_identities(collectors, facility_id="fac_a")
        # After resolution, the cfg should reflect the derived uid.
        assert cfg["uid"] == "fac_a:aa:bb:cc:dd:ee:02"
        assert cfg["mac_address"] == "AA:BB:CC:DD:EE:02"


class TestExceptionBoundary:
    """The agent must not be killable by anything _fetch_identities does.
    Including bugs in resolve_uid itself."""

    def test_unexpected_exception_in_collector_does_not_propagate(self):
        # Collector throws a non-network exception (e.g. parse error, KeyError).
        collectors = [
            (_cfg("http://10.0.0.1"),
             _Stub("http://10.0.0.1", raise_exc=KeyError("bad fixture"))),
            (_cfg("http://10.0.0.2"),
             _Stub("http://10.0.0.2", raise_exc=RuntimeError("internal bug"))),
        ]
        # Must complete without raising.
        identities = _fetch_identities(collectors, facility_id="fac_a")
        assert identities == {}
