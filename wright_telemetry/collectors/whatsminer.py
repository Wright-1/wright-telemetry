"""WhatsMiner (btminer firmware) collector adapter.

WhatsMiner exposes a CGMiner-derived TCP API on port 4028. Read access is
enabled by default and needs no password, so every telemetry query here is
unauthenticated. Two details make this API *not* interchangeable with the
LuxOS/Sealminer adapters:

* the request key is ``cmd``, not ``command`` (``{"command": "summary"}``
  returns ``invalid cmd``);
* responses are NUL-terminated and the connection stays open afterwards.

Newer control boards (H616 platform) also serve a length-prefixed API V3 on
port 4433, documented at https://apidoc.whatsminer.com. The 4028 API is the
one present across the whole fleet, including M30-era units whose firmware
has no 4433 listener at all, so it is what this adapter speaks.

Commands used (WhatsMiner API User's Manual V2.2.2, section 4):
    get_version     -> firmware / API version, platform, chip (probe helper)
    get_miner_info  -> hostname, MAC, network config
    devdetails      -> per-board model string
    summary         -> hashrate, power, fans, temps, uptime
    pools           -> per-pool stats
    edevs           -> per-hashboard hashrate, temps, chip count, PCB serial
    get_psu         -> PSU model, serial, input current / voltage
    get_error_code  -> miner error codes
"""

from __future__ import annotations

import json
import logging
import re
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

# get_miner_info takes an explicit field list. Always sending one is deliberate:
# a bare {"cmd":"get_miner_info"} has been observed emitting a trailing comma
# before the closing brace ("ledstat":"auto",}), which is not valid JSON.
_MINER_INFO_FIELDS = "ip,proto,netmask,gateway,dns,hostname,mac,ledstat,minersn,powersn"

# Matches a comma directly before a closing brace/bracket — see _repair_json.
_TRAILING_COMMA = re.compile(r",\s*([}\]])")


def _host_from_url(url: str) -> str:
    if "://" in url:
        parsed = urlparse(url)
        return parsed.hostname or url
    return url.split(":")[0]


def _repair_json(body: str) -> str:
    """Strip trailing commas some btminer builds emit before ``}`` / ``]``.

    Firmware 20220422.18.REL returns malformed JSON for at least one readable
    command. Repairing is preferable to dropping the reading, since the payload
    is otherwise complete and well-formed.
    """
    return _TRAILING_COMMA.sub(r"\1", body)


@CollectorFactory.register("whatsminer")
class WhatsminerCollector(MinerCollector):
    """Adapter for miners running WhatsMiner (btminer) firmware."""

    def __init__(self, url: str, username: Optional[str] = None, password: Optional[str] = None):
        super().__init__(url, username, password)
        self._host = _host_from_url(self.url)
        self._port = _DEFAULT_PORT

    def _send_command(self, command: str, **params: Any) -> dict[str, Any]:
        """Send a single API command over TCP and return the parsed JSON response."""
        payload: dict[str, Any] = {"cmd": command, **params}
        raw = json.dumps(payload)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(_SOCKET_TIMEOUT)
                sock.connect((self._host, self._port))
                sock.sendall(raw.encode("utf-8"))

                # btminer leaves the connection open after replying (it idles
                # for 300s), so read until the buffer parses as complete JSON
                # rather than waiting for an EOF that never arrives.
                chunks: list[bytes] = []
                parsed: Optional[dict[str, Any]] = None
                while True:
                    chunk = sock.recv(_RECV_BUF)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    buf = b"".join(chunks).rstrip(b"\x00")
                    try:
                        parsed = json.loads(buf.decode("utf-8"))
                        break
                    except (ValueError, UnicodeDecodeError):
                        continue  # reply not complete yet — keep reading

            if parsed is None:
                body = b"".join(chunks).decode("utf-8").rstrip("\x00")
                parsed = json.loads(_repair_json(body))
        except (socket.error, json.JSONDecodeError) as exc:
            logger.error(
                "WhatsMiner command '%s' failed on %s:%d — %s",
                command, self._host, self._port, exc,
            )
            raise

        # An unsupported command answers 200-style with STATUS "E"; surface it
        # as an empty result so a missing command degrades one metric instead
        # of raising and killing the whole poll.
        if parsed.get("STATUS") == "E":
            logger.warning(
                "WhatsMiner rejected command '%s' on %s: %s",
                command, self._host, parsed.get("Msg"),
            )
            return {}
        return parsed

    # ------------------------------------------------------------------
    # Authentication (no-op — read access is granted by default)
    # ------------------------------------------------------------------

    def authenticate(self) -> None:
        logger.debug("WhatsMiner auth is a no-op for telemetry collection (%s)", self.url)

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    def fetch_identity(self) -> MinerIdentity:
        info = self._send_command("get_miner_info", info=_MINER_INFO_FIELDS).get("Msg") or {}
        version = self._send_command("get_version").get("Msg") or {}
        summary = (self._send_command("summary").get("SUMMARY") or [{}])[0]

        mac = info.get("mac", "")
        # M30-era firmware omits minersn from get_miner_info entirely, so the
        # MAC is the only stable per-machine identifier available — and it is
        # what the canonical fleet UID (facility:mac) is keyed on anyway.
        serial = info.get("minersn", "")
        # "Miner Type" appears in summary on some builds and not others, and
        # get_version only carries miner_type on newer firmware, so fall back
        # to devdetails, where every build reports the model per board.
        model = summary.get("Miner Type", "") or version.get("miner_type", "")
        if not model:
            details = (self._send_command("devdetails").get("DEVDETAILS") or [{}])[0]
            model = details.get("Model", "")

        return MinerIdentity(
            uid=mac or serial,
            serial_number=serial,
            hostname=info.get("hostname", ""),
            mac_address=mac,
            model=model,
            firmware="whatsminer",
        )

    # ------------------------------------------------------------------
    # Metric fetchers
    # ------------------------------------------------------------------

    def fetch_cooling(self) -> CoolingData:
        summary_raw = self._send_command("summary")
        return CoolingData.from_whatsminer(summary_raw)

    def fetch_hashrate(self) -> HashrateData:
        summary_raw = self._send_command("summary")
        pools_raw = self._send_command("pools")
        psu_raw = self._send_command("get_psu")
        return HashrateData.from_whatsminer(summary_raw, pools_raw, psu_raw)

    def fetch_uptime(self) -> UptimeData:
        summary_raw = self._send_command("summary")
        version_raw = self._send_command("get_version")
        info_raw = self._send_command("get_miner_info", info=_MINER_INFO_FIELDS)
        return UptimeData.from_whatsminer(summary_raw, version_raw, info_raw)

    def fetch_hashboards(self) -> HashboardData:
        # "edevs" and "devs" return the same board array on current firmware;
        # edevs is the documented one, with devs as the fallback for builds
        # that predate it.
        edevs_raw = self._send_command("edevs")
        if not edevs_raw.get("DEVS"):
            edevs_raw = self._send_command("devs")
        return HashboardData.from_whatsminer(edevs_raw)

    def fetch_errors(self) -> ErrorData:
        error_raw = self._send_command("get_error_code")
        return ErrorData.from_whatsminer(error_raw)
