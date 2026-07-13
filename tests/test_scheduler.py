"""Tests for the scheduler: poll cycle, fan RPM detection, and collector wiring."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from wright_telemetry.collectors.base import MinerCollector
from wright_telemetry.models import (
    CoolingData,
    ErrorData,
    FanReading,
    HashboardData,
    HashrateData,
    MinerIdentity,
    UptimeData,
)
from wright_telemetry.scheduler import (
    _build_collectors,
    _detect_fan_dips,
    _poll_cycle,
    _resolve_miners,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "braiins"


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / name).read_text())


# ---------------------------------------------------------------
# Stub collector that returns fixture data without network calls
# ---------------------------------------------------------------

class StubCollector(MinerCollector):
    def __init__(self, url: str, fixtures: dict[str, Any]):
        super().__init__(url)
        self._fixtures = fixtures

    def authenticate(self) -> None:
        pass

    def fetch_identity(self) -> MinerIdentity:
        raw = self._fixtures["miner_details"]
        return MinerIdentity(
            uid=raw.get("uid", ""),
            serial_number=raw.get("serial_number", ""),
            hostname=raw.get("hostname", ""),
            mac_address=raw.get("mac_address", ""),
        )

    def fetch_cooling(self) -> CoolingData:
        return CoolingData.from_braiins(self._fixtures["cooling_state"])

    def fetch_hashrate(self) -> HashrateData:
        return HashrateData.from_braiins(self._fixtures["miner_stats"])

    def fetch_uptime(self) -> UptimeData:
        return UptimeData.from_braiins(self._fixtures["miner_details"])

    def fetch_hashboards(self) -> HashboardData:
        return HashboardData.from_braiins(self._fixtures["hashboards"])

    def fetch_errors(self) -> ErrorData:
        return ErrorData.from_braiins(self._fixtures["miner_errors"])


@pytest.fixture()
def all_fixtures() -> dict[str, Any]:
    return {
        "cooling_state": _load("cooling_state.json"),
        "miner_stats": _load("miner_stats.json"),
        "miner_details": _load("miner_details.json"),
        "hashboards": _load("hashboards.json"),
        "miner_errors": _load("miner_errors.json"),
    }


@pytest.fixture()
def stub_collector(all_fixtures) -> StubCollector:
    return StubCollector(url="http://10.0.0.1", fixtures=all_fixtures)


# ---------------------------------------------------------------
# _poll_cycle
# ---------------------------------------------------------------

class TestPollCycle:

    def test_sends_all_metrics(self, stub_collector):
        miner_cfg = {"url": "http://10.0.0.1", "name": "test-miner"}
        identity = stub_collector.fetch_identity()
        identities = {"http://10.0.0.1": identity}

        api_client = MagicMock()
        api_client.send.return_value = True

        metrics = ["cooling", "hashrate", "uptime", "hashboards", "errors"]

        from wright_telemetry.baseline import BaselineTracker
        _poll_cycle(
            [(miner_cfg, stub_collector)],
            identities, api_client, metrics, "fac-1",
            BaselineTracker(),
        )

        sent_types = [call.args[0].metric_type for call in api_client.send.call_args_list]
        for m in metrics:
            assert m in sent_types

    def test_metric_failure_doesnt_crash(self, all_fixtures):
        """If one metric fetch throws, other metrics still get sent."""

        class PartiallyBrokenCollector(StubCollector):
            def fetch_hashrate(self) -> HashrateData:
                raise ConnectionError("simulated failure")

        collector = PartiallyBrokenCollector("http://10.0.0.1", all_fixtures)
        miner_cfg = {"url": "http://10.0.0.1", "name": "broken"}
        identity = collector.fetch_identity()
        identities = {"http://10.0.0.1": identity}

        api_client = MagicMock()
        api_client.send.return_value = True

        metrics = ["cooling", "hashrate", "uptime"]

        from wright_telemetry.baseline import BaselineTracker
        _poll_cycle(
            [(miner_cfg, collector)],
            identities, api_client, metrics, "fac-1",
            BaselineTracker(),
        )

        sent_types = [call.args[0].metric_type for call in api_client.send.call_args_list]
        assert "cooling" in sent_types
        assert "uptime" in sent_types
        assert "hashrate" not in sent_types


# ---------------------------------------------------------------
# _build_collectors
# ---------------------------------------------------------------

class TestBuildCollectors:

    def test_creates_braiins_collector(self):
        import wright_telemetry.collectors.braiins  # noqa: F401
        miners = [
            {"url": "http://10.0.0.1", "username": "root", "firmware": "braiins"},
        ]
        result = _build_collectors(miners)
        assert len(result) == 1
        assert result[0][0]["url"] == "http://10.0.0.1"

    def test_default_type_braiins(self):
        import wright_telemetry.collectors.braiins  # noqa: F401
        miners = [{"url": "http://10.0.0.2"}]
        result = _build_collectors(miners, default_collector_type="braiins")
        assert len(result) == 1

    def test_unknown_type_raises(self):
        miners = [{"url": "http://10.0.0.3", "firmware": "nonexistent_firmware"}]
        with pytest.raises(ValueError, match="Unknown collector type"):
            _build_collectors(miners)


# ---------------------------------------------------------------
# _detect_fan_dips
# Baseline is a fixed snapshot (captured once at session start), not a
# rolling window. A dip is an isolated single fan reading at or below
# _DIP_RPM_MAX (a physical switch flip spins the fan down to near-zero,
# not some percentage of baseline) — if more than one fan on the miner is
# low at the same tick, it's ambiguous (power loss, hardware issue) and no
# dip event fires for anyone that tick. Every tick the isolated fan stays
# low fires its own "dip" record (so the shape of a multi-second dip is
# visible), and a single "recovered" record fires once, on the tick it
# crosses back above threshold — recovery is evaluated independently per
# fan, not gated by isolation.
# Real-world baseline: fans run ~6900 RPM (6780–6960 observed).
# ---------------------------------------------------------------

MINER_URL = "http://10.0.1.9"

# Realistic normal readings cycling through observed values
_NORMAL_RPMS = [6960, 6960, 6900, 6900]  # positions 0-3


def _cooling(rpms: list[int]) -> CoolingData:
    return CoolingData(
        fans=[FanReading(position=i, rpm=rpm, target_speed_ratio=1.0) for i, rpm in enumerate(rpms)]
    )


def _baseline(rpms: list[int] = _NORMAL_RPMS, url: str = MINER_URL) -> dict[tuple[str, int], int]:
    return {(url, i): rpm for i, rpm in enumerate(rpms)}


class TestDetectFanDips:

    def test_no_false_positive_at_baseline_rpm(self):
        """Readings that match the baseline should never trigger a dip."""
        baseline = _baseline()
        for _ in range(10):
            result = _detect_fan_dips(MINER_URL, _cooling(_NORMAL_RPMS), baseline, {})
            assert result == []

    def test_small_rpm_variation_no_false_positive(self):
        """Natural oscillation (±4% in the real fleet) never approaches _DIP_RPM_MAX."""
        baseline = _baseline([6960, 6960, 6960, 6960])
        low_normal = [6700, 6700, 6700, 6700]  # normal jitter, nowhere near 1000 RPM
        result = _detect_fan_dips(MINER_URL, _cooling(low_normal), baseline, {})
        assert result == []

    def test_isolated_single_fan_dip_emits_one_dip_event(self):
        """One fan dropping to near-zero while the others hold steady triggers detection."""
        baseline = _baseline()
        rpms = [200, 6960, 6900, 6900]  # fan 0 switched off; others unchanged
        dipped_state: dict = {}
        result = _detect_fan_dips(MINER_URL, _cooling(rpms), baseline, dipped_state)
        assert len(result) == 1
        assert result[0]["fan_position"] == 0
        assert result[0]["direction"] == "dip"
        assert dipped_state[(MINER_URL, 0)] is True

    def test_dip_refires_every_tick_while_down(self):
        """Dips only last a few seconds — this is not edge-triggered/deduped, so we
        can see the full shape of the RPM trace while the fan stays down."""
        baseline = _baseline()
        rpms = [200, 6960, 6900, 6900]
        dipped_state: dict = {}
        first = _detect_fan_dips(MINER_URL, _cooling(rpms), baseline, dipped_state)
        second = _detect_fan_dips(MINER_URL, _cooling(rpms), baseline, dipped_state)
        assert len(first) == 1
        assert len(second) == 1
        assert second[0]["direction"] == "dip"

    def test_recovery_emits_single_deduped_event(self):
        """The recovery edge (crossing back above threshold) fires exactly once."""
        baseline = _baseline()
        rpms = [200, 6960, 6900, 6900]
        dipped_state: dict = {}
        _detect_fan_dips(MINER_URL, _cooling(rpms), baseline, dipped_state)

        recovered = _detect_fan_dips(MINER_URL, _cooling(_NORMAL_RPMS), baseline, dipped_state)
        assert len(recovered) == 1
        assert recovered[0]["fan_position"] == 0
        assert recovered[0]["direction"] == "recovered"

        # Staying at baseline afterward should not re-fire recovery
        again = _detect_fan_dips(MINER_URL, _cooling(_NORMAL_RPMS), baseline, dipped_state)
        assert again == []

    def test_multiple_fans_low_at_once_is_suppressed(self):
        """More than one fan low at the same tick isn't a valid switch-test
        signature (power loss, hardware fault) — no dip event fires for anyone."""
        baseline = _baseline()
        rpms = [200, 200, 6900, 6900]  # two fans low simultaneously
        result = _detect_fan_dips(MINER_URL, _cooling(rpms), baseline, {})
        assert result == []

    def test_all_fans_dip_together_is_suppressed(self):
        """All fans dropping simultaneously is not a single-switch-flip signature."""
        baseline = _baseline()
        rpms = [200, 200, 200, 200]
        result = _detect_fan_dips(MINER_URL, _cooling(rpms), baseline, {})
        assert result == []

    def test_second_fan_joining_an_existing_dip_suppresses_further_dip_events(self):
        """An isolated dip fires normally; once a second fan also goes low,
        further "dip" events stop (ambiguous), but the first fan's dipped
        state is preserved so it resumes firing once isolation returns."""
        baseline = _baseline()
        dipped_state: dict = {}
        first = _detect_fan_dips(MINER_URL, _cooling([200, 6960, 6900, 6900]), baseline, dipped_state)
        assert len(first) == 1

        ambiguous = _detect_fan_dips(MINER_URL, _cooling([200, 200, 6900, 6900]), baseline, dipped_state)
        assert ambiguous == []
        assert dipped_state[(MINER_URL, 0)] is True  # unchanged, not cleared

        isolated_again = _detect_fan_dips(MINER_URL, _cooling([200, 6960, 6900, 6900]), baseline, dipped_state)
        assert len(isolated_again) == 1
        assert isolated_again[0]["fan_position"] == 0
        assert isolated_again[0]["direction"] == "dip"

    def test_missing_baseline_for_fan_is_skipped(self):
        """A fan position with no recorded baseline is silently skipped, not crashed on."""
        result = _detect_fan_dips(MINER_URL, _cooling([3000]), {}, {})
        assert result == []

    def test_non_cooling_data_returns_empty(self):
        """Non-CoolingData input should return [] without crashing."""
        result = _detect_fan_dips(MINER_URL, object(), _baseline(), {})
        assert result == []


# ---------------------------------------------------------------
# _resolve_miners
# ---------------------------------------------------------------

class TestResolveMiners:

    def test_returns_empty_when_config_miners_present_but_discovery_disabled(self):
        """cfg['miners'] is deprecated — _resolve_miners ignores it."""
        cfg = {
            "miners": [
                {"url": "http://10.0.0.1", "name": "legacy-a", "firmware": "braiins"},
            ],
            "discovery": {"enabled": False},
        }
        result = _resolve_miners(cfg)
        assert result == []

    def test_returns_empty_when_no_miners_and_discovery_disabled(self):
        cfg = {"discovery": {"enabled": False}}
        assert _resolve_miners(cfg) == []

    def test_discovery_scan_results_returned_when_enabled(self, monkeypatch):
        """Without a controller, _resolve_miners runs a subnet scan and returns results."""
        from wright_telemetry.discovery import DiscoveredMiner
        monkeypatch.setattr(
            "wright_telemetry.scheduler.discover_miners",
            lambda **_kw: [
                DiscoveredMiner(ip="10.0.0.5", firmware="braiins", hostname="found", mac_address="AA:BB:CC:DD:EE:05"),
            ],
        )
        cfg = {"discovery": {"enabled": True, "subnets": ["10.0.0.0/24"]}}
        result = _resolve_miners(cfg)
        assert len(result) == 1
        assert result[0]["url"] == "http://10.0.0.5"

    def test_controller_store_returned_when_controller_provided(self):
        """With a controller, _resolve_miners reads from the in-memory discovery store."""
        from unittest.mock import MagicMock
        controller = MagicMock()
        controller.get_discovered_miners.return_value = [
            {"url": "http://10.0.0.1", "name": "store-miner", "firmware": "braiins"},
            {"url": "http://10.0.0.5", "name": "store-miner-2", "firmware": "luxos"},
        ]
        cfg = {"discovery": {}}
        result = _resolve_miners(cfg, controller)
        urls = [m["url"] for m in result]
        assert "http://10.0.0.1" in urls
        assert "http://10.0.0.5" in urls

    def test_no_duplicates_when_config_miner_matches_discovered(self, monkeypatch):
        """A miner already in config is not duplicated when discovery finds the same MAC."""
        from wright_telemetry.discovery import DiscoveredMiner

        monkeypatch.setattr(
            "wright_telemetry.scheduler.discover_miners",
            lambda **_kw: [
                DiscoveredMiner(ip="10.0.0.1", firmware="braiins", hostname="same", mac_address="AA:BB:CC:DD:EE:01"),
            ],
        )
        cfg = {
            "miners": [{"url": "http://10.0.0.1", "name": "legacy", "firmware": "braiins", "mac_address": "AA:BB:CC:DD:EE:01"}],
            "discovery": {"enabled": True, "subnets": ["10.0.0.0/24"]},
        }
        result = _resolve_miners(cfg)
        assert len(result) == 1



