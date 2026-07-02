"""Regression tests for SealminerCollector against simulated CGMiner TCP API."""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from wright_telemetry.models import HashboardData


class TestAuthentication:

    def test_authenticate_is_noop(self, mock_sealminer_api, sealminer_collector):
        sealminer_collector.authenticate()


class TestFetchIdentity:

    def test_fields_mapped(self, mock_sealminer_api, sealminer_collector):
        identity = sealminer_collector.fetch_identity()
        assert identity.uid == "H01X2P22307050006"
        assert identity.serial_number == "H01X2P22307050006"
        assert identity.mac_address == "32:26:ca:00:38:ea"
        assert identity.model == "K10Pro"
        assert identity.firmware == "sealminer"

    def test_hostname_empty(self, mock_sealminer_api, sealminer_collector):
        identity = sealminer_collector.fetch_identity()
        assert identity.hostname == ""

    def test_missing_stats_defaults_empty(self, sealminer_collector):
        with patch.object(sealminer_collector, "_send_command", return_value={"STATS": [{}]}):
            identity = sealminer_collector.fetch_identity()
            assert identity.uid == ""
            assert identity.mac_address == ""
            assert identity.model == ""

    def test_empty_stats_list(self, sealminer_collector):
        with patch.object(sealminer_collector, "_send_command", return_value={"STATS": []}):
            identity = sealminer_collector.fetch_identity()
            assert identity.uid == ""


class TestFetchCooling:

    def test_fans_parsed(self, mock_sealminer_api, sealminer_collector):
        cooling = sealminer_collector.fetch_cooling()
        assert len(cooling.fans) == 4

    def test_fan_rpm(self, mock_sealminer_api, sealminer_collector):
        cooling = sealminer_collector.fetch_cooling()
        assert cooling.fans[0].rpm == 6210
        assert cooling.fans[0].position == 0

    def test_fan_target_speed_ratio(self, mock_sealminer_api, sealminer_collector):
        cooling = sealminer_collector.fetch_cooling()
        # PWM 207 / 255 = 0.8118...
        assert abs(cooling.fans[0].target_speed_ratio - round(207 / 255.0, 4)) < 1e-4

    def test_highest_temperature_from_boards(self, mock_sealminer_api, sealminer_collector):
        cooling = sealminer_collector.fetch_cooling()
        # Board max temps are 62, 65, 67; PSU AMB is 38 — overall max is 67
        assert cooling.highest_temperature == {"value": 67.0, "unit": "C"}

    def test_no_fans(self, sealminer_collector):
        with patch.object(
            sealminer_collector, "_send_command",
            return_value={"STATS": [{"Fan Count": 0, "Board Count": 0}]},
        ):
            cooling = sealminer_collector.fetch_cooling()
            assert cooling.fans == []
            assert cooling.highest_temperature is None


class TestFetchHashrate:

    def test_miner_stats(self, mock_sealminer_api, sealminer_collector):
        hr = sealminer_collector.fetch_hashrate()
        assert abs(hr.miner_stats["ghs_5s"] - 22683116.68 / 1000) < 1
        assert abs(hr.miner_stats["ghs_av"] - 206852501.39 / 1000) < 1
        assert hr.miner_stats["hardware_errors"] == 1

    def test_nominal_ghs(self, mock_sealminer_api, sealminer_collector):
        hr = sealminer_collector.fetch_hashrate()
        assert abs(hr.miner_stats["nominal_ghs"] - 167349688 / 1000) < 1

    def test_pool_stats(self, mock_sealminer_api, sealminer_collector):
        hr = sealminer_collector.fetch_hashrate()
        assert hr.pool_stats["pools"][0]["url"] == "stratum+tcp://pool.example.com:3333"
        assert hr.pool_stats["pools"][0]["accepted"] == 58432
        assert hr.pool_stats["pools"][0]["status"] == "Alive"

    def test_power_stats(self, mock_sealminer_api, sealminer_collector):
        hr = sealminer_collector.fetch_hashrate()
        assert hr.power_stats["watts"] == 3998
        # efficiency is W/T(Avg) (watts-per-terahash / J-per-TH) per the bdminer
        # spec, not the 0-1 "PSU Efficiency" ratio (kept as psu_efficiency).
        assert abs(hr.power_stats["efficiency"] - 24.084337) < 1e-4
        assert abs(hr.power_stats["psu_efficiency"] - 0.936937) < 1e-4

    def test_empty_response_defaults(self, sealminer_collector):
        with patch.object(
            sealminer_collector, "_send_command",
            return_value={"SUMMARY": [{}], "POOLS": [], "STATS": [{}]},
        ):
            hr = sealminer_collector.fetch_hashrate()
            assert hr.miner_stats["ghs_5s"] == 0
            assert hr.pool_stats == {"pools": []}
            assert hr.power_stats["watts"] == 0


