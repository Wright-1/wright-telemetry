"""Tests for Braiins network discovery probes and IP parsing utilities."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import responses
import pytest

from wright_telemetry.discovery import (
    DiscoveredMiner,
    _probe_braiins,
    discovered_to_miner_cfgs,
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
