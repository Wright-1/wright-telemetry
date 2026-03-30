"""Full regression tests for LuxOSCollector against simulated CGMiner TCP API."""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest


class TestAuthentication:

    def test_authenticate_is_noop(self, mock_luxos_api, luxos_collector):
        luxos_collector.authenticate()


class TestFetchIdentity:

    def test_fields_mapped(self, mock_luxos_api, luxos_collector):
        identity = luxos_collector.fetch_identity()
        assert identity.uid == "LX42391BX0089"
        assert identity.serial_number == "LX42391BX0089"
        assert identity.hostname == "luxminer-rack5-slot2"
        assert identity.mac_address == "11:22:33:44:55:66"

    def test_missing_config_defaults_empty(self, luxos_collector):
        with patch.object(luxos_collector, "_send_command", return_value={"CONFIG": [{}]}):
            identity = luxos_collector.fetch_identity()
            assert identity.uid == ""
            assert identity.serial_number == ""
            assert identity.hostname == ""
            assert identity.mac_address == ""

    def test_empty_config_list(self, luxos_collector):
        with patch.object(luxos_collector, "_send_command", return_value={"CONFIG": []}):
            identity = luxos_collector.fetch_identity()
            assert identity.uid == ""


class TestFetchCooling:

    def test_fans_parsed(self, mock_luxos_api, luxos_collector):
        cooling = luxos_collector.fetch_cooling()
        assert len(cooling.fans) == 4
        assert cooling.fans[0].position == 0
        assert cooling.fans[0].rpm == 4200
        assert cooling.fans[0].target_speed_ratio == 0.65

    def test_highest_temperature(self, mock_luxos_api, luxos_collector):
        cooling = luxos_collector.fetch_cooling()
        assert cooling.highest_temperature == {"value": 74.0, "unit": "C"}

    def test_empty_fans_list(self, luxos_collector):
        def _empty(self, cmd, param=""):
            return {"FANS": [], "TEMPS": []}

        with patch(
            "wright_telemetry.collectors.luxos.LuxOSCollector._send_command",
            _empty,
        ):
            cooling = luxos_collector.fetch_cooling()
            assert cooling.fans == []
            assert cooling.highest_temperature is None


class TestFetchHashrate:

    def test_miner_stats(self, mock_luxos_api, luxos_collector):
        hr = luxos_collector.fetch_hashrate()
        assert hr.miner_stats["ghs_5s"] == 145230.5
        assert hr.miner_stats["ghs_30m"] == 144980.2
        assert hr.miner_stats["hardware_errors"] == 12

    def test_pool_stats(self, mock_luxos_api, luxos_collector):
        hr = luxos_collector.fetch_hashrate()
        assert hr.pool_stats["pools"][0]["url"] == "stratum+tcp://pool.example.com:3333"
        assert hr.pool_stats["pools"][0]["accepted"] == 58432

    def test_power_stats(self, mock_luxos_api, luxos_collector):
        hr = luxos_collector.fetch_hashrate()
        assert hr.power_stats["watts"] == 3245

    def test_empty_response_defaults(self, luxos_collector):
        with patch.object(
            luxos_collector, "_send_command", return_value={"SUMMARY": [{}], "POOLS": [], "POWER": [{}]}
        ):
            hr = luxos_collector.fetch_hashrate()
            assert hr.miner_stats["ghs_5s"] == 0
            assert hr.pool_stats == {"pools": []}
            assert hr.power_stats["watts"] == 0


class TestFetchUptime:

    def test_fields_mapped(self, mock_luxos_api, luxos_collector):
        uptime = luxos_collector.fetch_uptime()
        assert uptime.bosminer_uptime_s == 1728000
        assert uptime.system_uptime_s == 1728000
        assert uptime.hostname == "luxminer-rack5-slot2"
        assert uptime.bos_version["luxminer"] == "2024.3.12.120000"
        assert uptime.bos_version["api"] == "4.0"

    def test_empty_response_defaults(self, luxos_collector):
        empty = {"SUMMARY": [{}], "VERSION": [{}], "CONFIG": [{}]}
        with patch.object(luxos_collector, "_send_command", return_value=empty):
            uptime = luxos_collector.fetch_uptime()
            assert uptime.bosminer_uptime_s == 0
            assert uptime.hostname == ""
            assert uptime.bos_version == {"luxminer": "", "api": "", "type": ""}


class TestFetchHashboards:

    def test_boards_parsed(self, mock_luxos_api, luxos_collector):
        hb = luxos_collector.fetch_hashboards()
        assert len(hb.hashboards) == 3

    def test_board_fields(self, mock_luxos_api, luxos_collector):
        hb = luxos_collector.fetch_hashboards()
        board = hb.hashboards[0]
        assert board.board_name == "Hashboard 0"
        assert board.board_temp == {"value": 58.0, "unit": "C"}
        assert board.id == "0"
        assert board.enabled is True
        assert board.stats["mhs_av"] == 48410200.0
        assert board.stats["serial_number"] == "HB0-LX91BX-A"

    def test_chip_temp_from_temps(self, mock_luxos_api, luxos_collector):
        hb = luxos_collector.fetch_hashboards()
        board = hb.hashboards[0]
        assert board.highest_chip_temp == {"value": 72.5, "unit": "C"}

    def test_empty_hashboards(self, luxos_collector):
        with patch.object(
            luxos_collector, "_send_command", return_value={"DEVS": [], "TEMPS": []}
        ):
            hb = luxos_collector.fetch_hashboards()
            assert hb.hashboards == []


class TestFetchErrors:

    def test_errors_parsed(self, mock_luxos_api, luxos_collector):
        errs = luxos_collector.fetch_errors()
        assert len(errs.errors) == 2

    def test_error_entry_fields(self, mock_luxos_api, luxos_collector):
        errs = luxos_collector.fetch_errors()
        entry = errs.errors[0]
        assert "temperature exceeds" in entry.message
        assert entry.timestamp == "2024-03-15T10:23:45Z"
        assert entry.error_codes[0]["code"] == "TEMP_WARNING"
        assert entry.components[0]["target"] == "hashboard"

    def test_no_errors(self, luxos_collector):
        with patch.object(
            luxos_collector, "_send_command", return_value={"EVENTS": []}
        ):
            errs = luxos_collector.fetch_errors()
            assert errs.errors == []


class TestSocketErrors:

    def test_socket_timeout(self, luxos_collector):
        with patch.object(
            luxos_collector, "_send_command", side_effect=socket.timeout("timed out")
        ):
            with pytest.raises(socket.timeout):
                luxos_collector.fetch_identity()

    def test_connection_refused(self, luxos_collector):
        with patch.object(
            luxos_collector, "_send_command", side_effect=ConnectionRefusedError("refused")
        ):
            with pytest.raises(ConnectionRefusedError):
                luxos_collector.fetch_cooling()

    def test_send_command_socket_error(self):
        """Verify _send_command surfaces socket.error from a real connection failure."""
        from wright_telemetry.collectors.luxos import LuxOSCollector
        collector = LuxOSCollector(url="192.168.255.254")
        collector._port = 1  # unreachable port
        with pytest.raises(socket.error):
            collector._send_command("config")