class TestFetchUptime:

    def test_miner_uptime(self, mock_sealminer_api, sealminer_collector):
        uptime = sealminer_collector.fetch_uptime()
        assert uptime.bosminer_uptime_s == 27784

    def test_system_uptime(self, mock_sealminer_api, sealminer_collector):
        uptime = sealminer_collector.fetch_uptime()
        assert uptime.system_uptime_s == 27942

    def test_hostname_empty(self, mock_sealminer_api, sealminer_collector):
        uptime = sealminer_collector.fetch_uptime()
        assert uptime.hostname == ""

    def test_version_fields(self, mock_sealminer_api, sealminer_collector):
        uptime = sealminer_collector.fetch_uptime()
        assert uptime.bos_version["firmware"] == "20230805FF"
        assert uptime.bos_version["software_version"] == "4.11.1-08400-g27d4664c"
        assert uptime.bos_version["mining_mode"] == "Normal"
        assert uptime.bos_version["pm_state"] == "Running"

    def test_empty_response_defaults(self, sealminer_collector):
        with patch.object(sealminer_collector, "_send_command", return_value={"SUMMARY": [{}], "STATS": [{}]}):
            uptime = sealminer_collector.fetch_uptime()
            assert uptime.bosminer_uptime_s == 0
            assert uptime.system_uptime_s == 0
            assert uptime.bos_version["firmware"] == ""


class TestFetchHashboards:

    def test_boards_parsed(self, mock_sealminer_api, sealminer_collector):
        hb = sealminer_collector.fetch_hashboards()
        assert len(hb.hashboards) == 3

    def test_board_fields(self, mock_sealminer_api, sealminer_collector):
        hb = sealminer_collector.fetch_hashboards()
        board = hb.hashboards[0]
        assert board.id == "0"
        assert board.enabled is True
        assert board.chips_count == 364
        assert board.board_temp == {"value": 62.0, "unit": "C"}
        # bdminer's per-board sensors are chip temps; highest_chip_temp must be
        # populated (not None) so the pipeline's thermal MV picks them up.
        assert board.highest_chip_temp == {"value": 62.0, "unit": "C"}

    def test_board_highest_chip_temp_none_without_sensors(self, sealminer_collector):
        stats = {"STATS": [{"Board Count": 1, "0 Online": True}]}
        hb = HashboardData.from_sealminer(stats)
        assert hb.hashboards[0].highest_chip_temp is None
        assert hb.hashboards[0].board_temp is None

    def test_board_stats(self, mock_sealminer_api, sealminer_collector):
        hb = sealminer_collector.fetch_hashboards()
        board = hb.hashboards[0]
        assert board.stats["serial_number"] == "H0152730628KPA526T2BV00610011"
        assert board.stats["hardware_errors"] == 0
        assert board.stats["low_hash"] is False

    def test_board_freq(self, mock_sealminer_api, sealminer_collector):
        hb = sealminer_collector.fetch_hashboards()
        assert hb.hashboards[0].freq_mhz == 220.0

    def test_no_boards(self, sealminer_collector):
        with patch.object(
            sealminer_collector, "_send_command",
            return_value={"STATS": [{"Board Count": 0}]},
        ):
            hb = sealminer_collector.fetch_hashboards()
            assert hb.hashboards == []


