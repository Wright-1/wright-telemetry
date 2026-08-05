"""Tests for WrightAPIClient.send()."""

from __future__ import annotations

import responses
import pytest

from wright_telemetry.api_client import WrightAPIClient, wright_api_url
from wright_telemetry.models import MinerIdentity, TelemetryPayload


API_URL = "https://api.wrightfan.com"
API_URL_WITH_API_SUFFIX = "https://api.wrightfan.com/api"
TELEMETRY_URL = wright_api_url(API_URL, "telemetry")
API_KEY = "test-api-key-12345"
FACILITY_ID = "fac-001"


class TestWrightApiUrl:

    def test_host_root_appends_api_v2(self) -> None:
        assert wright_api_url(
            "https://api.wrightfan.com", "telemetry",
        ) == "https://api.wrightfan.com/api/v2/telemetry"
        assert wright_api_url(
            "https://api.wrightfan.com", "ws", "agent",
        ) == "https://api.wrightfan.com/api/v2/ws/agent"

    def test_explicit_api_mount(self) -> None:
        assert wright_api_url(
            API_URL_WITH_API_SUFFIX, "telemetry",
        ) == "https://api.wrightfan.com/api/v2/telemetry"
        assert wright_api_url(
            "https://dev.wrightfan.com/api/", "ws", "agent",
        ) == "https://dev.wrightfan.com/api/v2/ws/agent"


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


BATCH_URL = wright_api_url(API_URL, "telemetry/batch")


class TestSendBatch:

    def test_empty_returns_zero(self, api_client):
        assert api_client.send_batch([]) == 0

    @responses.activate
    def test_single_request_for_many_payloads(self, api_client, sample_payload):
        """The whole point: N payloads cost one round trip, not N."""
        responses.add(
            responses.POST, BATCH_URL,
            json={"success": True, "accepted": 5, "failed": 0}, status=200,
        )
        assert api_client.send_batch([sample_payload] * 5) == 5
        assert len(responses.calls) == 1

    @responses.activate
    def test_payloads_are_encrypted(self, api_client, sample_payload):
        responses.add(
            responses.POST, BATCH_URL,
            json={"success": True, "accepted": 2, "failed": 0}, status=200,
        )
        api_client.send_batch([sample_payload] * 2)
        import json
        body = json.loads(responses.calls[0].request.body)
        assert len(body["payloads"]) == 2
        for wire in body["payloads"]:
            assert "nonce" in wire and "ciphertext" in wire
            assert "metric_type" not in wire

    @responses.activate
    def test_chunks_above_max_batch(self, api_client, sample_payload):
        from wright_telemetry.api_client import _MAX_BATCH
        responses.add(
            responses.POST, BATCH_URL,
            json={"success": True, "accepted": _MAX_BATCH, "failed": 0}, status=200,
        )
        n = _MAX_BATCH + 10
        api_client.send_batch([sample_payload] * n)
        assert len(responses.calls) == 2
        import json
        assert len(json.loads(responses.calls[0].request.body)["payloads"]) == _MAX_BATCH
        assert len(json.loads(responses.calls[1].request.body)["payloads"]) == 10

    @responses.activate
    def test_failed_chunk_is_not_retried(self, api_client, sample_payload):
        responses.add(responses.POST, BATCH_URL, json={"error": "boom"}, status=503)
        assert api_client.send_batch([sample_payload] * 3) == 0
        assert len(responses.calls) == 1

    @responses.activate
    def test_reports_partial_acceptance(self, api_client, sample_payload):
        responses.add(
            responses.POST, BATCH_URL,
            json={"success": True, "accepted": 2, "failed": 1}, status=200,
        )
        assert api_client.send_batch([sample_payload] * 3) == 2
