"""Tests for remote config commands (mask, get, update) and config migration."""

import copy
import json
import pytest
from unittest.mock import patch, MagicMock

from wright_telemetry.config import mask_config, run_setup_wizard, SENSITIVE_MASK


SAMPLE_CONFIG = {
    "wright_api_key": "wfs_secret_key_123",
    "wright_api_url": "https://api.wrightfan.com/api",
    "facility_id": "FAC-001",
    "poll_interval_seconds": 30,
    "collector_type": "braiins",
    "miners": [
        {
            "name": "Miner 1",
            "url": "http://192.168.1.100",
            "username": "root",
            "password_b64": "c2VjcmV0",
        },
        {
            "name": "Miner 2",
            "url": "http://192.168.1.101",
            "username": "root",
        },
    ],
    "discovery": {
        "enabled": True,
        "subnets": ["192.168.1.0/24"],
        "scan_interval_seconds": 300,
        "default_username": "root",
        "default_password_b64": "ZGVmYXVsdF9wdw==",
    },
    "consent": {
        "cooling": True,
        "hashrate": True,
        "uptime": False,
        "hashboards": False,
        "errors": True,
        "remote_config": True,
    },
    "disable_auto_update": False,
}


class TestMaskConfig:
    def test_masks_api_key(self):
        masked = mask_config(SAMPLE_CONFIG)
        assert masked["wright_api_key"] == SENSITIVE_MASK

    def test_masks_miner_passwords(self):
        masked = mask_config(SAMPLE_CONFIG)
        assert masked["miners"][0]["password_b64"] == SENSITIVE_MASK
        assert "password_b64" not in masked["miners"][1]

    def test_masks_discovery_password(self):
        masked = mask_config(SAMPLE_CONFIG)
        assert masked["discovery"]["default_password_b64"] == SENSITIVE_MASK

    def test_does_not_mutate_original(self):
        original_key = SAMPLE_CONFIG["wright_api_key"]
        mask_config(SAMPLE_CONFIG)
        assert SAMPLE_CONFIG["wright_api_key"] == original_key

    def test_preserves_non_sensitive_fields(self):
        masked = mask_config(SAMPLE_CONFIG)
        assert masked["facility_id"] == "FAC-001"
        assert masked["poll_interval_seconds"] == 30
        assert masked["miners"][0]["url"] == "http://192.168.1.100"
        assert masked["discovery"]["subnets"] == ["192.168.1.0/24"]

    def test_handles_empty_config(self):
        masked = mask_config({})
        assert masked == {}

    def test_handles_missing_nested_fields(self):
        cfg = {"wright_api_key": "secret", "miners": []}
        masked = mask_config(cfg)
        assert masked["wright_api_key"] == SENSITIVE_MASK
        assert masked["miners"] == []


# ---------------------------------------------------------------
# Backward-compat: collector_type string → collector_types list
#
# Old installs stored a single string in ``collector_type``.  The
# wizard must read it, promote it to a list in ``collector_types``,
# and drop the old key so callers only ever see the new format.
# ---------------------------------------------------------------

def _wizard_answers(overrides: dict) -> dict:
    """Minimal set of _ask() return values to get through the wizard."""
    defaults = {
        "Wright Fan API Key": "key",
        "Wright Fan API URL": "https://api.wrightfan.com/api",
        "Facility ID": "fac-001",
        "Poll interval in seconds": "30",
        "Collector OS type(s)": overrides.pop("Collector OS type(s)", "braiins"),
        "Keep existing manual miners? (y/n)": "n",
        "\n  Scan your local network to discover miners automatically? (y/n)": "n",
        "Would you like to add miners manually? (y/n)": "n",
        "Enable automatic updates? (y/n)": "y",
    }
    defaults.update(overrides)
    return defaults


def _run_wizard(existing: dict, ask_map: dict) -> dict:
    """Run the wizard with mocked _ask / _ask_password / consent / save."""
    def fake_ask(prompt, default=""):
        for key, val in ask_map.items():
            if key in prompt:
                return val
        return default

    with (
        patch("wright_telemetry.config._ask", side_effect=fake_ask),
        patch("wright_telemetry.config._ask_password", return_value=""),
        patch("wright_telemetry.config.run_consent_wizard", return_value={}),
        patch("wright_telemetry.config.save_config"),
    ):
        return run_setup_wizard(existing)