class TestFetchErrors:

    def test_no_errors_when_empty(self, mock_sealminer_api, sealminer_collector):
        # Fixture has empty Error Chip and Error Code
        errs = sealminer_collector.fetch_errors()
        assert errs.errors == []

    def test_error_chip_creates_entry(self, sealminer_collector):
        with patch.object(
            sealminer_collector, "_send_command",
            return_value={"STATS": [{"Error Chip": "chip_addr_0x0A", "Error Code": ""}]},
        ):
            errs = sealminer_collector.fetch_errors()
            assert len(errs.errors) == 1
            assert "chip_addr_0x0A" in errs.errors[0].message
            assert errs.errors[0].components[0]["chips"] == "chip_addr_0x0A"

    def test_error_code_with_bad_chips_creates_entry(self, sealminer_collector):
        # Error code alone (e.g. 602 on a healthy machine) should not fire;
        # it must be accompanied by a real hardware indicator.
        with patch.object(
            sealminer_collector, "_send_command",
            return_value={"STATS": [{"Error Chip": "", "Error Code": "E001", "Bad Chip Count": 1, "Board Count": 0}]},
        ):
            errs = sealminer_collector.fetch_errors()
            assert len(errs.errors) == 1
            assert errs.errors[0].error_codes[0]["code"] == "E001"

    def test_error_code_alone_no_entry(self, sealminer_collector):
        # Bare error code with no chip/HW errors (e.g. status code 602) must not create a false entry.
        with patch.object(
            sealminer_collector, "_send_command",
            return_value={"STATS": [{"Error Chip": "", "Error Code": "602", "Bad Chip Count": 0, "Board Count": 0}]},
        ):
            errs = sealminer_collector.fetch_errors()
            assert len(errs.errors) == 0

    def test_both_chip_and_code_combined_in_message(self, sealminer_collector):
        with patch.object(
            sealminer_collector, "_send_command",
            return_value={"STATS": [{"Error Chip": "chip_addr_0x0A", "Error Code": "E001"}]},
        ):
            errs = sealminer_collector.fetch_errors()
            assert len(errs.errors) == 1
            msg = errs.errors[0].message
            assert "chip_addr_0x0A" in msg
            assert "E001" in msg
            assert errs.errors[0].components[0]["chips"] == "chip_addr_0x0A"
            assert errs.errors[0].error_codes[0]["code"] == "E001"

    def test_missing_error_fields(self, sealminer_collector):
        with patch.object(
            sealminer_collector, "_send_command",
            return_value={"STATS": [{}]},
        ):
            errs = sealminer_collector.fetch_errors()
            assert errs.errors == []


class TestSocketErrors:

    def test_socket_timeout(self, sealminer_collector):
        with patch.object(
            sealminer_collector, "_send_command", side_effect=socket.timeout("timed out")
        ):
            with pytest.raises(socket.timeout):
                sealminer_collector.fetch_identity()

    def test_connection_refused(self, sealminer_collector):
        with patch.object(
            sealminer_collector, "_send_command", side_effect=ConnectionRefusedError("refused")
        ):
            with pytest.raises(ConnectionRefusedError):
                sealminer_collector.fetch_cooling()

    def test_send_command_socket_error(self):
        """Verify _send_command surfaces socket.error from a real connection failure."""
        from wright_telemetry.collectors.sealminer import SealminerCollector
        collector = SealminerCollector(url="127.0.0.1")
        collector._port = 1  # unbound — gives immediate ECONNREFUSED
        with pytest.raises(socket.error):
            collector._send_command("stats")


class TestFactoryRegistration:

    def test_sealminer_registered(self):
        from wright_telemetry.collectors.factory import CollectorFactory
        import wright_telemetry.collectors.sealminer  # noqa: F401
        assert "sealminer" in CollectorFactory.available()

    def test_factory_creates_instance(self):
        from wright_telemetry.collectors.factory import CollectorFactory
        import wright_telemetry.collectors.sealminer  # noqa: F401
        collector = CollectorFactory.create("sealminer", "192.168.1.210")
        from wright_telemetry.collectors.sealminer import SealminerCollector
        assert isinstance(collector, SealminerCollector)
