"""Regression tests for WhatsminerCollector against the btminer TCP API.

Fixtures are a verbatim capture from a live M30SVE20 (firmware
20220422.18.REL, API 2.0.3) on port 4028, so these tests pin the mapping
against real field names rather than the documented ideal — the two differ:
that firmware omits ``minersn`` and ``Miner Type`` entirely.
"""

from __future__ import annotations

import json
import socket
from unittest.mock import MagicMock, patch

import pytest

from wright_telemetry.collectors.factory import CollectorFactory
from wright_telemetry.collectors.whatsminer import WhatsminerCollector, _repair_json


class TestAuthentication:

    def test_authenticate_is_noop(self, mock_whatsminer_api, whatsminer_collector):
        whatsminer_collector.authenticate()


class TestFetchIdentity:

    def test_fields_mapped(self, mock_whatsminer_api, whatsminer_collector):
        identity = whatsminer_collector.fetch_identity()
        assert identity.mac_address == "C8:08:18:00:15:EF"
        assert identity.hostname == "WhatsMiner"
        assert identity.firmware == "whatsminer"

    def test_uid_falls_back_to_mac(self, mock_whatsminer_api, whatsminer_collector):
        # This firmware returns no minersn, so the MAC is the only stable ID.
        identity = whatsminer_collector.fetch_identity()
        assert identity.serial_number == ""
        assert identity.uid == "C8:08:18:00:15:EF"

    def test_model_from_devdetails_when_summary_lacks_miner_type(
        self, mock_whatsminer_api, whatsminer_collector
    ):
        identity = whatsminer_collector.fetch_identity()
        assert identity.model == "M30SVE20"

    def test_model_prefers_summary_miner_type(self, whatsminer_collector):
        def _fake(self, command, **params):
            if command == "summary":
                return {"SUMMARY": [{"Miner Type": "M30S+_VH70"}]}
            return {}

        with patch.object(WhatsminerCollector, "_send_command", _fake):
            assert whatsminer_collector.fetch_identity().model == "M30S+_VH70"

    def test_missing_everything_defaults_empty(self, whatsminer_collector):
        with patch.object(WhatsminerCollector, "_send_command", return_value={}):
            identity = whatsminer_collector.fetch_identity()
            assert identity.uid == ""
            assert identity.mac_address == ""
            assert identity.model == ""


class TestFetchCooling:

    def test_in_and_out_fans(self, mock_whatsminer_api, whatsminer_collector):
        cooling = whatsminer_collector.fetch_cooling()
        assert [(f.position, f.rpm) for f in cooling.fans] == [(0, 6990), (1, 6930)]

    def test_no_fan_duty_reported(self, mock_whatsminer_api, whatsminer_collector):
        cooling = whatsminer_collector.fetch_cooling()
        assert all(f.target_speed_ratio == 0.0 for f in cooling.fans)

    def test_highest_temperature_is_chip_max(self, mock_whatsminer_api, whatsminer_collector):
        cooling = whatsminer_collector.fetch_cooling()
        # Chip Temp Max 101.33 beats board Temperature 80.0.
        assert cooling.highest_temperature == {"value": 101.33, "unit": "C"}

    def test_env_temp_excluded(self, whatsminer_collector):
        # Env Temp is ambient intake air and must not become highest_temperature.
        with patch.object(
            WhatsminerCollector, "_send_command",
            return_value={"SUMMARY": [{"Env Temp": 200.0, "Temperature": 50.0}]},
        ):
            cooling = whatsminer_collector.fetch_cooling()
            assert cooling.highest_temperature == {"value": 50.0, "unit": "C"}

    def test_missing_fans(self, whatsminer_collector):
        with patch.object(
            WhatsminerCollector, "_send_command", return_value={"SUMMARY": [{}]},
        ):
            cooling = whatsminer_collector.fetch_cooling()
            assert cooling.fans == []
            assert cooling.highest_temperature is None


