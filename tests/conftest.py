"""Shared pytest fixtures for miner API test simulators."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import responses

BRAIINS_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "braiins"
LUXOS_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "luxos"
VNISH_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "vnish"
MINER_URL = "http://192.168.1.100"
LUXOS_HOST = "192.168.1.200"
VNISH_URL = "http://192.168.1.150"


def _load_braiins(name: str) -> dict[str, Any]:
    return json.loads((BRAIINS_FIXTURES_DIR / name).read_text())


def _load_luxos(name: str) -> dict[str, Any]:
    return json.loads((LUXOS_FIXTURES_DIR / name).read_text())


def _load_vnish(name: str) -> dict[str, Any]:
    return json.loads((VNISH_FIXTURES_DIR / name).read_text())


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
    return BraiinsCollector(url=MINER_URL, username="root", password="test123")


@pytest.fixture()
def braiins_collector_no_auth():
    """Return a BraiinsCollector with no credentials."""
    from wright_telemetry.collectors.braiins import BraiinsCollector
    return BraiinsCollector(url=MINER_URL)


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
    return VnishCollector(url=VNISH_URL, password="test123")


@pytest.fixture()
def vnish_collector_no_auth():
    """Return a VnishCollector with no credentials."""
    from wright_telemetry.collectors.vnish import VnishCollector
    return VnishCollector(url=VNISH_URL)
