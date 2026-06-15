"""Full regression tests for BitmainCollector against the simulated API."""

from __future__ import annotations

import pytest
import responses

from tests.conftest import BITMAIN_URL


class TestAuthentication:

    def test_authenticate_sets_digest_auth(self, bitmain_collector):
        """authenticate() should set HTTPDigestAuth on the session."""
        from requests.auth import HTTPDigestAuth
        bitmain_collector.authenticate()
        assert isinstance(bitmain_collector._session.auth, HTTPDigestAuth)

    def test_default_credentials_used_when_none_supplied(self, bitmain_collector_no_auth):
        """When no credentials are given, root:root should be used."""
        from requests.auth import HTTPDigestAuth
        auth = bitmain_collector_no_auth._session.auth
        assert isinstance(auth, HTTPDigestAuth)
        assert auth.username == "root"
        assert auth.password == "root"

    def test_supplied_credentials_used(self, bitmain_collector):
        """Explicitly supplied credentials should appear on the session auth."""
        from requests.auth import HTTPDigestAuth
        auth = bitmain_collector._session.auth
        assert isinstance(auth, HTTPDigestAuth)
        assert auth.username == "root"
        assert auth.password == "root"

    @responses.activate
    def test_auto_retry_on_401(self, bitmain_fixtures):
        """A 401 response should trigger a re-auth and one retry."""
        from wright_telemetry.collectors.bitmain import BitmainCollector
        collector = BitmainCollector(url=BITMAIN_URL, username="root", password="root")

        # First call returns 401; second returns the real fixture.
        responses.add(
            responses.GET,
            f"{BITMAIN_URL}/cgi-bin/get_system_info.cgi",
            json={"error": "unauthorized"},
            status=401,
        )
        responses.add(
            responses.GET,
            f"{BITMAIN_URL}/cgi-bin/get_system_info.cgi",
            json=bitmain_fixtures["get_system_info"],
            status=200,
        )
        responses.add(
            responses.GET,
            f"{BITMAIN_URL}/cgi-bin/miner_type.cgi",
            json=bitmain_fixtures["miner_type"],
            status=200,
        )
        identity = collector.fetch_identity()
        assert identity.serial_number == "JYZZG4UBEJCAH000F"
        assert len(responses.calls) == 3
        collector.close()


class TestFetchIdentity:

    def test_fields_mapped(self, mock_bitmain_api, bitmain_collector):
        identity = bitmain_collector.fetch_identity()
        assert identity.uid == "JYZZG4UBEJCAH000F"
        assert identity.serial_number == "JYZZG4UBEJCAH000F"
        assert identity.hostname == "antminer-rack1-slot4"
        assert identity.mac_address == "02:34:DA:F1:EB:5D"
        assert identity.firmware == "bitmain"
        assert identity.ip_address == "192.168.1.100"

    @responses.activate
    def test_missing_fields_default_empty(self, bitmain_collector):
        responses.add(
            responses.GET,
            f"{BITMAIN_URL}/cgi-bin/get_system_info.cgi",
            json={},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{BITMAIN_URL}/cgi-bin/miner_type.cgi",
            json={},
            status=200,
        )
        identity = bitmain_collector.fetch_identity()
        assert identity.uid == ""
        assert identity.serial_number == ""
        assert identity.hostname == ""
        assert identity.mac_address == ""


class TestFetchCooling:

    def test_fans_count(self, mock_bitmain_api, bitmain_collector):
        cooling = bitmain_collector.fetch_cooling()
        # Fixture has fan: [4560, 4560, 4530, 4530]
        assert len(cooling.fans) == 4

    def test_fan_position_and_rpm(self, mock_bitmain_api, bitmain_collector):
        cooling = bitmain_collector.fetch_cooling()
        assert cooling.fans[0].position == 0
        assert cooling.fans[0].rpm == 4560
        assert cooling.fans[2].position == 2
        assert cooling.fans[2].rpm == 4530

    def test_highest_temperature(self, mock_bitmain_api, bitmain_collector):
        cooling = bitmain_collector.fetch_cooling()
        # Max temp_chip across all chains in the fixture is 65°C (chain 0)
        assert cooling.highest_temperature == {"value": 65.0, "unit": "C"}

    @responses.activate
    def test_empty_fans_and_chains(self, bitmain_collector):
        responses.add(
            responses.GET,
            f"{BITMAIN_URL}/cgi-bin/stats.cgi",
            json={"STATS": [{"fan": [], "chain": []}]},
            status=200,
        )
        cooling = bitmain_collector.fetch_cooling()
        assert cooling.fans == []
        assert cooling.highest_temperature is None

    @responses.activate
    def test_missing_stats_block(self, bitmain_collector):
        responses.add(
            responses.GET,
            f"{BITMAIN_URL}/cgi-bin/stats.cgi",
            json={},
            status=200,
        )
        cooling = bitmain_collector.fetch_cooling()
        assert cooling.fans == []
        assert cooling.highest_temperature is None