class TestFetchHashrate:

    def test_mhs_converted_to_ghs(self, mock_whatsminer_api, whatsminer_collector):
        ms = whatsminer_collector.fetch_hashrate().miner_stats
        assert ms["ghs_5s"] == pytest.approx(104999.9864)
        assert ms["ghs_15m"] == pytest.approx(87376.5112)

    def test_ghs_av_uses_rolling_window_not_lifetime(
        self, mock_whatsminer_api, whatsminer_collector
    ):
        ms = whatsminer_collector.fetch_hashrate().miner_stats
        # "MHS av" is a since-boot lifetime average; ghs_av must track MHS 15m.
        assert ms["ghs_av"] == pytest.approx(87376.5112)

    def test_ghs_av_falls_back_to_lifetime_average(self, whatsminer_collector):
        with patch.object(
            WhatsminerCollector, "_send_command",
            return_value={"SUMMARY": [{"MHS av": 50000000.0}]},
        ):
            ms = whatsminer_collector.fetch_hashrate().miner_stats
            assert ms["ghs_av"] == pytest.approx(50000.0)

    def test_nominal_ghs_not_rescaled(self, mock_whatsminer_api, whatsminer_collector):
        hr = whatsminer_collector.fetch_hashrate()
        # "Factory GHS" is already GH/s — 91027 GH/s == 91 TH/s.
        assert hr.miner_stats["nominal_ghs"] == 91027
        assert hr.get_nominal_ghs() == 91027

    def test_power_stats(self, mock_whatsminer_api, whatsminer_collector):
        ps = whatsminer_collector.fetch_hashrate().power_stats
        assert ps["watts"] == 3370
        assert ps["efficiency"] == pytest.approx(38.57)
        assert ps["power_limit"] == 3360
        assert ps["power_mode"] == "Normal"

    def test_psu_units_decoded(self, mock_whatsminer_api, whatsminer_collector):
        ps = whatsminer_collector.fetch_hashrate().power_stats
        # get_psu sends strings: iin is 1mA units, vin is 10mV units.
        assert ps["psu_amps"] == pytest.approx(13.968)
        assert ps["psu_volts"] == pytest.approx(242.0)
        assert ps["psu_fan_rpm"] == 9840
        # Sanity: V x A should land near the reported input power.
        assert ps["psu_volts"] * ps["psu_amps"] == pytest.approx(3370, rel=0.01)

    def test_missing_psu_does_not_raise(self, whatsminer_collector):
        def _fake(self, command, **params):
            return {} if command == "get_psu" else {"SUMMARY": [{}], "POOLS": []}

        with patch.object(WhatsminerCollector, "_send_command", _fake):
            ps = whatsminer_collector.fetch_hashrate().power_stats
            assert ps["psu_amps"] == 0.0
            assert ps["psu_volts"] == 0.0

    def test_pools_mapped(self, mock_whatsminer_api, whatsminer_collector):
        pools = whatsminer_collector.fetch_hashrate().pool_stats["pools"]
        assert len(pools) == 3
        assert pools[0]["url"] == "stratum+tcp://stratum.braiins.com:3333"
        assert pools[0]["user"] == "jusmcafee.burlesonwhat"
        assert pools[0]["status"] == "Alive"
        assert pools[0]["accepted"] == 458
        assert pools[0]["rejected"] == 7
        assert pools[0]["pool_rejected_pct"] == pytest.approx(1.2516)


