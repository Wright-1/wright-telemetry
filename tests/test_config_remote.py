"""Tests for remote config commands (mask, get, update)."""

import copy
import json
import pytest
from unittest.mock import patch, MagicMock

from wright_telemetry.config import mask_config, SENSITIVE_MASK


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
