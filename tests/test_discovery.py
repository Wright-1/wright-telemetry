"""Tests for Braiins network discovery probes and IP parsing utilities."""

from __future__ import annotations

import ipaddress
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import responses
import pytest

from wright_telemetry.discovery import (
    DiscoveredMiner,
    _probe_braiins,
    _probe_vnish,
    default_subnet,
    default_subnets,
    discovered_to_miner_cfgs,
    firmware_types_for_collector,
    load_subnets_file,
    merge_miners,
    parse_ip_target,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "braiins"


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / name).read_text())


# ---------------------------------------------------------------
# _probe_braiins
# ---------------------------------------------------------------

class TestProbeBraiins:

    @responses.activate
    def test_200_returns_miner(self):
        details = _load("miner_details.json")
        responses.add(
            responses.GET,
            "http://10.0.0.1/api/v1/miner/details",
            json=details,
            status=200,
        )
        result = _probe_braiins("10.0.0.1")
        assert result is not None
        assert result.firmware == "braiins"
        assert result.hostname == "antminer-rack3-slot7"
        assert result.mac_address == "AA:BB:CC:DD:EE:F1"

    @responses.activate
    def test_401_still_detected(self):
        responses.add(
            responses.GET,
            "http://10.0.0.2/api/v1/miner/details",
            json={"error": "auth required"},
            status=401,
        )
        result = _probe_braiins("10.0.0.2")
        assert result is not None
        assert result.firmware == "braiins"
        assert result.hostname == ""
        assert result.mac_address == ""

    @responses.activate
    def test_404_returns_none(self):
        responses.add(
            responses.GET,
            "http://10.0.0.3/api/v1/miner/details",
            json={"error": "not found"},
            status=404,
        )
        result = _probe_braiins("10.0.0.3")
        assert result is None

    @responses.activate
    def test_connection_error_returns_none(self):
        responses.add(
            responses.GET,
            "http://10.0.0.4/api/v1/miner/details",
            body=ConnectionError("timeout"),
        )
        result = _probe_braiins("10.0.0.4")
        assert result is None


# ---------------------------------------------------------------
# _probe_vnish
# ---------------------------------------------------------------

class TestProbeVnish:

    @responses.activate
    def test_200_with_firmware_version(self):
        responses.add(
            responses.GET,
            "http://10.0.0.10/api/v1/info",
            json={
                "hostname": "vn-miner",
                "mac": "AA:BB:CC:DD:EE:FF",
                "firmware_version": "1.0.0",
            },
            status=200,
        )
        result = _probe_vnish("10.0.0.10")
        assert result is not None
        assert result.firmware == "vnish"
        assert result.hostname == "vn-miner"
        assert result.mac_address == "AA:BB:CC:DD:EE:FF"

    @responses.activate
    def test_401_not_treated_as_vnish(self):
        responses.add(
            responses.GET,
            "http://10.0.0.11/api/v1/info",
            json={"error": "auth required"},
            status=401,
        )
        assert _probe_vnish("10.0.0.11") is None

    @responses.activate
    def test_200_without_firmware_version_returns_none(self):
        responses.add(
            responses.GET,
            "http://10.0.0.12/api/v1/info",
            json={"hostname": "other"},
            status=200,
        )
        assert _probe_vnish("10.0.0.12") is None


# ---------------------------------------------------------------
# firmware_types_for_collector
# ---------------------------------------------------------------

class TestFirmwareTypesForCollector:

    def test_braiins(self):
        assert firmware_types_for_collector("braiins") == ["braiins"]

    def test_case_insensitive(self):
        assert firmware_types_for_collector("VNISH") == ["vnish"]

    def test_unknown_returns_none(self):
        assert firmware_types_for_collector("future-fw") is None

    def test_empty_defaults_to_braiins(self):
        assert firmware_types_for_collector("") == ["braiins"]


# ---------------------------------------------------------------
# parse_ip_target
# ---------------------------------------------------------------

