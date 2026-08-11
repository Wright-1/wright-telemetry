"""Shared pytest fixtures for miner API test simulators."""

from __future__ import annotations

import gc
import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import responses

try:
    import psutil as _psutil
    _PROC = _psutil.Process(os.getpid())
except ImportError:
    _psutil = None
    _PROC = None

@pytest.fixture(autouse=True)
def check_fd_leaks():
    if _PROC is None or sys.platform == "win32":
        yield
        return

    gc.collect()
    fd_before = _PROC.num_fds()

    yield

    gc.collect()
    fd_after = _PROC.num_fds()

    leaked = fd_after - fd_before
    if leaked > 0:
        try:
            conns = _psutil.net_connections(kind="all")
            detail = "\n".join(
                f"  fd={c.fd} {c.type.name} {c.laddr} -> {c.raddr} [{c.status}]"
                for c in conns
            )
        except Exception:
            detail = "  (could not enumerate connections)"
        pytest.fail(f"Test leaked {leaked} file descriptor(s).\nOpen connections:\n{detail}")


BRAIINS_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "braiins"
LUXOS_FIXTURES_DIR   = Path(__file__).parent / "fixtures" / "luxos"
VNISH_FIXTURES_DIR   = Path(__file__).parent / "fixtures" / "vnish"
BITMAIN_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "bitmain"
SEALMINER_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "sealminer"
WHATSMINER_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "whatsminer"
MINER_URL   = "http://192.168.1.100"
LUXOS_HOST  = "192.168.1.200"
VNISH_URL   = "http://192.168.1.150"
BITMAIN_URL = "http://192.168.1.200"
SEALMINER_HOST = "192.168.1.210"
WHATSMINER_HOST = "192.168.1.220"


def _load_braiins(name: str) -> dict[str, Any]:
    return json.loads((BRAIINS_FIXTURES_DIR / name).read_text())


def _load_luxos(name: str) -> dict[str, Any]:
    return json.loads((LUXOS_FIXTURES_DIR / name).read_text())


def _load_vnish(name: str) -> dict[str, Any]:
    return json.loads((VNISH_FIXTURES_DIR / name).read_text())


def _load_sealminer(name: str) -> dict[str, Any]:
    return json.loads((SEALMINER_FIXTURES_DIR / name).read_text())


def _load_whatsminer(name: str) -> dict[str, Any]:
    return json.loads((WHATSMINER_FIXTURES_DIR / name).read_text())


def _load_bitmain(name: str) -> dict[str, Any]:
    return json.loads((BITMAIN_FIXTURES_DIR / name).read_text())


@pytest.fixture()
def braiins_fixtures() -> dict[str, Any]:
    """All Braiins fixture data keyed by endpoint name."""
    return {
        "auth_login": _load_braiins("auth_login.json"),
        "cooling_state": _load_braiins("cooling_state.json"),
        "miner_stats": _load_braiins("miner_stats.json"),
        "miner_details": _load_braiins("miner_details.json"),
        "hashboards": _load_braiins("hashboards.json"),
        "miner_errors": _load_braiins("miner_errors.json"),
    }


@pytest.fixture()
def mock_braiins_api(braiins_fixtures) -> responses.RequestsMock:
    """Activate ``responses`` with all Braiins endpoints returning fixture data."""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(
            responses.POST,
            f"{MINER_URL}/api/v1/auth/login",
            json=braiins_fixtures["auth_login"],
            status=200,
        )
        rsps.add(
            responses.GET,
            f"{MINER_URL}/api/v1/cooling/state",
            json=braiins_fixtures["cooling_state"],
            status=200,
        )
        rsps.add(
            responses.GET,
            f"{MINER_URL}/api/v1/miner/stats",
            json=braiins_fixtures["miner_stats"],
            status=200,
        )
        rsps.add(
            responses.GET,
            f"{MINER_URL}/api/v1/miner/details",
            json=braiins_fixtures["miner_details"],
            status=200,
        )
        rsps.add(
            responses.GET,
            f"{MINER_URL}/api/v1/miner/hw/hashboards",
            json=braiins_fixtures["hashboards"],
            status=200,
        )
        rsps.add(
            responses.GET,
            f"{MINER_URL}/api/v1/miner/errors",
            json=braiins_fixtures["miner_errors"],
            status=200,
        )
        yield rsps


