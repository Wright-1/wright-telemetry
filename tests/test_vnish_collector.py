"""Full regression tests for VnishCollector against the simulated API."""

from __future__ import annotations

import responses
import pytest

from tests.conftest import VNISH_URL


class TestAuthentication:

    def test_authenticate_stores_token(self, mock_vnish_api, vnish_collector, vnish_fixtures):
        vnish_collector.authenticate()
        expected_token = vnish_fixtures["unlock"]["token"]
        assert vnish_collector._token == expected_token
        assert vnish_collector._session.headers.get("Authorization") == expected_token

    def test_authenticate_no_password_skips(self, mock_vnish_api, vnish_collector_no_auth):
        vnish_collector_no_auth.authenticate()
        assert vnish_collector_no_auth._token is None
        assert "Authorization" not in vnish_collector_no_auth._session.headers

    @responses.activate
    def test_authenticate_http_error_no_crash(self, vnish_collector):
        responses.add(
            responses.POST,
            f"{VNISH_URL}/api/v1/unlock",
            json={"error": "unauthorized"},
            status=403,
        )
        vnish_collector.authenticate()
        assert vnish_collector._token is None

    @responses.activate
    def test_authenticate_missing_token_field(self, vnish_collector):
        responses.add(
            responses.POST,
            f"{VNISH_URL}/api/v1/unlock",
            json={"status": "ok"},
            status=200,
        )
        vnish_collector.authenticate()
        assert vnish_collector._token is None

    @responses.activate
    def test_auto_reauth_on_401(self, vnish_collector, vnish_fixtures):
        responses.add(
            responses.GET,
            f"{VNISH_URL}/api/v1/info",
            json={"error": "unauthorized"},
            status=401,
        )
        responses.add(
            responses.POST,
            f"{VNISH_URL}/api/v1/unlock",
            json=vnish_fixtures["unlock"],
            status=200,
        )
        responses.add(
            responses.GET,
            f"{VNISH_URL}/api/v1/info",
            json=vnish_fixtures["info"],
            status=200,
        )
        identity = vnish_collector.fetch_identity()
        assert identity.uid == "VN42391CX0044"
        assert len(responses.calls) == 3


class TestFetchIdentity:

    def test_fields_mapped(self, mock_vnish_api, vnish_collector):
        identity = vnish_collector.fetch_identity()
        assert identity.uid == "VN42391CX0044"
        assert identity.serial_number == "VN42391CX0044"
        assert identity.hostname == "vnish-rack2-slot5"
        assert identity.mac_address == "AA:BB:CC:11:22:33"

    @responses.activate
    def test_missing_fields_default_empty(self, vnish_collector):
        responses.add(
            responses.GET,
            f"{VNISH_URL}/api/v1/info",
            json={},
            status=200,
        )
        identity = vnish_collector.fetch_identity()
        assert identity.uid == ""
        assert identity.serial_number == ""
        assert identity.hostname == ""
        assert identity.mac_address == ""


class TestFetchCooling:

    def test_fans_parsed(self, mock_vnish_api, vnish_collector):
        cooling = vnish_collector.fetch_cooling()
        assert len(cooling.fans) == 4
        assert cooling.fans[0].position == 0
        assert cooling.fans[0].rpm == 4200
        assert cooling.fans[0].target_speed_ratio == 0.65

    def test_highest_temperature(self, mock_vnish_api, vnish_collector):
        cooling = vnish_collector.fetch_cooling()
        assert cooling.highest_temperature == {"value": 74.0, "unit": "C"}

    @responses.activate
    def test_empty_fans_list(self, vnish_collector):
        responses.add(
            responses.GET,
            f"{VNISH_URL}/api/v1/status",
            json={"fans": [], "chains": []},
            status=200,
        )
        cooling = vnish_collector.fetch_cooling()
        assert cooling.fans == []
        assert cooling.highest_temperature is None


