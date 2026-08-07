"""Telemetry API client — generic interface + Wright One implementation.

ApiClient
---------
Abstract base class.  Implement ``endpoint()`` to target any backend.

WrightAPIClient
---------------
Wright One implementation.  All routes default to the data pipeline
(``/api/v2/...`` via ``WRIGHT_INGEST_URL``).  Pass ``pipeline=False``
to route to the provisioning/portal API instead (``WRIGHT_API_URL``).
"""

from __future__ import annotations

import logging
import platform
import time
from abc import ABC, abstractmethod
from typing import Any

import requests

from wright_telemetry.encryption import encrypt_payload
from wright_telemetry.models import ScanSummaryData, TelemetryPayload

logger = logging.getLogger(__name__)

_POST_TIMEOUT = 20  # seconds
_BATCH_POST_TIMEOUT = 60  # seconds — one request carries up to _MAX_BATCH payloads
_MAX_BATCH = 500  # must not exceed the gateway's MAX_BATCH_PAYLOADS


# ---------------------------------------------------------------------------
# URL builder  (used by WrightAPIClient.endpoint and portal_client)
# ---------------------------------------------------------------------------


def wright_api_url(base: str, *path: str) -> str:
    """Build a Wright pipeline URL from an explicit base URL and path segments.

    ``base`` may or may not already end in ``/api``; this function handles
    both cases so callers never need to think about it.

    Examples::

        wright_api_url("https://api.wrightfan.com", "telemetry")
        # → "https://api.wrightfan.com/api/v2/telemetry"

        wright_api_url("https://api.wrightfan.com/api", "ws", "agent")
        # → "https://api.wrightfan.com/api/v2/ws/agent"
    """
    base = base.rstrip("/")
    tail = "/".join(path)
    if base.endswith("/api"):
        return f"{base}/v2/{tail}"
    return f"{base}/api/v2/{tail}"


def build_url(*path: str, pipeline: bool = True) -> str:
    """Build a full URL from path segments using the configured base URLs.

    Pipeline routes (default) resolve to ``{INGEST_URL}/api/v2/{path}``.
    Portal routes resolve to  ``{API_URL}/api/{path}``.

    Both base URLs may end in ``/api`` (e.g. ``https://api.wrightfan.com/api``);
    this function handles that so callers never need to think about it.
    """
    from wright_telemetry.settings import API_URL, INGEST_URL
    base = (INGEST_URL if pipeline else API_URL).rstrip("/")
    tail = "/".join(path)
    if pipeline:
        return f"{base}/v2/{tail}" if base.endswith("/api") else f"{base}/api/v2/{tail}"
    return f"{base}/{tail}" if base.endswith("/api") else f"{base}/api/{tail}"


# ---------------------------------------------------------------------------
# Generic interface
# ---------------------------------------------------------------------------

class ApiClient(ABC):
    """Generic telemetry API client.

    Subclass this and implement ``endpoint()`` to connect the collector
    to any backend.  ``WrightAPIClient`` is the built-in Wright One
    implementation.
    """

    @abstractmethod
    def endpoint(self, *path: str, pipeline: bool = True) -> str:
        """Return the full URL for the given path segments.

        Args:
            *path: URL path segments joined with ``/``.
            pipeline: When ``True`` (default) the URL targets the data
                pipeline (``/api/v2/``).  When ``False`` it targets the
                portal / provisioning API.
        """
        ...


# ---------------------------------------------------------------------------
# Wright One implementation
# ---------------------------------------------------------------------------

