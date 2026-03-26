"""LuxOS (Luxor firmware) collector adapter.

LuxOS exposes a CGMiner-compatible TCP API on port 4028.  All telemetry
queries are read-only and do not require a session.

Commands used:
    config   -> MinerIdentity (hostname, MAC, serial, model)
    version  -> firmware version details
    summary  -> hashrate, uptime (elapsed), share stats
    pools    -> per-pool stats
    power    -> wattage
    fans     -> fan RPM and speed percentage
    temps    -> per-board temperature readings
    devs     -> per-hashboard stats (hashrate, accepted, temp)
    events   -> active miner events / errors
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
    """Extract a bare hostname/IP from a URL that may or may not have a scheme."""
    if "://" in url:
        parsed = urlparse(url)
        return parsed.hostname or url
    return url.split(":")[0]


@CollectorFactory.register("luxos")
class LuxOSCollector(MinerCollector):
    """Adapter for miners running LuxOS firmware."""

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
            logger.error("LuxOS command '%s' failed on %s:%d — %s", command, self._host, self._port, exc)
            raise

    # ------------------------------------------------------------------
    # Authentication (no-op for read-only telemetry)
    # ------------------------------------------------------------------

    def authenticate(self) -> None:
        """LuxOS read-only queries do not require authentication."""
        logger.debug("LuxOS auth is a no-op for telemetry collection (%s)", self.url)

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    def fetch_identity(self) -> MinerIdentity:
        config = self._send_command("config")
        cfg = (config.get("CONFIG") or [{}])[0]
        return MinerIdentity(
            uid=cfg.get("SerialNumber", ""),
            serial_number=cfg.get("SerialNumber", ""),
            hostname=cfg.get("Hostname", ""),
            mac_address=cfg.get("MACAddr", ""),
        )

    # ------------------------------------------------------------------
    # Metric fetchers
    # ------------------------------------------------------------------

    def fetch_cooling(self) -> CoolingData:
        fans_raw = self._send_command("fans")
        temps_raw = self._send_command("temps")
        return CoolingData.from_luxos(fans_raw, temps_raw)

    def fetch_hashrate(self) -> HashrateData:
        summary_raw = self._send_command("summary")
        pools_raw = self._send_command("pools")
        power_raw = self._send_command("power")
        return HashrateData.from_luxos(summary_raw, pools_raw, power_raw)

    def fetch_uptime(self) -> UptimeData:
        summary_raw = self._send_command("summary")
        version_raw = self._send_command("version")
        config_raw = self._send_command("config")
        return UptimeData.from_luxos(summary_raw, version_raw, config_raw)

    def fetch_hashboards(self) -> HashboardData:
        devs_raw = self._send_command("devs")
        temps_raw = self._send_command("temps")
        return HashboardData.from_luxos(devs_raw, temps_raw)

    def fetch_errors(self) -> ErrorData:
        events_raw = self._send_command("events")
        return ErrorData.from_luxos(events_raw)