class TestFetchUptime:

    def test_elapsed_and_system_uptime_differ(self, mock_whatsminer_api, whatsminer_collector):
        up = whatsminer_collector.fetch_uptime()
        assert up.bosminer_uptime_s == 4680   # Elapsed — mining process
        assert up.system_uptime_s == 6722     # Uptime — control board

    def test_version_fields(self, mock_whatsminer_api, whatsminer_collector):
        up = whatsminer_collector.fetch_uptime()
        assert up.hostname == "WhatsMiner"
        assert up.bos_version["firmware"] == "20220422.18.REL"
        assert up.bos_version["api"] == "2.0.3"
        assert up.bos_version["platform"] == "H6OS"

    def test_system_uptime_defaults_to_elapsed(self, whatsminer_collector):
        with patch.object(
            WhatsminerCollector, "_send_command",
            return_value={"SUMMARY": [{"Elapsed": 99}]},
        ):
            up = whatsminer_collector.fetch_uptime()
            assert up.system_uptime_s == 99


class TestFetchHashboards:

    def test_three_boards(self, mock_whatsminer_api, whatsminer_collector):
        hb = whatsminer_collector.fetch_hashboards()
        assert len(hb.hashboards) == 3
        assert [b.id for b in hb.hashboards] == ["0", "1", "2"]

    def test_temps_and_chips(self, mock_whatsminer_api, whatsminer_collector):
        board = whatsminer_collector.fetch_hashboards().hashboards[0]
        assert board.board_temp == {"value": 76.5, "unit": "C"}
        assert board.highest_chip_temp == {"value": 97.08, "unit": "C"}
        assert board.chips_count == 111
        assert board.freq_mhz == 696.0
        assert board.enabled is True

    def test_board_serial_and_nominal(self, mock_whatsminer_api, whatsminer_collector):
        board = whatsminer_collector.fetch_hashboards().hashboards[0]
        assert board.stats["serial_number"] == "SEM1ES6F211B24X31339"
        # Per-board Factory GHS 30378 GH/s -> nominal_mhs in MH/s.
        assert board.stats["nominal_mhs"] == 30378000

    def test_disabled_board(self, whatsminer_collector):
        with patch.object(
            WhatsminerCollector, "_send_command",
            return_value={"DEVS": [{"Slot": 0, "Enabled": "N", "Status": "Dead"}]},
        ):
            board = whatsminer_collector.fetch_hashboards().hashboards[0]
            assert board.enabled is False

    def test_falls_back_to_devs_when_edevs_empty(self, whatsminer_collector):
        calls: list[str] = []

        def _fake(self, command, **params):
            calls.append(command)
            if command == "edevs":
                return {}
            return {"DEVS": [{"Slot": 0, "Enabled": "Y", "Status": "Alive"}]}

        with patch.object(WhatsminerCollector, "_send_command", _fake):
            hb = whatsminer_collector.fetch_hashboards()
        assert calls == ["edevs", "devs"]
        assert len(hb.hashboards) == 1

    def test_no_extra_call_when_edevs_works(self, mock_whatsminer_api, whatsminer_collector):
        with patch.object(
            WhatsminerCollector, "_send_command", autospec=True,
            side_effect=lambda self, command, **params: (
                {"DEVS": [{"Slot": 0}]} if command == "edevs" else pytest.fail("devs called")
            ),
        ):
            assert len(whatsminer_collector.fetch_hashboards().hashboards) == 1


class TestFetchErrors:

    def test_healthy_miner_has_no_errors(self, mock_whatsminer_api, whatsminer_collector):
        assert whatsminer_collector.fetch_errors().errors == []

    def test_code_with_reason_and_timestamp(self, whatsminer_collector):
        payload = {"Msg": {"error_code": [
            {"531": "2025-03-12 14:52:35", "reason": "Slot1 not found."},
        ]}}
        with patch.object(WhatsminerCollector, "_send_command", return_value=payload):
            err = whatsminer_collector.fetch_errors().errors[0]
        assert err.message == "Slot1 not found."
        assert err.timestamp == "2025-03-12 14:52:35"
        assert err.error_codes == [{"code": "531"}]

    def test_bare_code_list(self, whatsminer_collector):
        with patch.object(
            WhatsminerCollector, "_send_command",
            return_value={"Msg": {"error_code": [2350]}},
        ):
            err = whatsminer_collector.fetch_errors().errors[0]
        assert err.message == "Error code 2350"
        assert err.error_codes == [{"code": "2350"}]

    def test_dict_of_codes(self, whatsminer_collector):
        with patch.object(
            WhatsminerCollector, "_send_command",
            return_value={"Msg": {"error_code": {"532": "2025-03-12 14:52:35"}}},
        ):
            errors = whatsminer_collector.fetch_errors().errors
        assert errors[0].error_codes == [{"code": "532"}]

    def test_missing_error_field(self, whatsminer_collector):
        with patch.object(WhatsminerCollector, "_send_command", return_value={}):
            assert whatsminer_collector.fetch_errors().errors == []