@pytest.fixture()
def braiins_collector():
    """Return an unauthenticated BraiinsCollector pointed at the test URL."""
    from wright_telemetry.collectors.braiins import BraiinsCollector
    collector = BraiinsCollector(url=MINER_URL, username="root", password="test123")
    yield collector
    collector.close()


@pytest.fixture()
def braiins_collector_no_auth():
    """Return a BraiinsCollector with no credentials."""
    from wright_telemetry.collectors.braiins import BraiinsCollector
    collector = BraiinsCollector(url=MINER_URL)
    yield collector
    collector.close()


# ---------------------------------------------------------------------------
# LuxOS fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def luxos_fixtures() -> dict[str, Any]:
    """All LuxOS fixture data keyed by CGMiner command name."""
    return {
        "config": _load_luxos("config.json"),
        "version": _load_luxos("version.json"),
        "summary": _load_luxos("summary.json"),
        "pools": _load_luxos("pools.json"),
        "power": _load_luxos("power.json"),
        "fans": _load_luxos("fans.json"),
        "temps": _load_luxos("temps.json"),
        "devs": _load_luxos("devs.json"),
        "events": _load_luxos("events.json"),
    }


@pytest.fixture()
def mock_luxos_api(luxos_fixtures):
    """Patch ``LuxOSCollector._send_command`` to return fixture data by command name."""
    def _fake_send(self, command, parameter=""):
        if command in luxos_fixtures:
            return luxos_fixtures[command]
        return {"STATUS": [{"STATUS": "E", "Msg": f"Unknown command: {command}"}]}

    with patch(
        "wright_telemetry.collectors.luxos.LuxOSCollector._send_command",
        _fake_send,
    ):
        yield


@pytest.fixture()
def luxos_collector():
    """Return a LuxOSCollector pointed at the test host."""
    from wright_telemetry.collectors.luxos import LuxOSCollector
    return LuxOSCollector(url=LUXOS_HOST)


# ---------------------------------------------------------------------------
# Vnish fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def vnish_fixtures() -> dict[str, Any]:
    """All Vnish fixture data keyed by endpoint name."""
    return {
        "unlock": _load_vnish("unlock.json"),
        "info": _load_vnish("info.json"),
        "summary": _load_vnish("summary.json"),
        "status": _load_vnish("status.json"),
    }


@pytest.fixture()
def mock_vnish_api(vnish_fixtures) -> responses.RequestsMock:
    """Activate ``responses`` with all Vnish endpoints returning fixture data."""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(
            responses.POST,
            f"{VNISH_URL}/api/v1/unlock",
            json=vnish_fixtures["unlock"],
            status=200,
        )
        rsps.add(
            responses.GET,
            f"{VNISH_URL}/api/v1/info",
            json=vnish_fixtures["info"],
            status=200,
        )
        rsps.add(
            responses.GET,
            f"{VNISH_URL}/api/v1/summary",
            json=vnish_fixtures["summary"],
            status=200,
        )
        rsps.add(
            responses.GET,
            f"{VNISH_URL}/api/v1/status",
            json=vnish_fixtures["status"],
            status=200,
        )
        yield rsps


@pytest.fixture()
def vnish_collector():
    """Return an unauthenticated VnishCollector pointed at the test URL."""
    from wright_telemetry.collectors.vnish import VnishCollector
    collector = VnishCollector(url=VNISH_URL, password="test123")
    yield collector
    collector.close()


@pytest.fixture()
def vnish_collector_no_auth():
    """Return a VnishCollector with no credentials."""
    from wright_telemetry.collectors.vnish import VnishCollector
    collector = VnishCollector(url=VNISH_URL)
    yield collector
    collector.close()


# ---------------------------------------------------------------------------
# Bitmain fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def bitmain_fixtures() -> dict[str, Any]:
    """All Bitmain fixture data keyed by CGI endpoint name."""
    return {
        "get_system_info": _load_bitmain("get_system_info.json"),
        "miner_type":      _load_bitmain("miner_type.json"),
        "stats":           _load_bitmain("stats.json"),
        "pools":           _load_bitmain("pools.json"),
        "warning":         _load_bitmain("warning.json"),
    }


