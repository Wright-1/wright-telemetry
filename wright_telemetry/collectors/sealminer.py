"""Sealminer (bdminer firmware) collector adapter.

Sealminer exposes a CGMiner-compatible TCP API on port 4028.  All telemetry
queries are read-only and do not require a session.

Commands used:
    version     -> firmware fingerprint (probe helper)
    stats       -> identity, cooling, hashrate power, uptime, hashboards, errors
    summary     -> hashrate averages, uptime (elapsed), share counts
    pools       -> per-pool stats
    devdetails  -> device name / model confirmation
"""

from __future__ import annotations

import json
import logging
import socket
from typing import Any, Optional
from urllib.parse import urlparse

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

_DEFAULT_PORT = 4028
_SOCKET_TIMEOUT = 10  # seconds
_RECV_BUF = 65536


def _host_from_url(url: str) -> str:
    if "://" in url:
        parsed = urlparse(url)
        return parsed.hostname or url
    return url.split(":")[0]


@CollectorFactory.register("sealminer")
class SealminerCollector(MinerCollector):
    """Adapter for miners running Sealminer (bdminer) firmware."""

    def __init__(self, url: str, username: Optional[str] = None, password: Optional[str] = None):
        super().__init__(url, username, password)
        self._host = _host_from_url(self.url)
        self._port = _DEFAULT_PORT

    def _send_command(self, command: str, parameter: str = "") -> dict[str, Any]:
        """Send a single API command over TCP and return the parsed JSON response."""
        payload: dict[str, Any] = {"command": command}
        if parameter:
            payload["parameter"] = parameter

        raw = json.dumps(payload)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(_SOCKET_TIMEOUT)
                sock.connect((self._host, self._port))
                sock.sendall(raw.encode("utf-8"))

                chunks: list[bytes] = []
                while True:
                    chunk = sock.recv(_RECV_BUF)
                    if not chunk:
                        break
                    chunks.append(chunk)

            body = b"".join(chunks).decode("utf-8").rstrip("\x00")
            return json.loads(body)
        except (socket.error, json.JSONDecodeError) as exc:
            logger.error(
                "Sealminer command '%s' failed on %s:%d — %s",
                command, self._host, self._port, exc,
            )
            raise

    # ------------------------------------------------------------------
    # Authentication (no-op — read access is granted by default)
    # ------------------------------------------------------------------

    def authenticate(self) -> None:
        logger.debug("Sealminer auth is a no-op for telemetry collection (%s)", self.url)

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    def fetch_identity(self) -> MinerIdentity:
        stats_raw = self._send_command("stats")
        devdetails_raw = self._send_command("devdetails")
        stats = (stats_raw.get("STATS") or [{}])[0]
        details = (devdetails_raw.get("DEVDETAILS") or [{}])[0]
        return MinerIdentity(
            uid=stats.get("Ctrl Board SN", ""),
            serial_number=stats.get("Ctrl Board SN", ""),
            hostname="",
            mac_address=stats.get("MAC", ""),
            model=stats.get("Model", "") or details.get("Model", ""),
            firmware="sealminer",
        )

    # ------------------------------------------------------------------
    # Metric fetchers
    # ------------------------------------------------------------------

    def fetch_cooling(self) -> CoolingData:
        stats_raw = self._send_command("stats")
        return CoolingData.from_sealminer(stats_raw)

    def fetch_hashrate(self) -> HashrateData:
        summary_raw = self._send_command("summary")
        pools_raw = self._send_command("pools")
        stats_raw = self._send_command("stats")
        return HashrateData.from_sealminer(summary_raw, pools_raw, stats_raw)

    def fetch_uptime(self) -> UptimeData:
        summary_raw = self._send_command("summary")
        stats_raw = self._send_command("stats")
        return UptimeData.from_sealminer(summary_raw, stats_raw)

    def fetch_hashboards(self) -> HashboardData:
        stats_raw = self._send_command("stats")
        return HashboardData.from_sealminer(stats_raw)

    def fetch_errors(self) -> ErrorData:
        stats_raw = self._send_command("stats")
        return ErrorData.from_sealminer(stats_raw)
