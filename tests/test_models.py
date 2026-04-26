"""Unit tests for all model factory methods across firmware types.

Each firmware's tests verify that the from_<firmware>() factory correctly
parses that firmware's raw API response format.  The cross-OS section
verifies that all three factories normalize to the same shape and values
when given fixture data that represents the same physical miner state.
"""

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

BRAIINS_DIR = Path(__file__).parent / "fixtures" / "braiins"
LUXOS_DIR = Path(__file__).parent / "fixtures" / "luxos"
VNISH_DIR = Path(__file__).parent / "fixtures" / "vnish"


def _b(name: str) -> dict[str, Any]:
    return json.loads((BRAIINS_DIR / name).read_text())


def _l(name: str) -> dict[str, Any]:
    return json.loads((LUXOS_DIR / name).read_text())


def _v(name: str) -> dict[str, Any]:
    return json.loads((VNISH_DIR / name).read_text())


# ---------------------------------------------------------------
# CoolingData
# ---------------------------------------------------------------

class TestCoolingDataFromBraiins:

    def test_full_data(self):
        cd = CoolingData.from_braiins(_b("cooling_state.json"))
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
        cd = CoolingData.from_braiins(_b("cooling_state.json"))
        d = asdict(cd)
        assert isinstance(d["fans"], list)
        assert d["fans"][0]["rpm"] == 4200


class TestCoolingDataFromLuxos:

    def test_full_data(self):
        cd = CoolingData.from_luxos(_l("fans.json"), _l("temps.json"))
        assert len(cd.fans) == 4
        assert cd.fans[1].rpm == 4150
        assert cd.highest_temperature is not None
        assert cd.highest_temperature["unit"] == "C"

    def test_fan_speed_ratio_normalised(self):
        cd = CoolingData.from_luxos(_l("fans.json"), _l("temps.json"))
        assert cd.fans[0].target_speed_ratio == pytest.approx(0.65)

    def test_highest_temp_is_max_of_all_chip_temps(self):
        cd = CoolingData.from_luxos(_l("fans.json"), _l("temps.json"))
        assert cd.highest_temperature["value"] == pytest.approx(74.0)

    def test_no_temps(self):
        cd = CoolingData.from_luxos(_l("fans.json"), {"TEMPS": []})
        assert cd.highest_temperature is None

    def test_empty_fans(self):
        cd = CoolingData.from_luxos({"FANS": []}, {"TEMPS": []})
        assert cd.fans == []


class TestCoolingDataFromVnish:

    def test_full_data(self):
        cd = CoolingData.from_vnish(_v("status.json"))
        assert len(cd.fans) == 4
        assert cd.fans[1].rpm == 4150
        assert cd.highest_temperature is not None
        assert cd.highest_temperature["unit"] == "C"

    def test_fan_speed_ratio_normalised(self):
        cd = CoolingData.from_vnish(_v("status.json"))
        assert cd.fans[0].target_speed_ratio == pytest.approx(0.65)

    def test_highest_temp_is_max_of_chain_temps(self):
        cd = CoolingData.from_vnish(_v("status.json"))
        assert cd.highest_temperature["value"] == pytest.approx(74.0)

    def test_empty_fans(self):
        cd = CoolingData.from_vnish({"fans": [], "chains": []})
        assert cd.fans == []
        assert cd.highest_temperature is None


# ---------------------------------------------------------------
# HashrateData
# ---------------------------------------------------------------

class TestHashrateDataFromBraiins:

    def test_full_data(self):
        hr = HashrateData.from_braiins(_b("miner_stats.json"))
        assert hr.miner_stats["ghs_5s"] == 145230.5
        assert hr.power_stats["watts"] == 3245

    def test_empty_sections(self):
        hr = HashrateData.from_braiins({})
        assert hr.miner_stats == {}
        assert hr.pool_stats == {}
        assert hr.power_stats == {}


