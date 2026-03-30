"""Full regression tests for BraiinsCollector against the simulated API."""

from __future__ import annotations

import responses
import pytest

from tests.conftest import MINER_URL


class TestAuthentication:

    def test_authenticate_stores_token(self, mock_braiins_api, braiins_collector, braiins_fixtures):
        braiins_collector.authenticate()
        expected_token = braiins_fixtures["auth_login"]["token"]
        assert braiins_collector._token == expected_token
        assert braiins_collector._session.headers.get("authorization") == expected_token

    def test_authenticate_no_credentials_skips(self, mock_braiins_api, braiins_collector_no_auth):
        braiins_collector_no_auth.authenticate()
        assert braiins_collector_no_auth._token is None
        assert "authorization" not in braiins_collector_no_auth._session.headers

    @responses.activate
    def test_authenticate_http_error_no_crash(self, braiins_collector):
        responses.add(
            responses.POST,
            f"{MINER_URL}/api/v1/auth/login",
            json={"error": "unauthorized"},
            status=403,
        )
        braiins_collector.authenticate()
        assert braiins_collector._token is None

    @responses.activate
    def test_authenticate_missing_token_field(self, braiins_collector):
        responses.add(
            responses.POST,
            f"{MINER_URL}/api/v1/auth/login",
            json={"timeout_s": 3600},
            status=200,
        )
        braiins_collector.authenticate()
        assert braiins_collector._token is None

    @responses.activate
    def test_auto_reauth_on_401(self, braiins_collector, braiins_fixtures):
        responses.add(
            responses.GET,
            f"{MINER_URL}/api/v1/miner/details",
            json={"error": "unauthorized"},
            status=401,
        )
        responses.add(
            responses.POST,
            f"{MINER_URL}/api/v1/auth/login",
            json=braiins_fixtures["auth_login"],
            status=200,
        )
        responses.add(
            responses.GET,
            f"{MINER_URL}/api/v1/miner/details",
            json=braiins_fixtures["miner_details"],
            status=200,
        )
        identity = braiins_collector.fetch_identity()
        assert identity.uid == "a1b2c3d4e5f6"
        assert len(responses.calls) == 3


class TestFetchIdentity:

    def test_fields_mapped(self, mock_braiins_api, braiins_collector):
        identity = braiins_collector.fetch_identity()
        assert identity.uid == "a1b2c3d4e5f6"
        assert identity.serial_number == "BHB42391AX0027"
        assert identity.hostname == "antminer-rack3-slot7"
        assert identity.mac_address == "AA:BB:CC:DD:EE:F1"

    @responses.activate
    def test_missing_fields_default_empty(self, braiins_collector):
        responses.add(
            responses.GET,
            f"{MINER_URL}/api/v1/miner/details",
            json={},
            status=200,
        )
        identity = braiins_collector.fetch_identity()
        assert identity.uid == ""
        assert identity.serial_number == ""
        assert identity.hostname == ""
        assert identity.mac_address == ""


class TestFetchCooling:

    def test_fans_parsed(self, mock_braiins_api, braiins_collector):
        cooling = braiins_collector.fetch_cooling()
        assert len(cooling.fans) == 4
        assert cooling.fans[0].position == 0
        assert cooling.fans[0].rpm == 4200
        assert cooling.fans[0].target_speed_ratio == 0.65

    def test_highest_temperature(self, mock_braiins_api, braiins_collector):
        cooling = braiins_collector.fetch_cooling()
        assert cooling.highest_temperature == {"value": 72.5, "unit": "C"}

    @responses.activate
    def test_empty_fans_list(self, braiins_collector):
        responses.add(
            responses.GET,
            f"{MINER_URL}/api/v1/cooling/state",
            json={"fans": []},
            status=200,
        )
        cooling = braiins_collector.fetch_cooling()
        assert cooling.fans == []
        assert cooling.highest_temperature is None


