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
import urllib3

from wright_telemetry.encryption import encrypt_payload
from wright_telemetry.models import TelemetryPayload

logger = logging.getLogger(__name__)

_POST_TIMEOUT = 20  # seconds


# ---------------------------------------------------------------------------
# URL builder  (used by WrightAPIClient.endpoint and portal_client)
# ---------------------------------------------------------------------------

def wright_api_url(base: str, *path: str) -> str:
    """Build a versioned API URL from an explicit base URL and path segments.

    Normalises ``base`` so that whether or not it already ends in ``/api``
    the result is always ``{scheme+host}/api/v2/{path}``.

    Examples::

        wright_api_url("https://api.wrightfan.com", "telemetry")
        # → "https://api.wrightfan.com/api/v2/telemetry"

        wright_api_url("https://api.wrightfan.com/api", "ws", "agent")
        # → "https://api.wrightfan.com/api/v2/ws/agent"
    """
    base = base.rstrip("/")
    if base.endswith("/api"):
        base = base[:-4]  # strip trailing /api so we can re-add it cleanly
    tail = "/".join(path)
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
        # URLs are resolved from settings at instantiation time;
        # api_url param is kept for backwards compatibility only.
        _ = api_url  # noqa: F841
        self.api_key     = api_key
        self.facility_id = facility_id

        self._session = requests.Session()
        # TODO: re-enable TLS verification before shipping production builds
        self._session.verify = False
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self._session.headers.update({
            "Content-Type":  "application/json",
            "X-API-Key":     self.api_key,
            "X-Facility-ID": self.facility_id,
        })

    # ── ApiClient interface ──────────────────────────────────────────────────

    def endpoint(self, *path: str, pipeline: bool = True) -> str:
        return build_url(*path, pipeline=pipeline)

    # ── Public methods ───────────────────────────────────────────────────────

    def close(self) -> None:
        self._session.close()

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
