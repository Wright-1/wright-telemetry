"""Braiins OS REST API collector adapter.

Endpoints used (Braiins OS Public REST API v1.2.0):
    GET /api/v1/cooling/state        -> CoolingData
    GET /api/v1/miner/stats          -> HashrateData
    GET /api/v1/miner/details        -> UptimeData + MinerIdentity
    GET /api/v1/miner/hw/hashboards  -> HashboardData
    GET /api/v1/miner/errors         -> ErrorData
"""

from __future__ import annotations

import json
import logging
from typing import Optional
from urllib.parse import urlparse

import requests

from wright_telemetry.collectors.base import MinerCollector
from wright_telemetry.collectors.factory import CollectorFactory
from wright_telemetry.models import (
    CoolingData,
    ErrorData,
    HashboardData,
    HashrateData,
    MinerIdentity,
    UptimeData,
)

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 15  # seconds


@CollectorFactory.register("braiins")
class BraiinsCollector(MinerCollector):
    """Adapter for miners running Braiins OS / BOS+."""

    def __init__(self, url: str, username: Optional[str] = None, password: Optional[str] = None):
        super().__init__(url, username, password)
        self._session = requests.Session()
        self._token: Optional[str] = None
        # Avoid requests' default Accept-Encoding (gzip, deflate, br). Some
        # embedded Braiins/stacks return compressed responses that decode to an
        # empty body in urllib3, which then fails JSON parsing — while stdlib
        # urllib (no br/gzip) receives plain JSON (matches scripts/probe_braiins_http.py).
        self._session.headers["Accept-Encoding"] = "identity"
        self._session.headers.setdefault("Accept", "application/json")
        self._session.headers.setdefault("User-Agent", "WrightTelemetry/braiins-collector")

    def close(self) -> None:
        self._session.close()

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def authenticate(self) -> None:
        if not self.username:
            logger.debug("No Braiins credentials configured -- skipping auth for %s", self.url)
            return

        login_url = f"{self.url}/api/v1/auth/login"
        payload = {"username": self.username, "password": self.password or ""}
        try:
            # Clear any stale token before logging in so it doesn't ride along
            # on the login request itself and confuse the miner's session logic.
            self._session.headers.pop("authorization", None)
            resp = self._session.post(login_url, json=payload, timeout=_REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = self._json_from_response(resp, login_url)
            self._token = data.get("token") or data.get("access_token")
            if self._token:
                self._session.headers["authorization"] = self._token
                logger.info("Authenticated with Braiins miner at %s (token timeout: %ss)",
                            self.url, data.get("timeout_s", "?"))
            else:
                logger.warning("Auth response missing token for %s -- body: %s -- continuing without auth",
                               self.url, data)
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Auth failed for %s (%s) -- will try requests without auth", self.url, exc)

    def _json_from_response(self, resp: requests.Response, url: str) -> dict:
        """Decode JSON, accepting UTF-8 BOM; raise with useful logs if the body is wrong."""
        raw = resp.content or b""
        text = raw.decode("utf-8-sig").strip()
        if not text:
            logger.warning(
                "Empty response body from %s (HTTP %s, Content-Type=%r, len=%d)",
                url,
                resp.status_code,
                resp.headers.get("Content-Type"),
                len(raw),
            )
            raise ValueError(f"Empty response body from {url}")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            preview = text[:400].replace("\n", " ")
            logger.warning(
                "Non-JSON response from %s (HTTP %s): %s",
                url,
                resp.status_code,
                preview,
            )
            raise

    def _maybe_upgrade_to_https_after_redirect(self, resp: requests.Response) -> bool:
        """If the miner redirected HTTP → HTTPS, follow it on our base URL and re-login.

        ``requests`` may drop the ``Authorization`` header on a cross-scheme redirect,
        which often produces empty or HTML responses and JSON decode failures.
        """
        if not resp.history or not self.url.startswith("http://"):
            return False
        final = urlparse(resp.url)
        if final.scheme != "https":
            return False
        new_base = f"{final.scheme}://{final.netloc}".rstrip("/")
        if new_base == self.url:
            return False
        logger.info(
            "Braiins miner redirected %s → %s; switching base URL and re-authenticating",
            self.url,
            new_base,
        )
        self.url = new_base
        self.authenticate()
        return True

    def _get(self, path: str) -> dict:
        """Issue a GET request with automatic 401 retry."""
        url = f"{self.url}{path}"
        resp = self._session.get(url, timeout=_REQUEST_TIMEOUT)

        if self._maybe_upgrade_to_https_after_redirect(resp):
            url = f"{self.url}{path}"
            resp = self._session.get(url, timeout=_REQUEST_TIMEOUT)

        if resp.status_code == 401 and self.username:
            logger.info("Got 401 from %s -- re-authenticating", url)
            self.authenticate()
            resp = self._session.get(url, timeout=_REQUEST_TIMEOUT)

        resp.raise_for_status()
        return self._json_from_response(resp, url)

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    def fetch_identity(self) -> MinerIdentity:
        raw = self._get("/api/v1/miner/details")
        return MinerIdentity(
            uid=raw.get("uid", ""),
            serial_number=raw.get("serial_number", ""),
            hostname=raw.get("hostname", ""),
            mac_address=raw.get("mac_address", ""),
        )

    # ------------------------------------------------------------------
    # Metric fetchers
    # ------------------------------------------------------------------

    def fetch_cooling(self) -> CoolingData:
        raw = self._get("/api/v1/cooling/state")
        return CoolingData.from_braiins(raw)

    def fetch_hashrate(self) -> HashrateData:
        raw = self._get("/api/v1/miner/stats")
        return HashrateData.from_braiins(raw)

    def fetch_uptime(self) -> UptimeData:
        raw = self._get("/api/v1/miner/details")
        return UptimeData.from_braiins(raw)

    def fetch_hashboards(self) -> HashboardData:
        raw = self._get("/api/v1/miner/hw/hashboards")
        return HashboardData.from_braiins(raw)

    def fetch_errors(self) -> ErrorData:
        raw = self._get("/api/v1/miner/errors")
        return ErrorData.from_braiins(raw)
