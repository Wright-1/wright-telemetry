"""Wright Fan API HTTP client.

Encrypts telemetry payloads with AES-256-GCM and POSTs them to the Wright
Fan cloud API.  Failures are logged but never bubble up to crash the
collector loop.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

import requests

from wright_telemetry.encryption import encrypt_payload
from wright_telemetry.models import TelemetryPayload

logger = logging.getLogger(__name__)

_POST_TIMEOUT = 20  # seconds


class WrightAPIClient:
    """Thin wrapper around the Wright Fan telemetry ingest endpoint."""

    def __init__(self, api_url: str, api_key: str, facility_id: str):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.facility_id = facility_id
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "X-API-Key": self.api_key,
            "X-Facility-ID": self.facility_id,
        })

    def mark_wright_fans(
        self,
        mac_address: str,
        fan_positions: list[int],
        detected_at: str,
    ) -> bool:
        """POST to /v1/miners/wright-fans to mark fans as Wright fans by MAC address."""
        from wright_telemetry.encryption import encrypt_payload
        url = f"{self.api_url}/v1/miners/wright-fans"
        payload = {
            "mac_address": mac_address,
            "fan_positions": fan_positions,
            "detected_at": detected_at,
            "facility_id": self.facility_id,
        }
        try:
            wire = encrypt_payload(payload, self.api_key)
            resp = self._session.post(url, json=wire, timeout=_POST_TIMEOUT)
            resp.raise_for_status()
            logger.info(
                "Marked %d Wright fans for miner %s (HTTP %d)",
                len(fan_positions), mac_address, resp.status_code,
            )
            return True
        except Exception as exc:
            logger.warning("Failed to mark Wright fans for miner %s: %s", mac_address, exc)
            return False

    def send(self, payload: TelemetryPayload) -> bool:
        """Encrypt and POST a telemetry payload.  Returns True on success."""
        url = f"{self.api_url}/api/v1/telemetry"
        try:
            wire = encrypt_payload(payload.to_dict(), self.api_key)
            resp = self._session.post(url, json=wire, timeout=_POST_TIMEOUT)
            resp.raise_for_status()
            logger.info(
                "Sent %s metric for miner %s (HTTP %d)",
                payload.metric_type,
                payload.miner_identity.hostname or payload.miner_identity.uid,
                resp.status_code,
            )
            return True
        except requests.RequestException as exc:
            logger.warning(
                "Failed to send %s metric for miner %s: %s",
                payload.metric_type,
                payload.miner_identity.hostname or payload.miner_identity.uid,
                exc,
            )
            return False