class TestHashrateDataFromLuxos:

    def test_full_data(self):
        hr = HashrateData.from_luxos(_l("summary.json"), _l("pools.json"), _l("power.json"))
        assert hr.miner_stats["ghs_5s"] == pytest.approx(145230.5)
        assert hr.power_stats["watts"] == 3245

    def test_pool_fields_present(self):
        hr = HashrateData.from_luxos(_l("summary.json"), _l("pools.json"), _l("power.json"))
        pool = hr.pool_stats["pools"][0]
        assert pool["url"] == "stratum+tcp://pool.example.com:3333"
        assert pool["accepted"] == 58432
        assert pool["rejected"] == 15

    def test_miner_stats_fields(self):
        hr = HashrateData.from_luxos(_l("summary.json"), _l("pools.json"), _l("power.json"))
        assert hr.miner_stats["hardware_errors"] == 12
        assert hr.miner_stats["ghs_av"] == pytest.approx(145100.0)

    def test_empty_summary(self):
        hr = HashrateData.from_luxos({"SUMMARY": []}, {"POOLS": []}, {"POWER": []})
        assert hr.miner_stats["ghs_5s"] == 0
        assert hr.power_stats["watts"] == 0


class TestHashrateDataFromVnish:

    def test_full_data(self):
        hr = HashrateData.from_vnish(_v("summary.json"))
        assert hr.miner_stats["ghs_5s"] == pytest.approx(145230.5)
        assert hr.power_stats["watts"] == 3245

    def test_pool_fields_present(self):
        hr = HashrateData.from_vnish(_v("summary.json"))
        pool = hr.pool_stats["pools"][0]
        assert pool["url"] == "stratum+tcp://pool.example.com:3333"
        assert pool["accepted"] == 58432

    def test_empty_sections(self):
        hr = HashrateData.from_vnish({})
        assert hr.miner_stats["ghs_5s"] == 0
        assert hr.pool_stats == {"pools": []}
        assert hr.power_stats["watts"] == 0


# ---------------------------------------------------------------
# UptimeData
# ---------------------------------------------------------------

class TestUptimeDataFromBraiins:

    def test_full_data(self):
        ud = UptimeData.from_braiins(_b("miner_details.json"))
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


class TestUptimeDataFromLuxos:

    def test_full_data(self):
        ud = UptimeData.from_luxos(_l("summary.json"), _l("version.json"), _l("config.json"))
        assert ud.bosminer_uptime_s == 1728000
        assert ud.system_uptime_s == 1728000
        assert ud.hostname == "luxminer-rack5-slot2"
        assert ud.bos_version["luxminer"] == "2024.3.12.120000"

    def test_empty_responses(self):
        ud = UptimeData.from_luxos({"SUMMARY": []}, {"VERSION": []}, {"CONFIG": []})
        assert ud.bosminer_uptime_s == 0
        assert ud.hostname == ""


class TestUptimeDataFromVnish:

    def test_full_data(self):
        ud = UptimeData.from_vnish(_v("info.json"), _v("summary.json"))
        assert ud.bosminer_uptime_s == 1728000
        assert ud.system_uptime_s == 1728000
        assert ud.hostname == "vnish-rack2-slot5"
        assert ud.bos_version["vnish"] == "1.2.6"
        assert ud.bos_version["model"] == "Antminer S19j Pro+"

    def test_empty_responses(self):
        ud = UptimeData.from_vnish({}, {})
        assert ud.bosminer_uptime_s == 0
        assert ud.hostname == ""


# ---------------------------------------------------------------
# HashboardData
# ---------------------------------------------------------------

class TestHashboardDataFromBraiins:

    def test_full_data(self):
        hbd = HashboardData.from_braiins(_b("hashboards.json"))
        assert len(hbd.hashboards) == 3

    def test_board_fields(self):
        hbd = HashboardData.from_braiins(_b("hashboards.json"))
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


