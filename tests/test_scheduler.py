"""Tests for the scheduler: poll cycle, fan RPM detection, and collector wiring."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from collections import deque

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
    _BASELINE_SAMPLES,
    _build_collectors,
    _detect_fan_dips,
    _poll_cycle,
    _report_miners_to_api,
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
# Real-world baseline: fans run ~6900 RPM (6780–6960 observed).
# A 15% dip from 6960 peak = ~5916 RPM threshold.
# ---------------------------------------------------------------

MINER_URL = "http://10.0.1.9"

# Realistic normal readings cycling through observed values
_NORMAL_RPMS = [6960, 6960, 6900, 6900]  # positions 0-3


def _cooling(rpms: list[int]) -> CoolingData:
    return CoolingData(
        fans=[FanReading(position=i, rpm=rpm, target_speed_ratio=1.0) for i, rpm in enumerate(rpms)]
    )


def _warm_up(url: str = MINER_URL, rpms: list[int] = _NORMAL_RPMS):
    """Feed _BASELINE_SAMPLES normal readings to establish a baseline. Returns state dicts."""
    fan_rpm_history: dict = {}
    fan_dip_times: dict = {}
    miner_last_detected: dict = {}
    for _ in range(_BASELINE_SAMPLES):
        _detect_fan_dips(url, _cooling(rpms), fan_rpm_history, fan_dip_times, miner_last_detected)
    return fan_rpm_history, fan_dip_times, miner_last_detected


class TestDetectFanDips:

    def test_no_detection_before_baseline_full(self):
        """Should never fire before the rolling window is full."""
        fan_rpm_history, fan_dip_times, miner_last_detected = {}, {}, {}
        dipped_rpm = [int(r * 0.80) for r in _NORMAL_RPMS]  # big drop
        for _ in range(_BASELINE_SAMPLES - 1):
            result = _detect_fan_dips(
                MINER_URL, _cooling(dipped_rpm),
                fan_rpm_history, fan_dip_times, miner_last_detected,
            )
            assert result == [], "Should not detect before baseline is established"

    def test_no_false_positive_at_normal_rpm(self):
        """Normal readings should never trigger detection."""
        fan_rpm_history, fan_dip_times, miner_last_detected = _warm_up()
        for _ in range(10):
            result = _detect_fan_dips(
                MINER_URL, _cooling(_NORMAL_RPMS),
                fan_rpm_history, fan_dip_times, miner_last_detected,
            )
            assert result == []

    def test_single_fan_dip_recorded(self):
        """A single fan dipping should update fan_dip_times but not trigger full detection."""
        fan_rpm_history, fan_dip_times, miner_last_detected = _warm_up()

        # Dip only fan 0 (~20% drop from 6960 peak → 5568 RPM)
        rpms = [5568, 6900, 6900, 6900]
        result = _detect_fan_dips(
            MINER_URL, _cooling(rpms),
            fan_rpm_history, fan_dip_times, miner_last_detected,
        )
        assert result == [], "Single fan dip should not trigger all-fans detection"
        assert (MINER_URL, 0) in fan_dip_times, "Fan 0 dip time should be recorded"
        assert (MINER_URL, 1) not in fan_dip_times, "Fan 1 should not have a dip time"

    def test_all_fans_dip_triggers_detection(self):
        """All fans dropping >15% simultaneously should return all positions."""
        fan_rpm_history, fan_dip_times, miner_last_detected = _warm_up()

        # ~20% drop across all fans (flip the switch scenario)
        dipped_rpms = [int(r * 0.80) for r in _NORMAL_RPMS]
        result = _detect_fan_dips(
            MINER_URL, _cooling(dipped_rpms),
            fan_rpm_history, fan_dip_times, miner_last_detected,
        )
        assert sorted(result) == [0, 1, 2, 3]

    def test_detection_respects_cooldown(self):
        """Second detection within cooldown window should not fire."""
        fan_rpm_history, fan_dip_times, miner_last_detected = _warm_up()
        dipped_rpms = [int(r * 0.80) for r in _NORMAL_RPMS]

        first = _detect_fan_dips(
            MINER_URL, _cooling(dipped_rpms),
            fan_rpm_history, fan_dip_times, miner_last_detected,
        )
        assert first != [], "First detection should fire"

        second = _detect_fan_dips(
            MINER_URL, _cooling(dipped_rpms),
            fan_rpm_history, fan_dip_times, miner_last_detected,
        )
        assert second == [], "Second detection within cooldown should be suppressed"

    def test_small_rpm_variation_no_false_positive(self):
        """Natural variation seen in real data (6780–6960) should not trigger dip."""
        fan_rpm_history, fan_dip_times, miner_last_detected = _warm_up(rpms=[6960, 6960, 6960, 6960])

        # Real observed low readings — only ~3% below peak
        low_normal = [6780, 6780, 6900, 6840]
        result = _detect_fan_dips(
            MINER_URL, _cooling(low_normal),
            fan_rpm_history, fan_dip_times, miner_last_detected,
        )
        assert result == [], "Natural RPM variation should not be flagged as a dip"

    def test_non_cooling_data_returns_empty(self):
        """Non-CoolingData input should return [] without crashing."""
        fan_rpm_history, fan_dip_times, miner_last_detected = {}, {}, {}
        result = _detect_fan_dips(
            MINER_URL, object(),
            fan_rpm_history, fan_dip_times, miner_last_detected,
        )
        assert result == []


# ---------------------------------------------------------------
# _resolve_miners
# ---------------------------------------------------------------

class TestResolveMiners:

    def test_returns_config_miners_when_discovery_disabled(self):
        """Miners explicitly in cfg["miners"] are returned even with discovery off."""
        cfg = {
            "miners": [
                {"url": "http://10.0.0.1", "name": "legacy-a", "firmware": "braiins"},
                {"url": "http://10.0.0.2", "name": "legacy-b", "firmware": "luxos"},
            ],
            "discovery": {"enabled": False},
        }
        result = _resolve_miners(cfg)
        assert len(result) == 2
        assert result[0]["url"] == "http://10.0.0.1"
        assert result[1]["url"] == "http://10.0.0.2"

    def test_returns_empty_when_no_miners_and_discovery_disabled(self):
        cfg = {"discovery": {"enabled": False}}
        assert _resolve_miners(cfg) == []

    def test_config_miners_included_when_discovery_enabled(self, monkeypatch):
        """Legacy config miners survive even when the discovery scan finds nothing."""
        monkeypatch.setattr(
            "wright_telemetry.scheduler.discover_miners",
            lambda **_kw: [],
        )
        cfg = {
            "miners": [{"url": "http://10.0.0.1", "name": "legacy", "firmware": "braiins"}],
            "discovery": {"enabled": True, "subnets": ["10.0.0.0/24"]},
        }
        result = _resolve_miners(cfg)
        assert any(m["url"] == "http://10.0.0.1" for m in result)

    def test_discovered_miners_merged_with_config_miners(self, monkeypatch):
        """Discovery results are merged in-memory alongside legacy config miners."""
        from wright_telemetry.discovery import DiscoveredMiner

        monkeypatch.setattr(
            "wright_telemetry.scheduler.discover_miners",
            lambda **_kw: [
                DiscoveredMiner(ip="10.0.0.5", firmware="braiins", hostname="new", mac_address="AA:BB:CC:DD:EE:05"),
            ],
        )
        cfg = {
            "miners": [{"url": "http://10.0.0.1", "name": "legacy", "firmware": "braiins", "mac_address": "AA:BB:CC:DD:EE:01"}],
            "discovery": {"enabled": True, "subnets": ["10.0.0.0/24"]},
        }
        result = _resolve_miners(cfg)
        urls = [m["url"] for m in result]
        assert "http://10.0.0.1" in urls   # legacy preserved
        assert "http://10.0.0.5" in urls   # newly discovered added

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


# ---------------------------------------------------------------
# _report_miners_to_api
# ---------------------------------------------------------------

class TestReportMinersToApi:

    def _make_identity(self, uid: str, ip: str = "10.0.0.1", firmware: str = "braiins") -> MinerIdentity:
        return MinerIdentity(
            uid=uid,
            serial_number="SN-001",
            hostname=f"miner-{uid}",
            mac_address="AA:BB:CC:DD:EE:01",
            ip_address=ip,
            firmware=firmware,
        )

    def test_sends_mark_miner_for_each_identity(self):
        api_client = MagicMock()
        api_client.send.return_value = True

        identities = {
            "http://10.0.0.1": self._make_identity("uid-1", "10.0.0.1"),
            "http://10.0.0.2": self._make_identity("uid-2", "10.0.0.2"),
        }
        _report_miners_to_api(api_client, "fac-1", identities)

        assert api_client.send.call_count == 2
        for call in api_client.send.call_args_list:
            payload = call.args[0]
            assert payload.metric_type == "mark_miner"
            assert payload.facility_id == "fac-1"

    def test_payload_data_contains_identity_fields(self):
        api_client = MagicMock()
        api_client.send.return_value = True

        identity = self._make_identity("uid-1", "10.0.0.1", "braiins")
        _report_miners_to_api(api_client, "fac-1", {"http://10.0.0.1": identity})

        payload = api_client.send.call_args.args[0]
        assert payload.data["ip"] == "10.0.0.1"
        assert payload.data["firmware"] == "braiins"
        assert payload.data["hostname"] == "miner-uid-1"
        assert payload.data["mac_address"] == "AA:BB:CC:DD:EE:01"

    def test_api_failure_does_not_raise(self):
        """A send() exception must not propagate — the loop should continue."""
        api_client = MagicMock()
        api_client.send.side_effect = RuntimeError("network down")

        identities = {"http://10.0.0.1": self._make_identity("uid-1")}
        # Should not raise
        _report_miners_to_api(api_client, "fac-1", identities)

    def test_empty_identities_sends_nothing(self):
        api_client = MagicMock()
        _report_miners_to_api(api_client, "fac-1", {})
        api_client.send.assert_not_called()
