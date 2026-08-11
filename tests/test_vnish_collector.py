"""Full regression tests for VnishCollector against the simulated API.

Fixtures are captured from a live Antminer S21 on Vnish 1.2.6-rc5 --
see tests/fixtures/vnish/README.md.
"""

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
        assert identity.mac_address == "AA:BB:CC:11:22:33"
        assert len(responses.calls) == 3


class TestFetchIdentity:

    def test_fields_mapped(self, mock_vnish_api, vnish_collector):
        identity = vnish_collector.fetch_identity()
        assert identity.hostname == "Antminer"
        assert identity.mac_address == "AA:BB:CC:11:22:33"
        assert identity.model == "Antminer S21"
        assert identity.firmware == "vnish"
        assert identity.ip_address == "192.168.1.150"

    def test_unreadable_serial_falls_back_to_mac(self, mock_vnish_api, vnish_collector):
        """This firmware reports serial "N/A", which must not become the uid."""
        identity = vnish_collector.fetch_identity()
        assert identity.serial_number == ""
        assert identity.uid == "AA:BB:CC:11:22:33"

    @responses.activate
    def test_real_serial_preferred_over_mac(self, vnish_collector, vnish_fixtures):
        info = {**vnish_fixtures["info"], "serial": "VN42391CX0044"}
        responses.add(responses.GET, f"{VNISH_URL}/api/v1/info", json=info, status=200)
        identity = vnish_collector.fetch_identity()
        assert identity.serial_number == "VN42391CX0044"
        assert identity.uid == "VN42391CX0044"

    @responses.activate
    def test_missing_fields_default_empty(self, vnish_collector):
        responses.add(responses.GET, f"{VNISH_URL}/api/v1/info", json={}, status=200)
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
        assert cooling.fans[0].rpm == 7000
        # Vnish reports one duty cycle for the miner, not per fan.
        assert cooling.fans[0].target_speed_ratio == 1.0

    def test_highest_temperature(self, mock_vnish_api, vnish_collector):
        cooling = vnish_collector.fetch_cooling()
        assert cooling.highest_temperature == {"value": 86.0, "unit": "C"}

    def test_reads_summary_not_status(self, mock_vnish_api, vnish_collector):
        """Regression: fans used to be read from /status, which has none."""
        vnish_collector.fetch_cooling()
        assert any(
            c.request.url.endswith("/api/v1/summary") for c in mock_vnish_api.calls
        )

    @responses.activate
    def test_empty_fans_list(self, vnish_collector):
        responses.add(
            responses.GET,
            f"{VNISH_URL}/api/v1/summary",
            json={"miner": {"cooling": {"fans": []}, "chains": []}},
            status=200,
        )
        cooling = vnish_collector.fetch_cooling()
        assert cooling.fans == []
        assert cooling.highest_temperature is None


class TestFetchHashrate:

    def test_miner_stats_are_in_ghs(self, mock_vnish_api, vnish_collector):
        """hr_realtime is GH/s; instant_hashrate is TH/s and 1000x smaller."""
        hr = vnish_collector.fetch_hashrate()
        assert hr.miner_stats["ghs_5s"] == 181060.66
        assert hr.miner_stats["ghs_av"] == 179911.0
        assert hr.miner_stats["hardware_errors"] == 1494
        assert hr.miner_stats["hr_nominal"] == 187892.94

    def test_pool_stats(self, mock_vnish_api, vnish_collector):
        hr = vnish_collector.fetch_hashrate()
        pools = hr.pool_stats["pools"]
        assert len(pools) == 4
        assert pools[0]["url"] == "sha256.stratum.examplepool.io:3333"
        assert pools[0]["accepted"] == 13070
        assert pools[0]["status"] == "active"
        # "diffa" is Vnish's name for accepted difficulty.
        assert pools[0]["difficulty_accepted"] == 5986123776.0

    def test_share_percentages_derived(self, mock_vnish_api, vnish_collector):
        """Vnish sends no reject/stale percentages, so we compute them."""
        hr = vnish_collector.fetch_hashrate()
        pool = hr.pool_stats["pools"][0]
        total = 13070 + 27 + 35
        assert pool["pool_rejected_pct"] == round(27 / total * 100, 3)
        assert pool["pool_stale_pct"] == round(35 / total * 100, 3)

    def test_idle_pool_no_divide_by_zero(self, mock_vnish_api, vnish_collector):
        hr = vnish_collector.fetch_hashrate()
        idle = hr.pool_stats["pools"][1]
        assert idle["accepted"] == 0
        assert idle["pool_rejected_pct"] == 0.0

    def test_power_stats(self, mock_vnish_api, vnish_collector):
        hr = vnish_collector.fetch_hashrate()
        assert hr.power_stats["watts"] == 3869
        assert hr.power_stats["efficiency"] == 21.505077

    @responses.activate
    def test_empty_response_defaults(self, vnish_collector):
        responses.add(responses.GET, f"{VNISH_URL}/api/v1/summary", json={}, status=200)
        hr = vnish_collector.fetch_hashrate()
        assert hr.miner_stats["ghs_5s"] == 0
        assert hr.pool_stats == {"pools": []}
        assert hr.power_stats["watts"] == 0