class WrightAPIClient(ApiClient):
    """Wright One telemetry client.

    Reads base URLs from the environment (``settings.py``) so no URL
    wiring is needed in the rest of the codebase.

    ``api_url`` is accepted for backwards compatibility but the settings
    values always take precedence.
    """

    def __init__(self, api_url: str, api_key: str, facility_id: str) -> None:
        from wright_telemetry.settings import API_URL, INGEST_URL
        # Portal / provisioning calls use api_url (falls back to WRIGHT_API_URL env).
        self._api_url    = api_url.rstrip("/") if api_url.strip() else API_URL.rstrip("/")
        # Telemetry pipeline always uses WRIGHT_INGEST_URL — independent of api_url.
        self._ingest_url = INGEST_URL.rstrip("/")
        self.api_key     = api_key
        self.facility_id = facility_id

        headers = {
            "Content-Type":  "application/json",
            "X-API-Key":     self.api_key,
            "X-Facility-ID": self.facility_id,
        }

        self._session = requests.Session()
        self._session.headers.update(headers)

        # Separate session for the methods the scheduler's uploader thread calls
        # (send_batch, send_scan_summary). send/send_agent_config stay on the main
        # thread using _session, so neither session is ever used concurrently —
        # requests.Session is not safe to share across threads.
        self._async_session = requests.Session()
        self._async_session.headers.update(headers)

    # ── ApiClient interface ──────────────────────────────────────────────────

    def endpoint(self, *path: str, pipeline: bool = True) -> str:
        base = (self._ingest_url if pipeline else self._api_url)
        tail = "/".join(path)
        if pipeline:
            return f"{base}/v2/{tail}" if base.endswith("/api") else f"{base}/api/v2/{tail}"
        return f"{base}/{tail}" if base.endswith("/api") else f"{base}/api/{tail}"

    # ── Public methods ───────────────────────────────────────────────────────

    def close(self) -> None:
        self._session.close()
        self._async_session.close()

    def send(self, payload: TelemetryPayload) -> bool:
        """Encrypt and POST a telemetry payload to the pipeline."""
        url = self.endpoint("telemetry")
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

    def send_batch(self, payloads: list[TelemetryPayload]) -> int:
        """Encrypt and POST many telemetry payloads in one request.

        Each payload is encrypted individually (same wire format as ``send``);
        batching only removes the per-payload HTTP round trip, which is what
        dominates the poll cycle at scale. Returns the number accepted.

        Sends in chunks of ``_MAX_BATCH`` to stay under the gateway's limit.
        A failed chunk is not retried — telemetry is periodic sampling, so a
        dropped payload costs one sample, and re-sending a partially-accepted
        chunk would double-insert the rest.
        """
        if not payloads:
            return 0

        url = self.endpoint("telemetry/batch")
        accepted = 0

        for start in range(0, len(payloads), _MAX_BATCH):
            chunk = payloads[start:start + _MAX_BATCH]

            # Encrypt per payload: a single unserialisable `data` field would
            # otherwise raise out of the whole chunk (json.dumps TypeError is not
            # a RequestException), costing 500 payloads instead of one.
            wire_payloads = []
            for p in chunk:
                try:
                    wire_payloads.append(encrypt_payload(p.to_dict(), self.api_key))
                except Exception as exc:
                    logger.warning(
                        "Skipping unencryptable %s payload for miner %s: %s",
                        p.metric_type,
                        p.miner_identity.hostname or p.miner_identity.uid,
                        exc,
                    )

            if not wire_payloads:
                continue

            try:
                resp = self._async_session.post(
                    url, json={"payloads": wire_payloads}, timeout=_BATCH_POST_TIMEOUT,
                )
                resp.raise_for_status()
                try:
                    accepted += int(resp.json().get("accepted", len(wire_payloads)))
                except (ValueError, AttributeError, TypeError):
                    accepted += len(wire_payloads)
            except requests.RequestException as exc:
                logger.warning(
                    "Failed to send telemetry batch (%d payload(s)): %s",
                    len(wire_payloads), exc,
                )

        if accepted < len(payloads):
            logger.warning(
                "Telemetry batch: %d/%d payload(s) accepted",
                accepted, len(payloads),
            )
        else:
            logger.info("Sent %d telemetry payload(s) in batch", accepted)
        return accepted

    def send_scan_summary(self, data: ScanSummaryData) -> bool:
        """Encrypt and POST a scan snapshot to the pipeline."""
        from dataclasses import asdict
        url = self.endpoint("telemetry/scan-summary")
        try:
            wire = encrypt_payload(asdict(data), self.api_key)
            resp = self._async_session.post(url, json=wire, timeout=_POST_TIMEOUT)
            resp.raise_for_status()
            logger.info(
                "Sent scan summary for facility %s: %d subnet(s), %d miner(s) (HTTP %d)",
                data.facility_id, len(data.subnets), data.total_miners, resp.status_code,
            )
            return True
        except requests.RequestException as exc:
            logger.warning("Failed to send scan summary: %s", exc)
            return False

    def send_agent_config(self, config: dict[str, Any], agent_version: str) -> bool:
        """POST an agent config snapshot to the portal (not the pipeline)."""
        url = self.endpoint("v1/telemetry/agent-config", pipeline=False)
        payload = {
            "facility_id": self.facility_id,
            "timestamp":   time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "data": {
                "config":        config,
                "agent_version": agent_version,
                "os":            platform.platform(),
                "time_running":  0,
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