class TestHashboardDataFromLuxos:

    def test_board_count(self):
        hbd = HashboardData.from_luxos(_l("devs.json"), _l("temps.json"))
        assert len(hbd.hashboards) == 3

    def test_board_fields(self):
        hbd = HashboardData.from_luxos(_l("devs.json"), _l("temps.json"))
        b = hbd.hashboards[0]
        assert b.board_name == "Hashboard 0"
        assert b.enabled is True
        assert b.stats["serial_number"] == "HB0-LX91BX-A"
        assert b.stats["hardware_errors"] == 4

    def test_board_temp_from_dev(self):
        hbd = HashboardData.from_luxos(_l("devs.json"), _l("temps.json"))
        b = hbd.hashboards[0]
        assert b.board_temp == {"value": 58.0, "unit": "C"}

    def test_empty_devs(self):
        hbd = HashboardData.from_luxos({"DEVS": []}, {"TEMPS": []})
        assert hbd.hashboards == []


class TestHashboardDataFromVnish:

    def test_board_count(self):
        hbd = HashboardData.from_vnish(_v("status.json"))
        assert len(hbd.hashboards) == 3

    def test_board_fields(self):
        hbd = HashboardData.from_vnish(_v("status.json"))
        b = hbd.hashboards[0]
        assert b.board_name == "Chain 0"
        assert b.chips_count == 114
        assert b.enabled is True
        assert b.stats["serial_number"] == "HB0-VN91CX-A"

    def test_board_temp(self):
        hbd = HashboardData.from_vnish(_v("status.json"))
        assert hbd.hashboards[0].board_temp == {"value": 58.0, "unit": "C"}
        assert hbd.hashboards[0].highest_chip_temp == {"value": 72.5, "unit": "C"}

    def test_empty_chains(self):
        hbd = HashboardData.from_vnish({"chains": []})
        assert hbd.hashboards == []


# ---------------------------------------------------------------
# ErrorData
# ---------------------------------------------------------------

class TestErrorDataFromBraiins:

    def test_full_data(self):
        ed = ErrorData.from_braiins(_b("miner_errors.json"))
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


class TestErrorDataFromLuxos:

    def test_full_data(self):
        ed = ErrorData.from_luxos(_l("events.json"))
        assert len(ed.errors) == 2

    def test_first_error_fields(self):
        ed = ErrorData.from_luxos(_l("events.json"))
        e = ed.errors[0]
        assert e.message == "Hashboard 1 temperature exceeds warning threshold"
        assert e.timestamp == "2024-03-15T10:23:45Z"
        assert e.error_codes[0]["code"] == "TEMP_WARNING"
        assert e.components[0]["target"] == "hashboard"

    def test_second_error_code(self):
        ed = ErrorData.from_luxos(_l("events.json"))
        assert ed.errors[1].error_codes[0]["code"] == "FAN_RPM_LOW"

    def test_empty_events(self):
        ed = ErrorData.from_luxos({"EVENTS": []})
        assert ed.errors == []


class TestErrorDataFromVnish:

    def test_full_data(self):
        ed = ErrorData.from_vnish(_v("status.json"))
        assert len(ed.errors) == 2

    def test_first_error_fields(self):
        ed = ErrorData.from_vnish(_v("status.json"))
        e = ed.errors[0]
        assert e.message == "Chain 1 temperature exceeds warning threshold"
        assert e.timestamp == "2024-03-15T10:23:45Z"
        assert e.error_codes[0]["code"] == "TEMP_WARNING"
        assert e.components[0]["type"] == "hashboard"

    def test_second_error_code(self):
        ed = ErrorData.from_vnish(_v("status.json"))
        assert ed.errors[1].error_codes[0]["code"] == "FAN_RPM_LOW"

    def test_empty_errors(self):
        ed = ErrorData.from_vnish({"errors": []})
        assert ed.errors == []