@pytest.fixture()
def mock_bitmain_api(bitmain_fixtures) -> responses.RequestsMock:
    """Activate ``responses`` with all Bitmain CGI endpoints returning fixture data."""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(
            responses.GET,
            f"{BITMAIN_URL}/cgi-bin/get_system_info.cgi",
            json=bitmain_fixtures["get_system_info"],
            status=200,
        )
        rsps.add(
            responses.GET,
            f"{BITMAIN_URL}/cgi-bin/miner_type.cgi",
            json=bitmain_fixtures["miner_type"],
            status=200,
        )
        rsps.add(
            responses.GET,
            f"{BITMAIN_URL}/cgi-bin/stats.cgi",
            json=bitmain_fixtures["stats"],
            status=200,
        )
        rsps.add(
            responses.GET,
            f"{BITMAIN_URL}/cgi-bin/pools.cgi",
            json=bitmain_fixtures["pools"],
            status=200,
        )
        rsps.add(
            responses.GET,
            f"{BITMAIN_URL}/cgi-bin/warning.cgi",
            json=bitmain_fixtures["warning"],
            status=200,
        )
        yield rsps


@pytest.fixture()
def bitmain_collector():
    """Return a BitmainCollector pointed at the test URL."""
    from wright_telemetry.collectors.bitmain import BitmainCollector
    collector = BitmainCollector(url=BITMAIN_URL, username="root", password="root")
    yield collector
    collector.close()


@pytest.fixture()
def bitmain_collector_no_auth():
    """Return a BitmainCollector with no credentials (uses default root:root)."""
    from wright_telemetry.collectors.bitmain import BitmainCollector
    collector = BitmainCollector(url=BITMAIN_URL)
    yield collector
    collector.close()


# ---------------------------------------------------------------------------
# Sealminer fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sealminer_fixtures() -> dict[str, Any]:
    """All Sealminer fixture data keyed by CGMiner command name."""
    return {
        "version":    _load_sealminer("version.json"),
        "summary":    _load_sealminer("summary.json"),
        "pools":      _load_sealminer("pools.json"),
        "devdetails": _load_sealminer("devdetails.json"),
        "stats":      _load_sealminer("stats.json"),
    }


@pytest.fixture()
def mock_sealminer_api(sealminer_fixtures):
    """Patch ``SealminerCollector._send_command`` to return fixture data by command name."""
    def _fake_send(self, command, parameter=""):
        if command in sealminer_fixtures:
            return sealminer_fixtures[command]
        return {"STATUS": [{"STATUS": "E", "Msg": f"Unknown command: {command}"}]}

    with patch(
        "wright_telemetry.collectors.sealminer.SealminerCollector._send_command",
        _fake_send,
    ):
        yield


@pytest.fixture()
def sealminer_collector():
    """Return a SealminerCollector pointed at the test host."""
    from wright_telemetry.collectors.sealminer import SealminerCollector
    return SealminerCollector(url=SEALMINER_HOST)


# ---------------------------------------------------------------------------
# WhatsMiner fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def whatsminer_fixtures() -> dict[str, Any]:
    """All WhatsMiner fixture data keyed by btminer command name.

    Captured from a live M30SVE20 on firmware 20220422.18.REL (API 2.0.3).
    """
    return {
        "summary":        _load_whatsminer("summary.json"),
        "pools":          _load_whatsminer("pools.json"),
        "edevs":          _load_whatsminer("edevs.json"),
        "devs":           _load_whatsminer("devs.json"),
        "devdetails":     _load_whatsminer("devdetails.json"),
        "get_version":    _load_whatsminer("get_version.json"),
        "get_psu":        _load_whatsminer("get_psu.json"),
        "status":         _load_whatsminer("status.json"),
        "get_error_code": _load_whatsminer("get_error_code.json"),
        "get_miner_info": _load_whatsminer("get_miner_info.json"),
    }


@pytest.fixture()
def mock_whatsminer_api(whatsminer_fixtures):
    """Patch ``WhatsminerCollector._send_command`` to return fixture data by command."""
    def _fake_send(self, command, **params):
        if command in whatsminer_fixtures:
            return whatsminer_fixtures[command]
        # Mirrors the real adapter, which maps an "invalid cmd" reply to {}.
        return {}

    with patch(
        "wright_telemetry.collectors.whatsminer.WhatsminerCollector._send_command",
        _fake_send,
    ):
        yield


@pytest.fixture()
def whatsminer_collector():
    """Return a WhatsminerCollector pointed at the test host."""
    from wright_telemetry.collectors.whatsminer import WhatsminerCollector
    return WhatsminerCollector(url=WHATSMINER_HOST)