class TestFetchHashrate:

    def test_miner_stats_present(self, mock_bitmain_api, bitmain_collector):
        hr = bitmain_collector.fetch_hashrate()
        assert hr.miner_stats["ghs_5s"] == pytest.approx(232436.76)
        assert hr.miner_stats["ghs_av"] == pytest.approx(231429.72)
        assert hr.miner_stats["rate_unit"] == "GH/s"

    def test_pool_stats_present(self, mock_bitmain_api, bitmain_collector):
        hr = bitmain_collector.fetch_hashrate()
        assert len(hr.pool_stats["pools"]) == 2
        pool = hr.pool_stats["pools"][0]
        assert pool["url"] == "stratum+tcp://pool.example.com:3333"
        assert pool["user"] == "wallet.worker1"
        assert pool["status"] == "Alive"
        assert pool["accepted"] == 1024
        assert pool["rejected"] == 3
        assert pool["stale"] == 1

    def test_power_stats_present(self, mock_bitmain_api, bitmain_collector):
        hr = bitmain_collector.fetch_hashrate()
        assert hr.power_stats["watts"] == 3250
        assert hr.power_stats["efficiency"] == pytest.approx(14.03)

    @responses.activate
    def test_empty_response_defaults(self, bitmain_collector):
        responses.add(
            responses.GET,
            f"{BITMAIN_URL}/cgi-bin/stats.cgi",
            json={},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{BITMAIN_URL}/cgi-bin/pools.cgi",
            json={},
            status=200,
        )
        hr = bitmain_collector.fetch_hashrate()
        assert hr.miner_stats["ghs_5s"] == 0
        assert hr.pool_stats["pools"] == []
        assert hr.power_stats["watts"] == 0


class TestFetchUptime:

    def test_elapsed_mapped(self, mock_bitmain_api, bitmain_collector):
        uptime = bitmain_collector.fetch_uptime()
        assert uptime.bosminer_uptime_s == 3600
        assert uptime.system_uptime_s == 3600

    def test_hostname_mapped(self, mock_bitmain_api, bitmain_collector):
        uptime = bitmain_collector.fetch_uptime()
        assert uptime.hostname == "antminer-rack1-slot4"

    def test_firmware_version_present(self, mock_bitmain_api, bitmain_collector):
        uptime = bitmain_collector.fetch_uptime()
        assert uptime.bos_version["firmware"] == "FD-1.5(250418-S21+)"
        assert uptime.bos_version["firmware_type"] == "Factory"

    @responses.activate
    def test_empty_response_defaults(self, bitmain_collector):
        responses.add(
            responses.GET,
            f"{BITMAIN_URL}/cgi-bin/stats.cgi",
            json={},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{BITMAIN_URL}/cgi-bin/get_system_info.cgi",
            json={},
            status=200,
        )
        uptime = bitmain_collector.fetch_uptime()
        assert uptime.bosminer_uptime_s == 0
        assert uptime.hostname == ""
        assert uptime.bos_version["firmware"] == ""


