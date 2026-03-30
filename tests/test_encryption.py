"""Encryption round-trip and tamper detection tests."""

from __future__ import annotations

import base64

import pytest

from wright_telemetry.encryption import decrypt_payload, derive_key, encrypt_payload


class TestEncryptDecryptRoundTrip:

    def test_basic_round_trip(self):
        original = {"metric_type": "cooling", "data": {"fans": [1, 2, 3]}}
        wire = encrypt_payload(original, "my-api-key")
        recovered = decrypt_payload(wire, "my-api-key")
        assert recovered == original

    def test_round_trip_nested_data(self):
        original = {
            "metric_type": "hashboards",
            "data": {
                "boards": [
                    {"id": 0, "temp": 72.5, "enabled": True},
                    {"id": 1, "temp": None, "enabled": False},
                ],
            },
        }
        wire = encrypt_payload(original, "key-123")
        recovered = decrypt_payload(wire, "key-123")
        assert recovered == original

    def test_different_keys_produce_different_ciphertext(self):
        data = {"value": 42}
        wire_a = encrypt_payload(data, "key-a")
        wire_b = encrypt_payload(data, "key-b")
        assert wire_a["ciphertext"] != wire_b["ciphertext"]

    def test_wrong_key_raises(self):
        data = {"value": 42}
        wire = encrypt_payload(data, "correct-key")
        with pytest.raises(Exception):
            decrypt_payload(wire, "wrong-key")

    def test_tampered_ciphertext_raises(self):
        data = {"value": 42}
        wire = encrypt_payload(data, "my-key")
        raw_ct = base64.b64decode(wire["ciphertext"])
        tampered = bytes([raw_ct[0] ^ 0xFF]) + raw_ct[1:]
        wire["ciphertext"] = base64.b64encode(tampered).decode()
        with pytest.raises(Exception):
            decrypt_payload(wire, "my-key")


class TestDeriveKey:

    def test_deterministic(self):
        k1 = derive_key("test-key")
        k2 = derive_key("test-key")
        assert k1 == k2

    def test_different_inputs(self):
        k1 = derive_key("key-a")
        k2 = derive_key("key-b")
        assert k1 != k2

    def test_key_length(self):
        k = derive_key("any-key")
        assert len(k) == 32
