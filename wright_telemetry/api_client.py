"""Wright Fan API HTTP client.

Encrypts telemetry payloads with AES-256-GCM and POSTs them to the Wright
Fan cloud API.  Failures are logged but never bubble up to crash the
collector loop.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import requests
import urllib3

from wright_telemetry.encryption import encrypt_payload
from wright_telemetry.models import TelemetryPayload

logger = logging.getLogger(__name__)

_POST_TIMEOUT = 20  # seconds


def wright_api_v1_url(api_url: str, *segments: str) -> str:
    """Build a URL under ``/api/v1/...`` from the configured Wright API base.

    The setup wizard often stores the mount point explicitly, e.g.
    ``https://api.wrightfan.com/api`` or ``https://dev.wrightfan.com/api``.
    In that case paths are appended as ``/v1/<segments>`` only.

    If the base is the host root (no trailing ``/api``), ``/api/v1/<segments>``
    is appended.
    """
    base = (api_url or "").strip().rstrip("/")
    tail = "/".join(segments)
    if base.endswith("/api"):
        return f"{base}/v1/{tail}"
    return f"{base}/api/v1/{tail}"


class WrightAPIClient:
    """Thin wrapper around the Wright Fan telemetry ingest endpoint."""

    def __init__(self, api_url: str, api_key: str, facility_id: str):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.facility_id = facility_id
        self._session = requests.Session()
        # TODO: Re-enable TLS verification before shipping production builds.
        self._session.verify = False
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self._session.headers.update({
            "Content-Type": "application/json",
            "X-API-Key": self.api_key,
            "X-Facility-ID": self.facility_id,
        })

    def close(self) -> None:
        self._session.close()


    
    def send_agent_config(self, config: dict[str, Any], agent_version: str) -> bool:
        """POST agent config snapshot via the encrypted /v1/telemetry endpoint."""
        import platform
        import time
        url = wright_api_v1_url(self.api_url, "telemetry")
        payload = {
            "facility_id": self.facility_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "metric_type": "agent_config",
            "data": {
                "config": config,
                "agent_version": agent_version,
                "os": platform.platform(),
                "time_running": 0,
            },
        }
        try:
            wire = encrypt_payload(payload, self.api_key)
            resp = self._session.post(url, json=wire, timeout=_POST_TIMEOUT)
            resp.raise_for_status()
            logger.info("Sent agent config snapshot (HTTP %d)", resp.status_code)
            return True
        except Exception as exc:
            logger.warning("Failed to send agent config snapshot: %s", exc)
            return False

    def send(self, payload: TelemetryPayload) -> bool:
        """Encrypt and POST a telemetry payload.  Returns True on success."""
        url = wright_api_v1_url(self.api_url, "telemetry")
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
