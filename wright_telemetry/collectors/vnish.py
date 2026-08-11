"""Vnish firmware REST API collector adapter.

Vnish exposes a REST API at ``/api/v1/<command>`` on port 80.
Authentication is token-based via ``POST /api/v1/unlock``.

Endpoints used:
    POST /api/v1/unlock    -> authentication token
    GET  /api/v1/info      -> MinerIdentity (hostname, MAC, serial, model, firmware)
    GET  /api/v1/summary   -> everything else: hashrate, pools, power, uptime,
                              fans (``miner.cooling.fans``), hashboards
                              (``miner.chains``) and inferred errors
    GET  /api/v1/status    -> miner state flags only; used as an uptime fallback

``/api/v1/status`` returns nothing but state flags on this firmware
(``miner_state``, ``find_miner``, ``reboot_required``) -- fans and chains are
in the summary response, not there. Verified against an Antminer S21 running
Vnish 1.2.6-rc5; see ``tests/fixtures/vnish/README.md``.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

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


@CollectorFactory.register("vnish")
class VnishCollector(MinerCollector):
    """Adapter for miners running Vnish firmware."""

    def __init__(self, url: str, username: Optional[str] = None, password: Optional[str] = None):
        super().__init__(url, username, password)
        self._session = requests.Session()
        self._token: Optional[str] = None

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._session.close()

    def authenticate(self) -> None:
        if not self.password:
            logger.debug("No Vnish password configured -- skipping auth for %s", self.url)
            return

        unlock_url = f"{self.url}/api/v1/unlock"
        payload = {"pw": self.password}
        try:
            self._session.headers.pop("Authorization", None)
            resp = self._session.post(unlock_url, json=payload, timeout=_REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            self._token = data.get("token")
            if self._token:
                self._session.headers["Authorization"] = self._token
                logger.info("Authenticated with Vnish miner at %s", self.url)
            else:
                logger.warning("Auth response missing token for %s -- continuing without auth",
                               self.url)
        except requests.RequestException as exc:
            logger.warning("Auth failed for %s (%s) -- will try requests without auth", self.url, exc)

    def _get(self, path: str) -> dict[str, Any]:
        """Issue a GET request with automatic 401 retry."""
        url = f"{self.url}{path}"
        resp = self._session.get(url, timeout=_REQUEST_TIMEOUT)

        if resp.status_code == 401 and self.password:
            logger.info("Got 401 from %s -- re-authenticating", url)
            self.authenticate()
            resp = self._session.get(url, timeout=_REQUEST_TIMEOUT)

        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    def fetch_identity(self) -> MinerIdentity:
        raw = self._get("/api/v1/info")
        network = raw.get("system", {}).get("network_status", {})
        mac = network.get("mac", "")

        # Vnish reports serial "N/A" on hardware whose EEPROM it can't read
        # (every S21 seen so far), so the MAC is the only stable identifier.
        serial = raw.get("serial", "")
        if serial.strip().upper() in ("", "N/A", "NA", "UNKNOWN"):
            serial = ""
        uid = raw.get("uid") or serial or mac

        return MinerIdentity(
            uid=uid,
            serial_number=serial,
            hostname=network.get("hostname", ""),
            mac_address=mac,
            # "miner" is the display name ("Antminer S21"); "model" is the
            # slug ("s21").
            model=raw.get("miner") or raw.get("model", ""),
            firmware="vnish",
            ip_address=network.get("ip", ""),
        )

    # ------------------------------------------------------------------
    # Metric fetchers
    # ------------------------------------------------------------------

    def fetch_cooling(self) -> CoolingData:
        raw = self._get("/api/v1/summary")
        return CoolingData.from_vnish(raw)

    def fetch_hashrate(self) -> HashrateData:
        raw = self._get("/api/v1/summary")
        return HashrateData.from_vnish(raw)

    def fetch_uptime(self) -> UptimeData:
        info_raw = self._get("/api/v1/info")
        summary_raw = self._get("/api/v1/summary")
        return UptimeData.from_vnish(info_raw, summary_raw)

    def fetch_hashboards(self) -> HashboardData:
        raw = self._get("/api/v1/summary")
        return HashboardData.from_vnish(raw)

    def fetch_errors(self) -> ErrorData:
        raw = self._get("/api/v1/summary")
        return ErrorData.from_vnish(raw)
