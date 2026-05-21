"""Unit tests for resolve_uid — the single source of truth for miner_uid
selection on the agent side. Mirrors the worker's MinerUidResolver behavior.
"""

from __future__ import annotations

import pytest

from wright_telemetry.models import resolve_uid


FACILITY = "fac_a"
MAC = "AA:BB:CC:DD:EE:01"
DERIVED = "fac_a:aa:bb:cc:dd:ee:01"


class TestPriority:
    def test_explicit_uid_wins(self):
        assert resolve_uid(FACILITY, "real-uid-001", "serial-xxx", MAC, "host") == "real-uid-001"

    def test_serial_used_when_uid_blank(self):
        assert resolve_uid(FACILITY, "", "serial-xxx", MAC, "host") == "serial-xxx"

    def test_serial_used_when_uid_none(self):
        assert resolve_uid(FACILITY, None, "serial-xxx", MAC, "host") == "serial-xxx"

    def test_derived_from_mac_when_uid_and_serial_missing(self):
        assert resolve_uid(FACILITY, None, None, MAC, "host") == DERIVED

    def test_derived_from_mac_when_uid_and_serial_blank(self):
        assert resolve_uid(FACILITY, "", "", MAC, "host") == DERIVED

    def test_hostname_used_as_last_resort(self):
        assert resolve_uid(FACILITY, None, None, None, "miner-host") == "miner-host"

    def test_returns_none_when_everything_missing(self):
        assert resolve_uid(FACILITY, None, None, None, None) is None

    def test_returns_none_when_everything_blank(self):
        assert resolve_uid(FACILITY, "", "", "", "") is None


class TestNormalization:
    def test_mac_lowercased_in_derived(self):
        assert resolve_uid(FACILITY, None, None, "Aa:bB:Cc:Dd:Ee:01", None) == DERIVED

    def test_whitespace_stripped_from_uid(self):
        assert resolve_uid(FACILITY, "  real-uid  ", None, None, None) == "real-uid"

    def test_whitespace_only_uid_treated_as_blank(self):
        assert resolve_uid(FACILITY, "   ", None, MAC, None) == DERIVED


class TestRejectsBogusValues:
    @pytest.mark.parametrize("bogus", ["unknown", "Unknown", "UNKNOWN", "n/a", "none", "null"])
    def test_obvious_placeholders_rejected_from_uid_and_serial_tiers(self, bogus):
        # Should fall through to MAC tier.
        assert resolve_uid(FACILITY, bogus, bogus, MAC, "host") == DERIVED

    def test_hostname_shaped_uid_rejected(self):
        # A uid containing '.' looks like a hostname — should fall through.
        assert resolve_uid(FACILITY, "miner-001.local", None, MAC, "host") == DERIVED

    def test_overlong_uid_rejected(self):
        long_uid = "x" * 200
        assert resolve_uid(FACILITY, long_uid, None, MAC, "host") == DERIVED


class TestMacValidation:
    @pytest.mark.parametrize("bad_mac", [
        "not-a-mac",
        "AA:BB:CC",            # too short
        "AA-BB-CC-DD-EE-01",   # dashes
        "AABBCCDDEE01",        # no separators
        "GG:BB:CC:DD:EE:01",   # invalid hex
        "AA:BB:CC:DD:EE:01:FF",  # too long
    ])
    def test_invalid_mac_does_not_produce_derived(self, bad_mac):
        # With no uid/serial and an invalid mac, only a hostname can save us.
        assert resolve_uid(FACILITY, None, None, bad_mac, None) is None
        # But if hostname exists, falls through to it.
        assert resolve_uid(FACILITY, None, None, bad_mac, "host") == "host"


class TestStableAcrossRestarts:
    """The whole point of the design: same physical miner, agent restarts and
    forgets/regenerates its uid, but MAC stays the same -> resolver still
    converges on the same canonical uid."""

    def test_unstable_uids_with_stable_mac_resolve_to_same_canonical_via_derivation(self):
        # When candidate_uid is rejected (e.g. hostname-shaped) the derivation
        # from MAC produces a stable result across calls. This mirrors the
        # worker-side guarantee for the agent's own pre-server resolution.
        uid_run_1 = resolve_uid(FACILITY, "ephemeral-12345.local", None, MAC, None)
        uid_run_2 = resolve_uid(FACILITY, "ephemeral-67890.local", None, MAC, None)
        assert uid_run_1 == uid_run_2 == DERIVED


class TestFacilityScoping:
    def test_same_mac_different_facility_yields_different_uid(self):
        a = resolve_uid("fac_a", None, None, MAC, None)
        b = resolve_uid("fac_b", None, None, MAC, None)
        assert a != b
        assert a == "fac_a:aa:bb:cc:dd:ee:01"
        assert b == "fac_b:aa:bb:cc:dd:ee:01"
