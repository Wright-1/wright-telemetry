"""Shared pytest fixtures for the Braiins API test simulator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import responses

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "braiins"
MINER_URL = "http://192.168.1.100"


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / name).read_text())


@pytest.fixture()
def braiins_fixtures() -> dict[str, Any]:
    """All Braiins fixture data keyed by endpoint name."""
    return {
        "auth_login": _load("auth_login.json"),
        "cooling_state": _load("cooling_state.json"),
        "miner_stats": _load("miner_stats.json"),
        "miner_details": _load("miner_details.json"),
        "hashboards": _load("hashboards.json"),
        "miner_errors": _load("miner_errors.json"),
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