class TestFetchUptime:

    def test_fields_mapped(self, mock_vnish_api, vnish_collector):
        uptime = vnish_collector.fetch_uptime()
        assert uptime.bosminer_uptime_s == 147708
        assert uptime.system_uptime_s == 147708
        assert uptime.hostname == "Antminer"
        assert uptime.bos_version["vnish"] == "1.2.6-rc5"
        assert uptime.bos_version["model"] == "Antminer S21"

    @responses.activate
    def test_version_falls_back_to_miner_type(self, vnish_collector, vnish_fixtures):
        """Older builds lack fw_version; summary's miner_type still carries it."""
        info = {k: v for k, v in vnish_fixtures["info"].items() if k != "fw_version"}
        responses.add(responses.GET, f"{VNISH_URL}/api/v1/info", json=info, status=200)
        responses.add(
            responses.GET,
            f"{VNISH_URL}/api/v1/summary",
            json=vnish_fixtures["summary"],
            status=200,
        )
        uptime = vnish_collector.fetch_uptime()
        assert uptime.bos_version["vnish"] == "1.2.6-rc5"

    @responses.activate
    def test_legacy_firmware_version_field(self, vnish_collector):
        responses.add(
            responses.GET,
            f"{VNISH_URL}/api/v1/info",
            json={"firmware_version": "1.1.0", "model": "s19"},
            status=200,
        )
        responses.add(responses.GET, f"{VNISH_URL}/api/v1/summary", json={}, status=200)
        uptime = vnish_collector.fetch_uptime()
        assert uptime.bos_version["vnish"] == "1.1.0"

    @responses.activate
    def test_empty_response_defaults(self, vnish_collector):
        responses.add(responses.GET, f"{VNISH_URL}/api/v1/info", json={}, status=200)
        responses.add(responses.GET, f"{VNISH_URL}/api/v1/summary", json={}, status=200)
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
        assert board.board_name == "Chain 1"
        assert board.id == "1"
        # Temps arrive as {"min": .., "max": ..} ranges.
        assert board.board_temp == {"value": 70, "unit": "C"}
        assert board.highest_chip_temp == {"value": 85, "unit": "C"}
        assert board.lowest_inlet_temp == {"value": 52, "unit": "C"}
        assert board.enabled is True
        assert board.freq_mhz == 460.0
        assert board.stats["hashrate"] == 59595.152
        assert board.stats["hardware_errors"] == 3

    def test_chip_count_from_status_histogram(self, mock_vnish_api, vnish_collector):
        """No chip-count field exists; chip_statuses covers every chip."""
        hb = vnish_collector.fetch_hashboards()
        assert hb.hashboards[0].chips_count == 108  # 1 red + 7 orange + 100 grey
        assert hb.hashboards[0].stats["chips_red"] == 1
        assert hb.hashboards[2].chips_count == 108

    @responses.activate
    def test_missing_board_is_absent_not_disabled(self, vnish_collector, vnish_faulted_summary):
        """Vnish drops disconnected chains from the array entirely."""
        responses.add(
            responses.GET,
            f"{VNISH_URL}/api/v1/summary",
            json=vnish_faulted_summary,
            status=200,
        )
        hb = vnish_collector.fetch_hashboards()
        assert [b.id for b in hb.hashboards] == ["1", "3"]
        assert hb.hashboards[1].enabled is False  # state "failure"

    @responses.activate
    def test_empty_hashboards(self, vnish_collector):
        responses.add(
            responses.GET,
            f"{VNISH_URL}/api/v1/summary",
            json={"miner": {"chains": []}},
            status=200,
        )
        hb = vnish_collector.fetch_hashboards()
        assert hb.hashboards == []


class TestFetchErrors:

    def test_healthy_miner_reports_no_errors(self, mock_vnish_api, vnish_collector):
        """A few red chips are normal and must not raise an error."""
        errs = vnish_collector.fetch_errors()
        assert errs.errors == []

    @responses.activate
    def test_faults_detected(self, vnish_collector, vnish_faulted_summary):
        responses.add(
            responses.GET,
            f"{VNISH_URL}/api/v1/summary",
            json=vnish_faulted_summary,
            status=200,
        )
        errs = vnish_collector.fetch_errors()
        codes = [e.error_codes[0]["code"] for e in errs.errors]
        assert codes.count("FAN_FAILURE") == 1
        assert codes.count("CHAIN_NOT_MINING") == 2  # idle + failure
        assert codes.count("MINER_NOT_MINING") == 1

    @responses.activate
    def test_fault_entry_fields(self, vnish_collector, vnish_faulted_summary):
        responses.add(
            responses.GET,
            f"{VNISH_URL}/api/v1/summary",
            json=vnish_faulted_summary,
            status=200,
        )
        errs = vnish_collector.fetch_errors()
        fan = next(e for e in errs.errors if e.error_codes[0]["code"] == "FAN_FAILURE")
        assert fan.components[0] == {"type": "fan", "id": "1"}
        assert "0 rpm" in fan.message

        chain = next(
            e for e in errs.errors
            if e.error_codes[0]["code"] == "CHAIN_NOT_MINING" and e.components[0]["id"] == "3"
        )
        assert "chain lost" in chain.message

    @responses.activate
    def test_no_errors_on_empty(self, vnish_collector):
        responses.add(responses.GET, f"{VNISH_URL}/api/v1/summary", json={}, status=200)
        errs = vnish_collector.fetch_errors()
        assert errs.errors == []


class TestHTTPErrors:

    @responses.activate
    def test_fetch_raises_on_500(self, vnish_collector):
        responses.add(
            responses.GET,
            f"{VNISH_URL}/api/v1/summary",
            json={"error": "internal"},
            status=500,
        )
        with pytest.raises(Exception):
            vnish_collector.fetch_cooling()

    @responses.activate
    def test_connection_error(self, vnish_collector):
        responses.add(
            responses.GET,
            f"{VNISH_URL}/api/v1/summary",
            body=ConnectionError("refused"),
        )
        with pytest.raises(ConnectionError):
            vnish_collector.fetch_cooling()
