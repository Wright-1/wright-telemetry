"""Tests for the scheduler: poll cycle, fan RPM detection, and collector wiring."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional
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
    _check_fan_rpm_changes,
    _poll_cycle,
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
# _check_fan_rpm_changes
# ---------------------------------------------------------------

class TestCheckFanRpmChanges:

    def test_initial_reading_no_events(self, stub_collector):
        """First reading has no previous state, so no transitions."""
        cooling = stub_collector.fetch_cooling()
        prev: dict[tuple[str, int], int] = {}
        drops: list[dict] = []
        events = _check_fan_rpm_changes("miner1", cooling, "http://10.0.0.1", prev, drops)
        assert events == []
        assert len(prev) == 4

    def test_fan_off_detected(self):
        """RPM drops from >0 to 0 => off event."""
        prev: dict[tuple[str, int], int] = {("http://m", 0): 4200}
        drops: list[dict] = []
        cooling = CoolingData(
            fans=[FanReading(position=0, rpm=0, target_speed_ratio=0.0)],
        )
        events = _check_fan_rpm_changes("m", cooling, "http://m", prev, drops)
        assert len(events) == 1
        assert events[0]["transition_type"] == "off"
        assert events[0]["prev_rpm"] == 4200
        assert events[0]["curr_rpm"] == 0
        assert len(drops) == 1
        assert drops[0]["recovered_at"] is None

    def test_fan_on_detected(self):
        """RPM rises from 0 to >0 => on event, closes the drop."""
        prev: dict[tuple[str, int], int] = {("http://m", 0): 0}
        drops: list[dict] = [
            {
                "miner": "m",
                "miner_url": "http://m",
                "fan_position": 0,
                "prev_rpm": 4200,
                "detected_at": 1000.0,
                "recovered_at": None,
                "duration_s": None,
            }
        ]
        cooling = CoolingData(
            fans=[FanReading(position=0, rpm=4100, target_speed_ratio=0.65)],
        )
        events = _check_fan_rpm_changes("m", cooling, "http://m", prev, drops)
        assert len(events) == 1
        assert events[0]["transition_type"] == "on"
        assert drops[0]["recovered_at"] is not None
        assert drops[0]["duration_s"] is not None

    def test_stable_rpm_no_events(self):
        """Same RPM as before => no events."""
        prev: dict[tuple[str, int], int] = {("http://m", 0): 4200}
        drops: list[dict] = []
        cooling = CoolingData(
            fans=[FanReading(position=0, rpm=4200, target_speed_ratio=0.65)],
        )
        events = _check_fan_rpm_changes("m", cooling, "http://m", prev, drops)
        assert events == []

    def test_non_cooling_data_ignored(self):
        prev: dict[tuple[str, int], int] = {}
        drops: list[dict] = []
        events = _check_fan_rpm_changes("m", HashrateData({}, {}, {}), "http://m", prev, drops)
        assert events == []


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
        fan_prev: dict[tuple[str, int], int] = {}
        fan_drops: list[dict] = []

        from wright_telemetry.baseline import BaselineTracker
        _poll_cycle(
            [(miner_cfg, stub_collector)],
            identities, api_client, metrics, "fac-1",
            fan_prev, fan_drops, BaselineTracker(),
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
        fan_prev: dict[tuple[str, int], int] = {}
        fan_drops: list[dict] = []

        from wright_telemetry.baseline import BaselineTracker
        _poll_cycle(
            [(miner_cfg, collector)],
            identities, api_client, metrics, "fac-1",
            fan_prev, fan_drops, BaselineTracker(),
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