class TestProtocol:
    """The wire format, which is where btminer differs from CGMiner."""

    @staticmethod
    def _mock_socket(payloads: list[bytes]) -> MagicMock:
        sock = MagicMock()
        sock.__enter__ = MagicMock(return_value=sock)
        sock.__exit__ = MagicMock(return_value=False)
        sock.recv.side_effect = payloads + [b""]
        return sock

    def test_request_uses_cmd_key(self, whatsminer_collector):
        sock = self._mock_socket([b'{"STATUS":"S"}'])
        with patch("socket.socket", return_value=sock):
            whatsminer_collector._send_command("summary")
        sent = json.loads(sock.sendall.call_args[0][0].decode())
        # "command" is the CGMiner spelling and returns "invalid cmd" here.
        assert sent == {"cmd": "summary"}

    def test_extra_params_are_included(self, whatsminer_collector):
        sock = self._mock_socket([b'{"STATUS":"S"}'])
        with patch("socket.socket", return_value=sock):
            whatsminer_collector._send_command("get_miner_info", info="mac,hostname")
        sent = json.loads(sock.sendall.call_args[0][0].decode())
        assert sent == {"cmd": "get_miner_info", "info": "mac,hostname"}

    def test_nul_terminated_response(self, whatsminer_collector):
        sock = self._mock_socket([b'{"STATUS":"S","Msg":{"a":1}}\x00'])
        with patch("socket.socket", return_value=sock):
            assert whatsminer_collector._send_command("summary")["Msg"] == {"a": 1}

    def test_split_response_is_reassembled(self, whatsminer_collector):
        sock = self._mock_socket([b'{"STATUS":"S",', b'"Msg":{"a":1}}'])
        with patch("socket.socket", return_value=sock):
            assert whatsminer_collector._send_command("summary")["Msg"] == {"a": 1}

    def test_invalid_cmd_becomes_empty_dict(self, whatsminer_collector):
        # A rejected command must degrade one metric, not raise and kill the poll.
        sock = self._mock_socket([b'{"STATUS":"E","Code":14,"Msg":"invalid cmd"}'])
        with patch("socket.socket", return_value=sock):
            assert whatsminer_collector._send_command("stats") == {}

    def test_socket_error_propagates(self, whatsminer_collector):
        with patch("socket.socket", side_effect=socket.error("refused")):
            with pytest.raises(socket.error):
                whatsminer_collector._send_command("summary")

    def test_trailing_comma_is_repaired(self):
        # Observed verbatim on firmware 20220422.18.REL for a bare
        # get_miner_info: a comma before the closing brace.
        broken = '{"Msg":{"ledstat":"auto",},"Description":""}'
        assert json.loads(_repair_json(broken))["Msg"] == {"ledstat": "auto"}


class TestRegistration:

    def test_registered_with_factory(self):
        assert "whatsminer" in CollectorFactory.available()

    def test_factory_creates_collector(self):
        collector = CollectorFactory.create("whatsminer", url="192.168.1.220")
        assert isinstance(collector, WhatsminerCollector)

    def test_host_parsed_from_url(self):
        assert WhatsminerCollector(url="http://192.168.1.220")._host == "192.168.1.220"
        assert WhatsminerCollector(url="192.168.1.220")._host == "192.168.1.220"
