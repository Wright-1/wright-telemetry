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
    HashboardData,
    HashrateData,
    MinerIdentity,
    UptimeData,
)
from wright_telemetry.scheduler import (
    _build_collectors,
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
