"""Bitmain Antminer CGI REST API collector adapter.

Antminer firmware exposes a CGI-based HTTP API authenticated with HTTP Digest
Auth (RFC 2617).  No token exchange is needed — credentials are passed on
every request via ``requests.auth.HTTPDigestAuth``.

Default credentials: username ``root``, password ``root``.

Endpoints used:
    GET /cgi-bin/get_system_info.cgi  -> MinerIdentity (hostname, MAC, serial)
    GET /cgi-bin/miner_type.cgi       -> MinerIdentity (model, fw_version)
    GET /cgi-bin/stats.cgi            -> CoolingData + HashrateData + UptimeData
                                         + HashboardData (fans, temps, chains,
                                         elapsed, watt)
    GET /cgi-bin/pools.cgi            -> HashrateData (pool stats)
    GET /cgi-bin/warning.cgi          -> ErrorData (device warnings / errors)
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import requests
from requests.auth import HTTPDigestAuth

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


@CollectorFactory.register("bitmain")
class BitmainCollector(MinerCollector):
    """Adapter for miners running Bitmain Antminer stock firmware."""

    def __init__(self, url: str, username: Optional[str] = None, password: Optional[str] = None):
        super().__init__(url, username, password)
        self._session = requests.Session()
        # Antminer uses HTTP Digest Auth — set once on the session, no token round-trip.
        self._session.auth = HTTPDigestAuth(
            username or "root",
            password or "root",
        )

    def close(self) -> None:
        self._session.close()

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def authenticate(self) -> None:
        """Antminer uses HTTP Digest Auth — credentials are already set on the
        session in ``__init__``.  This is a no-op but refreshes the auth object
        in case credentials were changed after construction."""
        self._session.auth = HTTPDigestAuth(
            self.username or "root",
            self.password or "root",
        )
        logger.debug("Bitmain Digest Auth configured for %s (user=%s)",
                     self.url, self.username or "root")

    def _get(self, path: str) -> dict[str, Any]:
        """Issue a GET request; on 401 refresh Digest Auth and retry once."""
        url = f"{self.url}{path}"
        resp = self._session.get(url, timeout=_REQUEST_TIMEOUT)

        if resp.status_code == 401:
            logger.info("Got 401 from %s — refreshing Digest Auth and retrying", url)
            self.authenticate()
            resp = self._session.get(url, timeout=_REQUEST_TIMEOUT)

        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    def fetch_identity(self) -> MinerIdentity:
        sysinfo = self._get("/cgi-bin/get_system_info.cgi")
        miner_type = self._get("/cgi-bin/miner_type.cgi")
        serial = sysinfo.get("serinum", "")
        return MinerIdentity(
            uid=serial,
            serial_number=serial,
            hostname=sysinfo.get("hostname", ""),
            mac_address=sysinfo.get("macaddr", ""),
            model=miner_type.get("miner_type", ""),
            firmware="bitmain",
            ip_address=sysinfo.get("ipaddress", ""),
        )

    # ------------------------------------------------------------------
    # Metric fetchers
    # ------------------------------------------------------------------

    def fetch_cooling(self) -> CoolingData:
        raw = self._get("/cgi-bin/stats.cgi")
        return CoolingData.from_bitmain(raw)

    def fetch_hashrate(self) -> HashrateData:
        stats_raw = self._get("/cgi-bin/stats.cgi")
        pools_raw = self._get("/cgi-bin/pools.cgi")
        return HashrateData.from_bitmain(stats_raw, pools_raw)

    def fetch_uptime(self) -> UptimeData:
        stats_raw = self._get("/cgi-bin/stats.cgi")
        sysinfo_raw = self._get("/cgi-bin/get_system_info.cgi")
        return UptimeData.from_bitmain(stats_raw, sysinfo_raw)

    def fetch_hashboards(self) -> HashboardData:
        raw = self._get("/cgi-bin/stats.cgi")
        return HashboardData.from_bitmain(raw)

    def fetch_errors(self) -> ErrorData:
        raw = self._get("/cgi-bin/warning.cgi")
        return ErrorData.from_bitmain(raw)
