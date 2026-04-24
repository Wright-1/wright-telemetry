"""Tests for WrightAPIClient.send() and register_miner()."""

from __future__ import annotations

import responses
import pytest

from wright_telemetry.api_client import WrightAPIClient, wright_api_v1_url
from wright_telemetry.models import MinerIdentity, TelemetryPayload


API_URL = "https://api.wrightfan.com"
TELEMETRY_URL = wright_api_v1_url(API_URL, "telemetry")
API_URL_WITH_API_SUFFIX = "https://api.wrightfan.com/api"
API_KEY = "test-api-key-12345"
FACILITY_ID = "fac-001"


class TestWrightApiV1Url:

    def test_host_root_appends_api_v1(self) -> None:
        assert wright_api_v1_url(
            "https://api.wrightfan.com", "telemetry",
        ) == "https://api.wrightfan.com/api/v1/telemetry"
        assert wright_api_v1_url(
            "https://api.wrightfan.com", "ws", "agent",
        ) == "https://api.wrightfan.com/api/v1/ws/agent"

    def test_explicit_api_mount(self) -> None:
        assert wright_api_v1_url(
            API_URL_WITH_API_SUFFIX, "telemetry",
        ) == "https://api.wrightfan.com/api/v1/telemetry"
        assert wright_api_v1_url(
            "https://dev.wrightfan.com/api/", "ws", "agent",
        ) == "https://dev.wrightfan.com/api/v1/ws/agent"


@pytest.fixture()
def api_client():
    return WrightAPIClient(api_url=API_URL, api_key=API_KEY, facility_id=FACILITY_ID)


@pytest.fixture()
def sample_payload():
    mi = MinerIdentity(uid="u1", serial_number="sn1", hostname="h1", mac_address="m1")
    return TelemetryPayload(
        metric_type="cooling",
        facility_id=FACILITY_ID,
        miner_identity=mi,
        data={"fans": [{"rpm": 4200}]},
    )


class TestSend:

    @responses.activate
    def test_successful_post(self, api_client, sample_payload):
        responses.add(
            responses.POST,
            TELEMETRY_URL,
            json={"status": "ok"},
            status=200,
        )
        assert api_client.send(sample_payload) is True
        assert len(responses.calls) == 1

    @responses.activate
    def test_payload_is_encrypted(self, api_client, sample_payload):
        """The POST body must contain nonce+ciphertext, not plaintext fields."""
        responses.add(
            responses.POST,
            TELEMETRY_URL,
            json={"status": "ok"},
            status=200,
        )
        api_client.send(sample_payload)
        import json
        body = json.loads(responses.calls[0].request.body)
        assert "nonce" in body
        assert "ciphertext" in body
        assert "metric_type" not in body

    @responses.activate
    def test_http_error_returns_false(self, api_client, sample_payload):
        responses.add(
            responses.POST,
            TELEMETRY_URL,
            json={"error": "bad request"},
            status=400,
        )
        assert api_client.send(sample_payload) is False

    @responses.activate
    def test_server_error_returns_false(self, api_client, sample_payload):
        responses.add(
            responses.POST,
            TELEMETRY_URL,
            json={"error": "internal"},
            status=500,
        )
        assert api_client.send(sample_payload) is False

    @responses.activate
    def test_connection_error_returns_false(self, api_client, sample_payload):
        import requests as req
        responses.add(
            responses.POST,
            TELEMETRY_URL,
            body=req.ConnectionError("refused"),
        )
        assert api_client.send(sample_payload) is False

    @responses.activate
    def test_headers_set(self, api_client, sample_payload):
        responses.add(
            responses.POST,
            TELEMETRY_URL,
            json={"status": "ok"},
            status=200,
        )
        api_client.send(sample_payload)
        req = responses.calls[0].request
        assert req.headers["X-API-Key"] == API_KEY
        assert req.headers["X-Facility-ID"] == FACILITY_ID


REGISTER_URL = wright_api_v1_url(API_URL, "telemetry", FACILITY_ID, "miners")


@pytest.fixture()
def full_identity():
    return MinerIdentity(
        uid="a1b2c3d4e5f6",
        serial_number="BHB42391AX0027",
        hostname="antminer-rack3-slot7",
        mac_address="AA:BB:CC:DD:EE:F1",
        ip_address="192.168.1.100",
        wright_fans=True,
    )


@pytest.fixture()
def miner_cfg():
    return {"firmware": "braiins", "wright_fans": True}


class TestRegisterMiner:

    @responses.activate
    def test_successful_post(self, api_client, full_identity, miner_cfg):
        responses.add(responses.POST, REGISTER_URL, json={"status": "ok"}, status=201)
        assert api_client.register_miner(full_identity, miner_cfg) is True
        assert len(responses.calls) == 1

    @responses.activate
    def test_payload_fields(self, api_client, full_identity, miner_cfg):
        responses.add(responses.POST, REGISTER_URL, json={}, status=201)
        api_client.register_miner(full_identity, miner_cfg)
        import json as _json
        body = _json.loads(responses.calls[0].request.body)
        assert body["minerUid"] == "a1b2c3d4e5f6"
        assert body["minerMac"].lower() == "aa:bb:cc:dd:ee:f1"
        assert body["minerIp"] == "192.168.1.100"
        assert body["minerHostname"] == "antminer-rack3-slot7"
        assert body["minerSerial"] == "BHB42391AX0027"
        assert body["wrightFans"] is True
        assert body["os"] == "braiins"

    @responses.activate
    def test_payload_not_encrypted(self, api_client, full_identity, miner_cfg):
        responses.add(responses.POST, REGISTER_URL, json={}, status=201)
        api_client.register_miner(full_identity, miner_cfg)
        import json as _json
        body = _json.loads(responses.calls[0].request.body)
        assert "nonce" not in body
        assert "ciphertext" not in body

    @responses.activate
    def test_unknown_serial_sent_as_none(self, api_client, miner_cfg):
        identity = MinerIdentity(
            uid="abc123", serial_number="unknown",
            hostname="miner1", mac_address="AA:BB:CC:DD:EE:FF",
        )
        responses.add(responses.POST, REGISTER_URL, json={}, status=201)
        api_client.register_miner(identity, miner_cfg)
        import json as _json
        body = _json.loads(responses.calls[0].request.body)
        assert body["minerSerial"] is None

    def test_skips_unknown_uid(self, api_client, miner_cfg):
        identity = MinerIdentity(
            uid="unknown", serial_number="SN1",
            hostname="miner1", mac_address="AA:BB:CC:DD:EE:FF",
        )
        assert api_client.register_miner(identity, miner_cfg) is False

    def test_skips_empty_uid(self, api_client, miner_cfg):
        identity = MinerIdentity(
            uid="", serial_number="SN1",
            hostname="miner1", mac_address="AA:BB:CC:DD:EE:FF",
        )
        assert api_client.register_miner(identity, miner_cfg) is False

    @responses.activate
    def test_http_error_returns_false(self, api_client, full_identity, miner_cfg):
        responses.add(responses.POST, REGISTER_URL, json={"error": "bad"}, status=400)
        assert api_client.register_miner(full_identity, miner_cfg) is False

    @responses.activate
    def test_server_error_returns_false(self, api_client, full_identity, miner_cfg):
        responses.add(responses.POST, REGISTER_URL, json={"error": "internal"}, status=500)
        assert api_client.register_miner(full_identity, miner_cfg) is False