class TestFetchHashrate:

    def test_miner_stats(self, mock_vnish_api, vnish_collector):
        hr = vnish_collector.fetch_hashrate()
        assert hr.miner_stats["ghs_5s"] == 145230.5
        assert hr.miner_stats["ghs_av"] == 145100.0
        assert hr.miner_stats["hardware_errors"] == 12

    def test_pool_stats(self, mock_vnish_api, vnish_collector):
        hr = vnish_collector.fetch_hashrate()
        assert hr.pool_stats["pools"][0]["url"] == "stratum+tcp://pool.example.com:3333"
        assert hr.pool_stats["pools"][0]["accepted"] == 58432

    def test_power_stats(self, mock_vnish_api, vnish_collector):
        hr = vnish_collector.fetch_hashrate()
        assert hr.power_stats["watts"] == 3245

    @responses.activate
    def test_empty_response_defaults(self, vnish_collector):
        responses.add(
            responses.GET,
            f"{VNISH_URL}/api/v1/summary",
            json={},
            status=200,
        )
        hr = vnish_collector.fetch_hashrate()
        assert hr.miner_stats["ghs_5s"] == 0
        assert hr.pool_stats == {"pools": []}
        assert hr.power_stats["watts"] == 0


class TestFetchUptime:

    def test_fields_mapped(self, mock_vnish_api, vnish_collector):
        uptime = vnish_collector.fetch_uptime()
        assert uptime.bosminer_uptime_s == 1728000
        assert uptime.system_uptime_s == 1728000
        assert uptime.hostname == "vnish-rack2-slot5"
        assert uptime.bos_version["vnish"] == "1.2.6"
        assert uptime.bos_version["model"] == "Antminer S19j Pro+"

    @responses.activate
    def test_empty_response_defaults(self, vnish_collector):
        responses.add(
            responses.GET,
            f"{VNISH_URL}/api/v1/info",
            json={},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{VNISH_URL}/api/v1/summary",
            json={},
            status=200,
        )
        uptime = vnish_collector.fetch_uptime()
        assert uptime.bosminer_uptime_s == 0
        assert uptime.hostname == ""
        assert uptime.bos_version == {"vnish": "", "model": ""}


class TestFetchHashboards:

    def test_boards_parsed(self, mock_vnish_api, vnish_collector):
        hb = vnish_collector.fetch_hashboards()
        assert len(hb.hashboards) == 3

    def test_board_fields(self, mock_vnish_api, vnish_collector):
        hb = vnish_collector.fetch_hashboards()
        board = hb.hashboards[0]
        assert board.board_name == "Chain 0"
        assert board.board_temp == {"value": 58.0, "unit": "C"}
        assert board.highest_chip_temp == {"value": 72.5, "unit": "C"}
        assert board.chips_count == 114
        assert board.id == "0"
        assert board.enabled is True
        assert board.stats["hashrate"] == 48410.2
        assert board.stats["serial_number"] == "HB0-VN91CX-A"

    @responses.activate
    def test_empty_hashboards(self, vnish_collector):
        responses.add(
            responses.GET,
            f"{VNISH_URL}/api/v1/status",
            json={"chains": [], "fans": []},
            status=200,
        )
        hb = vnish_collector.fetch_hashboards()
        assert hb.hashboards == []


class TestFetchErrors:

    def test_errors_parsed(self, mock_vnish_api, vnish_collector):
        errs = vnish_collector.fetch_errors()
        assert len(errs.errors) == 2

    def test_error_entry_fields(self, mock_vnish_api, vnish_collector):
        errs = vnish_collector.fetch_errors()
        entry = errs.errors[0]
        assert "temperature exceeds" in entry.message
        assert entry.timestamp == "2024-03-15T10:23:45Z"
        assert entry.error_codes[0]["code"] == "TEMP_WARNING"
        assert entry.components[0]["type"] == "hashboard"

    @responses.activate
    def test_no_errors(self, vnish_collector):
        responses.add(
            responses.GET,
            f"{VNISH_URL}/api/v1/status",
            json={"errors": [], "fans": [], "chains": []},
            status=200,
        )
        errs = vnish_collector.fetch_errors()
        assert errs.errors == []


class TestHTTPErrors:

    @responses.activate
    def test_fetch_raises_on_500(self, vnish_collector):
        responses.add(
            responses.GET,
            f"{VNISH_URL}/api/v1/status",
            json={"error": "internal"},
            status=500,
        )
        with pytest.raises(Exception):
            vnish_collector.fetch_cooling()

    @responses.activate
    def test_connection_error(self, vnish_collector):
        responses.add(
            responses.GET,
            f"{VNISH_URL}/api/v1/status",
            body=ConnectionError("refused"),
        )
        with pytest.raises(ConnectionError):
            vnish_collector.fetch_cooling()