class TestParseIpTarget:

    def test_single_ip(self):
        assert parse_ip_target("192.168.1.50") == ["192.168.1.50"]

    def test_cidr_24(self):
        hosts = parse_ip_target("192.168.1.0/24")
        assert len(hosts) == 254
        assert "192.168.1.1" in hosts
        assert "192.168.1.254" in hosts
        assert "192.168.1.0" not in hosts
        assert "192.168.1.255" not in hosts

    def test_cidr_30(self):
        hosts = parse_ip_target("10.0.0.0/30")
        assert len(hosts) == 2
        assert "10.0.0.1" in hosts
        assert "10.0.0.2" in hosts

    def test_range(self):
        hosts = parse_ip_target("192.168.1.100-192.168.1.105")
        assert len(hosts) == 6
        assert hosts[0] == "192.168.1.100"
        assert hosts[-1] == "192.168.1.105"

    def test_reversed_range(self):
        hosts = parse_ip_target("192.168.1.105-192.168.1.100")
        assert len(hosts) == 6

    def test_invalid_ip_raises(self):
        with pytest.raises(ValueError):
            parse_ip_target("not.an.ip")


# ---------------------------------------------------------------
# merge_miners / discovered_to_miner_cfgs
# ---------------------------------------------------------------

class TestMergeMiners:

    def test_no_overlap(self):
        manual = [{"url": "http://1.1.1.1", "name": "m1"}]
        discovered = [{"url": "http://2.2.2.2", "name": "d1"}]
        merged = merge_miners(manual, discovered)
        assert len(merged) == 2

    def test_manual_wins_on_overlap(self):
        manual = [{"url": "http://1.1.1.1", "name": "manual"}]
        discovered = [{"url": "http://1.1.1.1", "name": "discovered"}]
        merged = merge_miners(manual, discovered)
        assert len(merged) == 1
        assert merged[0]["name"] == "manual"


class TestDiscoveredToMinerCfgs:

    def test_basic_conversion(self):
        miners = [
            DiscoveredMiner(ip="10.0.0.1", firmware="braiins", hostname="host1", mac_address="AA:BB"),
        ]
        cfgs = discovered_to_miner_cfgs(miners, default_username="admin", default_password_b64="cHc=")
        assert len(cfgs) == 1
        assert cfgs[0]["url"] == "http://10.0.0.1"
        assert cfgs[0]["username"] == "admin"
        assert cfgs[0]["firmware"] == "braiins"
        assert cfgs[0]["password_b64"] == "cHc="
        assert cfgs[0]["discovered"] is True

    def test_no_password(self):
        miners = [
            DiscoveredMiner(ip="10.0.0.2", firmware="braiins", hostname="", mac_address=""),
        ]
        cfgs = discovered_to_miner_cfgs(miners, default_username="root", default_password_b64="")
        assert "password_b64" not in cfgs[0]


# ---------------------------------------------------------------
# default_subnets
# ---------------------------------------------------------------