class TestFetchHashrate:

    def test_stats_sections_present(self, mock_braiins_api, braiins_collector):
        hr = braiins_collector.fetch_hashrate()
        assert hr.miner_stats["ghs_5s"] == 145230.5
        assert hr.pool_stats["pools"][0]["url"] == "stratum+tcp://pool.example.com:3333"
        assert hr.power_stats["watts"] == 3245

    @responses.activate
    def test_empty_response_defaults(self, braiins_collector):
        responses.add(
            responses.GET,
            f"{MINER_URL}/api/v1/miner/stats",
            json={},
            status=200,
        )
        hr = braiins_collector.fetch_hashrate()
        assert hr.miner_stats == {}
        assert hr.pool_stats == {}
        assert hr.power_stats == {}


class TestFetchUptime:

    def test_fields_mapped(self, mock_braiins_api, braiins_collector):
        uptime = braiins_collector.fetch_uptime()
        assert uptime.bosminer_uptime_s == 1728000
        assert uptime.system_uptime_s == 1729200
        assert uptime.hostname == "antminer-rack3-slot7"
        assert uptime.bos_version["full"] == "24.3.1-20240315-134500"
        assert uptime.platform == 1
        assert uptime.status == 1

    @responses.activate
    def test_empty_response_defaults(self, braiins_collector):
        responses.add(
            responses.GET,
            f"{MINER_URL}/api/v1/miner/details",
            json={},
            status=200,
        )
        uptime = braiins_collector.fetch_uptime()
        assert uptime.bosminer_uptime_s == 0
        assert uptime.hostname == ""
        assert uptime.bos_version == {}


class TestFetchHashboards:

    def test_boards_parsed(self, mock_braiins_api, braiins_collector):
        hb = braiins_collector.fetch_hashboards()
        assert len(hb.hashboards) == 3

    def test_board_fields(self, mock_braiins_api, braiins_collector):
        hb = braiins_collector.fetch_hashboards()
        board = hb.hashboards[0]
        assert board.board_name == "Hashboard 0"
        assert board.board_temp == {"value": 58.0, "unit": "C"}
        assert board.highest_chip_temp == {"value": 72.5, "unit": "C"}
        assert board.chips_count == 114
        assert board.id == "0"
        assert board.enabled is True
        assert board.stats["ghs_5s"] == 48410.2

    @responses.activate
    def test_empty_hashboards(self, braiins_collector):
        responses.add(
            responses.GET,
            f"{MINER_URL}/api/v1/miner/hw/hashboards",
            json={"hashboards": []},
            status=200,
        )
        hb = braiins_collector.fetch_hashboards()
        assert hb.hashboards == []


class TestFetchErrors:

    def test_errors_parsed(self, mock_braiins_api, braiins_collector):
        errs = braiins_collector.fetch_errors()
        assert len(errs.errors) == 2

    def test_error_entry_fields(self, mock_braiins_api, braiins_collector):
        errs = braiins_collector.fetch_errors()
        entry = errs.errors[0]
        assert "temperature exceeds" in entry.message
        assert entry.timestamp == "2024-03-15T10:23:45Z"
        assert entry.error_codes[0]["code"] == "TEMP_WARNING"
        assert entry.components[0]["type"] == "hashboard"

    @responses.activate
    def test_no_errors(self, braiins_collector):
        responses.add(
            responses.GET,
            f"{MINER_URL}/api/v1/miner/errors",
            json={"errors": []},
            status=200,
        )
        errs = braiins_collector.fetch_errors()
        assert errs.errors == []


class TestHTTPErrors:

    @responses.activate
    def test_fetch_raises_on_500(self, braiins_collector):
        responses.add(
            responses.GET,
            f"{MINER_URL}/api/v1/cooling/state",
            json={"error": "internal"},
            status=500,
        )
        with pytest.raises(Exception):
            braiins_collector.fetch_cooling()

    @responses.activate
    def test_connection_error(self, braiins_collector):
        responses.add(
            responses.GET,
            f"{MINER_URL}/api/v1/cooling/state",
            body=ConnectionError("refused"),
        )
        with pytest.raises(ConnectionError):
            braiins_collector.fetch_cooling()