# ---------------------------------------------------------------
# Cross-OS normalization
#
# These tests assert that all three firmware factories produce the
# same normalised shape and values when given fixtures that represent
# the same physical miner state.  They catch drift if a new firmware
# adapter maps a field differently from the others.
# ---------------------------------------------------------------

class TestCrossOsCoolingNormalization:
    """Fan count, positions, RPMs and speed ratios must be identical across firmware."""

    @pytest.fixture()
    def all_cooling(self):
        return {
            "braiins": CoolingData.from_braiins(_b("cooling_state.json")),
            "luxos":   CoolingData.from_luxos(_l("fans.json"), _l("temps.json")),
            "vnish":   CoolingData.from_vnish(_v("status.json")),
        }

    def test_fan_count(self, all_cooling):
        for fw, cd in all_cooling.items():
            assert len(cd.fans) == 4, f"{fw}: expected 4 fans"

    def test_fan_rpms(self, all_cooling):
        expected_rpms = [4200, 4150, 4180, 4210]
        for fw, cd in all_cooling.items():
            actual = [f.rpm for f in cd.fans]
            assert actual == expected_rpms, f"{fw}: RPMs mismatch"

    def test_fan_positions(self, all_cooling):
        for fw, cd in all_cooling.items():
            positions = [f.position for f in cd.fans]
            assert positions == [0, 1, 2, 3], f"{fw}: positions mismatch"

    def test_fan_speed_ratio(self, all_cooling):
        for fw, cd in all_cooling.items():
            for fan in cd.fans:
                assert fan.target_speed_ratio == pytest.approx(0.65), (
                    f"{fw}: speed ratio mismatch on fan {fan.position}"
                )

    def test_highest_temperature_present(self, all_cooling):
        for fw, cd in all_cooling.items():
            assert cd.highest_temperature is not None, f"{fw}: missing highest_temperature"
            assert cd.highest_temperature["unit"] == "C", f"{fw}: temp unit mismatch"


class TestCrossOsHashrateNormalization:
    """Hashrate, pool stats, and power must be identical across firmware."""

    @pytest.fixture()
    def all_hashrate(self):
        return {
            "braiins": HashrateData.from_braiins(_b("miner_stats.json")),
            "luxos":   HashrateData.from_luxos(_l("summary.json"), _l("pools.json"), _l("power.json")),
            "vnish":   HashrateData.from_vnish(_v("summary.json")),
        }

    def test_ghs_5s(self, all_hashrate):
        for fw, hr in all_hashrate.items():
            assert hr.miner_stats["ghs_5s"] == pytest.approx(145230.5), f"{fw}: ghs_5s mismatch"

    def test_power_watts(self, all_hashrate):
        for fw, hr in all_hashrate.items():
            assert hr.power_stats["watts"] == 3245, f"{fw}: watts mismatch"

    def test_pool_count(self, all_hashrate):
        for fw, hr in all_hashrate.items():
            assert len(hr.pool_stats["pools"]) == 1, f"{fw}: pool count mismatch"

    def test_pool_url(self, all_hashrate):
        expected = "stratum+tcp://pool.example.com:3333"
        for fw, hr in all_hashrate.items():
            assert hr.pool_stats["pools"][0]["url"] == expected, f"{fw}: pool URL mismatch"

    def test_pool_accepted(self, all_hashrate):
        for fw, hr in all_hashrate.items():
            assert hr.pool_stats["pools"][0]["accepted"] == 58432, f"{fw}: accepted mismatch"


