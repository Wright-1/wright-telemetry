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
from wright_telemetry.mac_util import normalize_mac_address
from wright_telemetry.models import MinerIdentity, TelemetryPayload

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

    def mark_stock_fans(
        self,
        mac_address: str,
        fan_baselines: list[dict],
        detected_at: str,
    ) -> bool:
        """POST to /api/v1/miners/stock-fans to record baseline RPMs and mark as stock."""
        url = wright_api_v1_url(self.api_url, "miners", "stock-fans")
        mac = normalize_mac_address(mac_address) or (mac_address or "").strip()
        payload = {
            "mac_address": mac,
            "fan_baselines": fan_baselines,
            "detected_at": detected_at,
            "facility_id": self.facility_id,
        }
        try:
            wire = encrypt_payload(payload, self.api_key)
            resp = self._session.post(url, json=wire, timeout=_POST_TIMEOUT)
            resp.raise_for_status()
            logger.info(
                "Marked stock fans for miner %s (HTTP %d)",
                mac, resp.status_code,
            )
            return True
        except Exception as exc:
            logger.warning("Failed to mark stock fans for miner %s: %s", mac, exc)
            return False

    def mark_wright_fans(
        self,
        mac_address: str,
        fan_positions: list[int],
        detected_at: str,
    ) -> bool:
        """POST to /v1/miners/wright-fans to mark fans as Wright fans by MAC address."""
        url = wright_api_v1_url(self.api_url, "miners", "wright-fans")
        mac = normalize_mac_address(mac_address) or (mac_address or "").strip()
        payload = {
            "mac_address": mac,
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
                len(fan_positions), mac, resp.status_code,
            )
            return True
        except Exception as exc:
            logger.warning("Failed to mark Wright fans for miner %s: %s", mac, exc)
            return False

    def register_miner(
        self,
        identity: MinerIdentity,
        miner_cfg: dict[str, Any],
    ) -> bool:
        """Upsert a miner record in the API miners table.

        POST /telemetry/{facilityId}/miners — creates or updates the row keyed
        on (facilityId, minerUid).  Skips miners whose uid is still 'unknown'.
        """
        if not identity.uid or identity.uid == "unknown":
            return False
        url = wright_api_v1_url(self.api_url, "telemetry", self.facility_id, "miners")
        mac_raw = identity.mac_address or ""
        mac = normalize_mac_address(mac_raw) or mac_raw.strip() or None
        payload: dict[str, Any] = {
            "minerUid": identity.uid,
            "minerMac": mac,
            "minerIp": identity.ip_address or None,
            "minerHostname": identity.hostname or None,
            "minerSerial": identity.serial_number if identity.serial_number != "unknown" else None,
            "wrightFans": miner_cfg.get("wright_fans"),
            "os": miner_cfg.get("firmware"),
        }
        try:
            resp = self._session.post(url, json=payload, timeout=_POST_TIMEOUT)
            resp.raise_for_status()
            logger.info(
                "Registered miner uid=%s mac=%s (HTTP %d)",
                identity.uid, mac, resp.status_code,
            )
            return True
        except Exception as exc:
            logger.warning("Failed to register miner uid=%s: %s", identity.uid, exc)
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
