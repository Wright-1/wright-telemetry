"""Unit tests for all from_braiins() model factory methods."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from wright_telemetry.models import (
    CoolingData,
    ErrorData,
    HashboardData,
    HashrateData,
    MinerIdentity,
    TelemetryPayload,
    UptimeData,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "braiins"


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / name).read_text())


# ---------------------------------------------------------------
# CoolingData.from_braiins
# ---------------------------------------------------------------

class TestCoolingDataFromBraiins:

    def test_full_data(self):
        raw = _load("cooling_state.json")
        cd = CoolingData.from_braiins(raw)
        assert len(cd.fans) == 4
        assert cd.fans[1].rpm == 4150
        assert cd.highest_temperature["value"] == 72.5

    def test_empty_fans(self):
        cd = CoolingData.from_braiins({"fans": []})
        assert cd.fans == []
        assert cd.highest_temperature is None

    def test_missing_keys(self):
        cd = CoolingData.from_braiins({})
        assert cd.fans == []
        assert cd.highest_temperature is None

    def test_fan_defaults_on_missing_fields(self):
        cd = CoolingData.from_braiins({"fans": [{}]})
        assert cd.fans[0].position == 0
        assert cd.fans[0].rpm == 0
        assert cd.fans[0].target_speed_ratio == 0.0

    def test_asdict_round_trip(self):
        raw = _load("cooling_state.json")
        cd = CoolingData.from_braiins(raw)
        d = asdict(cd)
        assert isinstance(d["fans"], list)
        assert d["fans"][0]["rpm"] == 4200


# ---------------------------------------------------------------
# HashrateData.from_braiins
# ---------------------------------------------------------------

class TestHashrateDataFromBraiins:

    def test_full_data(self):
        raw = _load("miner_stats.json")
        hr = HashrateData.from_braiins(raw)
        assert hr.miner_stats["ghs_5s"] == 145230.5
        assert hr.power_stats["watts"] == 3245

    def test_empty_sections(self):
        hr = HashrateData.from_braiins({})
        assert hr.miner_stats == {}
        assert hr.pool_stats == {}
        assert hr.power_stats == {}


# ---------------------------------------------------------------
# UptimeData.from_braiins
# ---------------------------------------------------------------

class TestUptimeDataFromBraiins:

    def test_full_data(self):
        raw = _load("miner_details.json")
        ud = UptimeData.from_braiins(raw)
        assert ud.bosminer_uptime_s == 1728000
        assert ud.system_uptime_s == 1729200
        assert ud.hostname == "antminer-rack3-slot7"
        assert ud.bos_version["major"] == "24"
        assert ud.platform == 1

    def test_defaults(self):
        ud = UptimeData.from_braiins({})
        assert ud.bosminer_uptime_s == 0
        assert ud.system_uptime_s == 0
        assert ud.hostname == ""
        assert ud.bos_version == {}
        assert ud.platform == 0
        assert ud.status == 0


# ---------------------------------------------------------------
# HashboardData.from_braiins
# ---------------------------------------------------------------

class TestHashboardDataFromBraiins:

    def test_full_data(self):
        raw = _load("hashboards.json")
        hbd = HashboardData.from_braiins(raw)
        assert len(hbd.hashboards) == 3

    def test_board_fields(self):
        raw = _load("hashboards.json")
        hbd = HashboardData.from_braiins(raw)
        b = hbd.hashboards[2]
        assert b.board_name == "Hashboard 2"
        assert b.chips_count == 114
        assert b.enabled is True
        assert b.stats["serial_number"] == "HB2-2391AX-C"

    def test_empty_hashboards(self):
        hbd = HashboardData.from_braiins({"hashboards": []})
        assert hbd.hashboards == []

    def test_missing_key(self):
        hbd = HashboardData.from_braiins({})
        assert hbd.hashboards == []

    def test_board_defaults(self):
        hbd = HashboardData.from_braiins({"hashboards": [{}]})
        b = hbd.hashboards[0]
        assert b.board_name == ""
        assert b.board_temp is None
        assert b.chips_count == 0
        assert b.id == ""
        assert b.enabled is False
        assert b.stats == {}


# ---------------------------------------------------------------
# ErrorData.from_braiins
# ---------------------------------------------------------------

class TestErrorDataFromBraiins:

    def test_full_data(self):
        raw = _load("miner_errors.json")
        ed = ErrorData.from_braiins(raw)
        assert len(ed.errors) == 2
        assert ed.errors[1].error_codes[0]["code"] == "FAN_RPM_LOW"

    def test_empty_errors(self):
        ed = ErrorData.from_braiins({"errors": []})
        assert ed.errors == []

    def test_missing_key(self):
        ed = ErrorData.from_braiins({})
        assert ed.errors == []

    def test_error_entry_defaults(self):
        ed = ErrorData.from_braiins({"errors": [{}]})
        e = ed.errors[0]
        assert e.message == ""
        assert e.timestamp == ""
        assert e.error_codes == []
        assert e.components == []


# ---------------------------------------------------------------
# MinerIdentity
# ---------------------------------------------------------------

class TestMinerIdentity:

    def test_to_dict(self):
        mi = MinerIdentity(
            uid="abc", serial_number="SN1",
            hostname="miner1", mac_address="AA:BB:CC:DD:EE:FF",
        )
        d = mi.to_dict()
        assert d["uid"] == "abc"
        assert d["wright_fans"] is False

    def test_wright_fans_flag(self):
        mi = MinerIdentity(
            uid="abc", serial_number="SN1",
            hostname="miner1", mac_address="AA:BB:CC:DD:EE:FF",
            wright_fans=True,
        )
        assert mi.to_dict()["wright_fans"] is True


# ---------------------------------------------------------------
# TelemetryPayload
# ---------------------------------------------------------------

class TestTelemetryPayload:

    def test_to_dict_structure(self):
        mi = MinerIdentity(uid="u", serial_number="s", hostname="h", mac_address="m")
        tp = TelemetryPayload(
            metric_type="cooling",
            facility_id="fac-1",
            miner_identity=mi,
            data={"fans": []},
        )
        d = tp.to_dict()
        assert d["metric_type"] == "cooling"
        assert d["facility_id"] == "fac-1"
        assert d["miner_identity"]["uid"] == "u"
        assert d["data"] == {"fans": []}
        assert "timestamp" in d