class TestFetchHashboards:

    def test_board_count(self, mock_bitmain_api, bitmain_collector):
        hb = bitmain_collector.fetch_hashboards()
        # Fixture has 3 chains
        assert len(hb.hashboards) == 3

    def test_board_fields(self, mock_bitmain_api, bitmain_collector):
        hb = bitmain_collector.fetch_hashboards()
        board = hb.hashboards[0]
        assert board.board_name == "Chain 0"
        assert board.id == "0"
        assert board.chips_count == 55
        assert board.enabled is True
        assert board.stats["serial_number"] == "JYZZYT0BEJCAH002M"
        assert board.stats["ghs_real"] == pytest.approx(77100.0)
        assert board.stats["freq_avg"] == 708
        assert board.freq_mhz == pytest.approx(708.0)

    def test_freq_mhz_all_boards(self, mock_bitmain_api, bitmain_collector):
        hb = bitmain_collector.fetch_hashboards()
        assert hb.hashboards[0].freq_mhz == pytest.approx(708.0)
        assert hb.hashboards[1].freq_mhz == pytest.approx(710.0)
        assert hb.hashboards[2].freq_mhz == pytest.approx(710.0)

    def test_board_temps(self, mock_bitmain_api, bitmain_collector):
        hb = bitmain_collector.fetch_hashboards()
        board = hb.hashboards[0]
        # temp_pcb max in chain 0: [50, 50, 60, 60] → 60
        assert board.board_temp == {"value": 60.0, "unit": "C"}
        # temp_chip max in chain 0: [55, 55, 65, 65] → 65
        assert board.highest_chip_temp == {"value": 65.0, "unit": "C"}

    def test_inlet_outlet_not_available(self, mock_bitmain_api, bitmain_collector):
        hb = bitmain_collector.fetch_hashboards()
        # Antminer stock firmware doesn't expose inlet/outlet separately
        assert hb.hashboards[0].lowest_inlet_temp is None
        assert hb.hashboards[0].highest_outlet_temp is None

    @responses.activate
    def test_empty_chain_list(self, bitmain_collector):
        responses.add(
            responses.GET,
            f"{BITMAIN_URL}/cgi-bin/stats.cgi",
            json={"STATS": [{"chain": []}]},
            status=200,
        )
        hb = bitmain_collector.fetch_hashboards()
        assert hb.hashboards == []


class TestFetchErrors:

    def test_warnings_parsed(self, mock_bitmain_api, bitmain_collector):
        errs = bitmain_collector.fetch_errors()
        assert len(errs.errors) == 2

    def test_error_entry_fields(self, mock_bitmain_api, bitmain_collector):
        errs = bitmain_collector.fetch_errors()
        entry = errs.errors[0]
        assert "Fan speed below threshold" in entry.message
        assert entry.timestamp == "2025-04-18T10:23:45Z"
        assert entry.error_codes[0]["code"] == "W003"
        assert entry.error_codes[0]["level"] == "warning"
        assert entry.components == []

    @responses.activate
    def test_no_warnings(self, bitmain_collector):
        responses.add(
            responses.GET,
            f"{BITMAIN_URL}/cgi-bin/warning.cgi",
            json={"WARNINGS": []},
            status=200,
        )
        errs = bitmain_collector.fetch_errors()
        assert errs.errors == []

    @responses.activate
    def test_missing_warnings_key(self, bitmain_collector):
        responses.add(
            responses.GET,
            f"{BITMAIN_URL}/cgi-bin/warning.cgi",
            json={},
            status=200,
        )
        errs = bitmain_collector.fetch_errors()
        assert errs.errors == []


class TestHTTPErrors:

    @responses.activate
    def test_fetch_raises_on_500(self, bitmain_collector):
        responses.add(
            responses.GET,
            f"{BITMAIN_URL}/cgi-bin/stats.cgi",
            json={"error": "internal"},
            status=500,
        )
        with pytest.raises(Exception):
            bitmain_collector.fetch_cooling()

    @responses.activate
    def test_connection_error(self, bitmain_collector):
        responses.add(
            responses.GET,
            f"{BITMAIN_URL}/cgi-bin/stats.cgi",
            body=ConnectionError("refused"),
        )
        with pytest.raises(ConnectionError):
            bitmain_collector.fetch_cooling()


class TestFactoryRegistration:

    def test_bitmain_registered_in_factory(self):
        import wright_telemetry.collectors.bitmain  # noqa: F401
        from wright_telemetry.collectors.factory import CollectorFactory
        assert "bitmain" in CollectorFactory.available()

    def test_factory_creates_bitmain_collector(self):
        import wright_telemetry.collectors.bitmain  # noqa: F401
        from wright_telemetry.collectors.factory import CollectorFactory
        from wright_telemetry.collectors.bitmain import BitmainCollector
        c = CollectorFactory.create("bitmain", url=BITMAIN_URL, username="root", password="root")
        assert isinstance(c, BitmainCollector)
        c.close()