class TestDefaultSubnets:

    def test_returns_list(self):
        result = default_subnets()
        assert isinstance(result, list)

    def test_no_loopback(self):
        result = default_subnets()
        for subnet in result:
            assert not subnet.startswith("127.")

    def test_all_valid_cidr24(self):
        result = default_subnets()
        for subnet in result:
            net = ipaddress.IPv4Network(subnet, strict=False)
            assert net.prefixlen == 24

    def test_no_duplicates(self):
        result = default_subnets()
        assert len(result) == len(set(result))

    def test_uses_getaddrinfo_result(self):
        fake_infos = [
            (None, None, None, None, ("10.0.1.5", 0)),
            (None, None, None, None, ("10.0.2.5", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=fake_infos):
            with patch("wright_telemetry.discovery.get_local_ip", return_value=None):
                result = default_subnets()
        assert "10.0.1.0/24" in result
        assert "10.0.2.0/24" in result
        assert len(result) == 2

    def test_deduplicates_same_subnet(self):
        fake_infos = [
            (None, None, None, None, ("192.168.1.10", 0)),
            (None, None, None, None, ("192.168.1.20", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=fake_infos):
            with patch("wright_telemetry.discovery.get_local_ip", return_value=None):
                result = default_subnets()
        assert result == ["192.168.1.0/24"]

    def test_loopback_filtered(self):
        fake_infos = [
            (None, None, None, None, ("127.0.0.1", 0)),
            (None, None, None, None, ("10.0.0.5", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=fake_infos):
            with patch("wright_telemetry.discovery.get_local_ip", return_value=None):
                result = default_subnets()
        assert result == ["10.0.0.0/24"]

    def test_udp_fallback_supplements(self):
        fake_infos = [(None, None, None, None, ("10.0.1.5", 0))]
        with patch("socket.getaddrinfo", return_value=fake_infos):
            with patch("wright_telemetry.discovery.get_local_ip", return_value="10.0.2.9"):
                result = default_subnets()
        assert "10.0.1.0/24" in result
        assert "10.0.2.0/24" in result

    def test_udp_fallback_no_duplicate(self):
        fake_infos = [(None, None, None, None, ("10.0.1.5", 0))]
        with patch("socket.getaddrinfo", return_value=fake_infos):
            with patch("wright_telemetry.discovery.get_local_ip", return_value="10.0.1.99"):
                result = default_subnets()
        assert result == ["10.0.1.0/24"]

    def test_getaddrinfo_exception_falls_back_to_udp(self):
        with patch("socket.getaddrinfo", side_effect=OSError("fail")):
            with patch("wright_telemetry.discovery.get_local_ip", return_value="172.16.0.50"):
                result = default_subnets()
        assert result == ["172.16.0.0/24"]

    def test_both_fail_returns_empty(self):
        with patch("socket.getaddrinfo", side_effect=OSError("fail")):
            with patch("wright_telemetry.discovery.get_local_ip", return_value=None):
                result = default_subnets()
        assert result == []


# ---------------------------------------------------------------
# default_subnet (backwards compat wrapper)
# ---------------------------------------------------------------

class TestDefaultSubnetBackwardsCompat:

    def test_returns_first(self):
        with patch("wright_telemetry.discovery.default_subnets", return_value=["10.0.0.0/24", "10.0.1.0/24"]):
            assert default_subnet() == "10.0.0.0/24"

    def test_returns_none_when_empty(self):
        with patch("wright_telemetry.discovery.default_subnets", return_value=[]):
            assert default_subnet() is None


# ---------------------------------------------------------------
# load_subnets_file
# ---------------------------------------------------------------

class TestLoadSubnetsFile:

    def _write_temp(self, content: str) -> str:
        fd, path = tempfile.mkstemp(suffix=".txt")
        try:
            os.write(fd, content.encode("utf-8"))
        finally:
            os.close(fd)
        return path

    def test_basic_cidrs(self):
        path = self._write_temp("192.168.1.0/24\n192.168.2.0/24\n")
        try:
            assert load_subnets_file(path) == ["192.168.1.0/24", "192.168.2.0/24"]
        finally:
            os.unlink(path)

    def test_skips_comments(self):
        path = self._write_temp("# comment\n10.0.0.0/24\n")
        try:
            assert load_subnets_file(path) == ["10.0.0.0/24"]
        finally:
            os.unlink(path)

    def test_skips_blank_lines(self):
        path = self._write_temp("\n\n10.0.1.0/27\n\n10.0.2.0/27\n")
        try:
            assert load_subnets_file(path) == ["10.0.1.0/27", "10.0.2.0/27"]
        finally:
            os.unlink(path)

    def test_strips_whitespace(self):
        path = self._write_temp("  192.168.5.0/24  \n  10.0.0.0/8  \n")
        try:
            assert load_subnets_file(path) == ["192.168.5.0/24", "10.0.0.0/8"]
        finally:
            os.unlink(path)

    def test_ranges_accepted(self):
        path = self._write_temp("192.168.1.100-192.168.1.200\n")
        try:
            assert load_subnets_file(path) == ["192.168.1.100-192.168.1.200"]
        finally:
            os.unlink(path)

    def test_empty_file_returns_empty_list(self):
        path = self._write_temp("")
        try:
            assert load_subnets_file(path) == []
        finally:
            os.unlink(path)

    def test_only_comments_returns_empty_list(self):
        path = self._write_temp("# comment\n# another\n")
        try:
            assert load_subnets_file(path) == []
        finally:
            os.unlink(path)

    def test_missing_file_raises_oserror(self):
        with pytest.raises(OSError):
            load_subnets_file("/tmp/does_not_exist_wright_telemetry_test_xyz.txt")

    def test_75_vlans(self):
        lines = "\n".join(f"192.168.{i}.0/27" for i in range(1, 76))
        path = self._write_temp(lines + "\n")
        try:
            result = load_subnets_file(path)
        finally:
            os.unlink(path)
        assert len(result) == 75
        assert result[0] == "192.168.1.0/27"
        assert result[74] == "192.168.75.0/27"