class TestCrossOsUptimeNormalization:
    """Uptime seconds and hostname shape must be consistent across firmware."""

    @pytest.fixture()
    def all_uptime(self):
        return {
            "braiins": UptimeData.from_braiins(_b("miner_details.json")),
            "luxos":   UptimeData.from_luxos(_l("summary.json"), _l("version.json"), _l("config.json")),
            "vnish":   UptimeData.from_vnish(_v("info.json"), _v("summary.json")),
        }

    def test_bosminer_uptime_s(self, all_uptime):
        for fw, ud in all_uptime.items():
            assert ud.bosminer_uptime_s == 1728000, f"{fw}: bosminer_uptime_s mismatch"

    def test_hostname_non_empty(self, all_uptime):
        for fw, ud in all_uptime.items():
            assert ud.hostname, f"{fw}: hostname is empty"

    def test_bos_version_is_dict(self, all_uptime):
        for fw, ud in all_uptime.items():
            assert isinstance(ud.bos_version, dict), f"{fw}: bos_version is not a dict"
            assert ud.bos_version, f"{fw}: bos_version is empty"


class TestCrossOsHashboardNormalization:
    """Board count, enabled flags, and temperature fields must be consistent."""

    @pytest.fixture()
    def all_hashboards(self):
        return {
            "braiins": HashboardData.from_braiins(_b("hashboards.json")),
            "luxos":   HashboardData.from_luxos(_l("devs.json"), _l("temps.json")),
            "vnish":   HashboardData.from_vnish(_v("status.json")),
        }

    def test_board_count(self, all_hashboards):
        for fw, hbd in all_hashboards.items():
            assert len(hbd.hashboards) == 3, f"{fw}: board count mismatch"

    def test_all_boards_enabled(self, all_hashboards):
        for fw, hbd in all_hashboards.items():
            for b in hbd.hashboards:
                assert b.enabled is True, f"{fw}: board {b.id} not enabled"

    def test_board_temp_present(self, all_hashboards):
        for fw, hbd in all_hashboards.items():
            for b in hbd.hashboards:
                assert b.board_temp is not None, f"{fw}: board {b.id} missing board_temp"
                assert b.board_temp["unit"] == "C", f"{fw}: board {b.id} temp unit mismatch"

    def test_board_id_is_string(self, all_hashboards):
        for fw, hbd in all_hashboards.items():
            for b in hbd.hashboards:
                assert isinstance(b.id, str), f"{fw}: board id is not a string"

    def test_stats_has_serial_number(self, all_hashboards):
        for fw, hbd in all_hashboards.items():
            for b in hbd.hashboards:
                assert "serial_number" in b.stats, f"{fw}: board {b.id} missing serial_number in stats"


class TestCrossOsErrorNormalization:
    """Error entry shape must be consistent across firmware."""

    @pytest.fixture()
    def all_errors(self):
        return {
            "braiins": ErrorData.from_braiins(_b("miner_errors.json")),
            "luxos":   ErrorData.from_luxos(_l("events.json")),
            "vnish":   ErrorData.from_vnish(_v("status.json")),
        }

    def test_error_count(self, all_errors):
        for fw, ed in all_errors.items():
            assert len(ed.errors) == 2, f"{fw}: error count mismatch"

    def test_second_error_code_is_fan_rpm_low(self, all_errors):
        for fw, ed in all_errors.items():
            code = ed.errors[1].error_codes[0]["code"]
            assert code == "FAN_RPM_LOW", f"{fw}: second error code mismatch (got {code!r})"

    def test_error_entries_have_message_and_timestamp(self, all_errors):
        for fw, ed in all_errors.items():
            for e in ed.errors:
                assert e.message, f"{fw}: error entry missing message"
                assert e.timestamp, f"{fw}: error entry missing timestamp"

    def test_error_codes_is_list(self, all_errors):
        for fw, ed in all_errors.items():
            for e in ed.errors:
                assert isinstance(e.error_codes, list), f"{fw}: error_codes is not a list"
                assert e.error_codes, f"{fw}: error_codes is empty"

    def test_components_is_list(self, all_errors):
        for fw, ed in all_errors.items():
            for e in ed.errors:
                assert isinstance(e.components, list), f"{fw}: components is not a list"
                assert e.components, f"{fw}: components is empty"


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
        assert d["wright_fans"] is None

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
