"""Single fake miner server — one process per Docker container.

Controlled entirely by environment variables:

    FIRMWARE      braiins | vnish | luxos      (default: braiins)
    MINER_INDEX   integer 0-255               (default: 0)
    FIXTURES_DIR  path to fixtures root       (default: /fixtures)
    HTTP_PORT     port for Braiins/Vnish      (default: 80)
    LUXOS_PORT    port for LuxOS TCP API      (default: 4028)

Each miner gets a unique hostname, MAC address, and serial number derived
from MINER_INDEX so they look like real distinct devices in the dashboard.
Hashrate values are deterministically jittered per-index so the numbers
aren't identical across fakes.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import random
import socket
import socketserver
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("fake-miner")

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------

FIRMWARE = os.environ.get("FIRMWARE", "braiins").lower().strip()
MINER_INDEX = int(os.environ.get("MINER_INDEX", "0"))
FIXTURES_DIR = Path(os.environ.get("FIXTURES_DIR", "/fixtures"))
HTTP_PORT = int(os.environ.get("HTTP_PORT", "80"))
LUXOS_PORT = int(os.environ.get("LUXOS_PORT", "4028"))


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

def load_fixtures() -> dict[str, Any]:
    folder = FIXTURES_DIR / FIRMWARE
    if not folder.is_dir():
        logger.error(
            "Fixtures directory not found: %s — is the volume mounted?", folder
        )
        sys.exit(1)
    out: dict[str, Any] = {}
    for path in sorted(folder.glob("*.json")):
        with path.open() as fh:
            out[path.stem] = json.load(fh)
    logger.info("Loaded %d fixture(s) from %s", len(out), folder)
    return out


# ---------------------------------------------------------------------------
# Per-miner personalisation
# ---------------------------------------------------------------------------

def _mac(offset: int) -> str:
    """Derive a unique-looking MAC from MINER_INDEX + an offset."""
    n = (MINER_INDEX + offset) & 0xFFFFFF
    return f"AA:BB:CC:{(n >> 16) & 0xFF:02X}:{(n >> 8) & 0xFF:02X}:{n & 0xFF:02X}"


def _jitter(value: float, pct: float = 0.05) -> float:
    """Slightly randomise *value* — seeded in personalize() so it's deterministic."""
    return round(value * (1.0 + random.uniform(-pct, pct)), 3)


def personalize(fixtures: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of *fixtures* with per-miner identity fields applied."""
    random.seed(MINER_INDEX)  # deterministic jitter per container
    fx = copy.deepcopy(fixtures)

    if FIRMWARE == "braiins":
        details = fx.get("miner_details", {})
        details["hostname"] = f"braiins-fake-{MINER_INDEX:03d}"
        details["mac_address"] = _mac(0x000000)
        details["serial_number"] = f"BRFAKE{MINER_INDEX:05d}"

        inner = fx.get("miner_stats", {}).get("miner_stats", {})
        for key in ("ghs_5s", "ghs_30m", "ghs_av"):
            if key in inner:
                inner[key] = _jitter(inner[key])

    elif FIRMWARE == "vnish":
        info = fx.get("info", {})
        info["hostname"] = f"vnish-fake-{MINER_INDEX:03d}"
        info["mac"] = _mac(0x001000)
        info["serial"] = f"VNFAKE{MINER_INDEX:05d}"

        inner = fx.get("summary", {}).get("miner", {})
        for key in ("instant_hashrate", "average_hashrate"):
            if key in inner:
                inner[key] = _jitter(inner[key])

    elif FIRMWARE == "luxos":
        cfg_list = fx.get("config", {}).get("CONFIG")
        if isinstance(cfg_list, list) and cfg_list:
            cfg_list[0]["Hostname"] = f"luxos-fake-{MINER_INDEX:03d}"
            cfg_list[0]["MACAddr"] = _mac(0x002000)
            cfg_list[0]["SerialNumber"] = f"LXFAKE{MINER_INDEX:05d}"

        summary_list = fx.get("summary", {}).get("SUMMARY")
        if isinstance(summary_list, list) and summary_list:
            for key in ("GHS 5s", "GHS 30m", "GHS av"):
                if key in summary_list[0]:
                    summary_list[0][key] = _jitter(summary_list[0][key])

    return fx


# ---------------------------------------------------------------------------
# HTTP server — Braiins and Vnish
# ---------------------------------------------------------------------------

# Map (method, path) → fixture key for each firmware.
# The probe endpoints are the critical ones; the rest serve collector data.

_BRAIINS_GET = {
    "/api/v1/miner/details":       "miner_details",   # ← probe hits this
    "/api/v1/cooling/state":       "cooling_state",
    "/api/v1/miner/stats":         "miner_stats",
    "/api/v1/miner/hw/hashboards": "hashboards",
    "/api/v1/miner/errors":        "miner_errors",
}
_BRAIINS_POST = {
    "/api/v1/auth/login": "auth_login",
}

_VNISH_GET = {
    "/api/v1/info":    "info",       # ← probe hits this (must have firmware_version)
    "/api/v1/summary": "summary",
    "/api/v1/status":  "status",
}
_VNISH_POST = {
    "/api/v1/unlock": "unlock",
}

_HTTP_ROUTES: dict[str, dict[str, dict[str, str]]] = {
    "braiins": {"GET": _BRAIINS_GET, "POST": _BRAIINS_POST},
    "vnish":   {"GET": _VNISH_GET,   "POST": _VNISH_POST},
}


def _make_http_handler(fixtures: dict[str, Any]) -> type[BaseHTTPRequestHandler]:
    routes = _HTTP_ROUTES[FIRMWARE]

    class _Handler(BaseHTTPRequestHandler):
        server_version = "FakeMiner/1.0"
        sys_version = ""

        def log_message(self, fmt, *args):  # redirect to our logger
            logger.debug(fmt, *args)

        def _send_json(self, payload: Any, status: int = 200) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _dispatch(self, method: str) -> None:
            key = routes.get(method, {}).get(self.path)
            if key is None:
                self._send_json({"error": "not found"}, 404)
                return
            if method == "POST":
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)   # consume body; we don't need it
            self._send_json(fixtures[key])

        def do_GET(self):  self._dispatch("GET")
        def do_POST(self): self._dispatch("POST")

    return _Handler


# ---------------------------------------------------------------------------
# TCP server — LuxOS port 4028
# ---------------------------------------------------------------------------

_LUXOS_COMMANDS = {
    "version": "version",   # ← probe sends this; response must contain "LUXminer"
    "config":  "config",
    "summary": "summary",
    "pools":   "pools",
    "power":   "power",
    "fans":    "fans",
    "temps":   "temps",
    "devs":    "devs",
    "events":  "events",
}

_LUXOS_ERROR = {
    "STATUS": [{"STATUS": "E", "Code": 14, "Msg": "Invalid command"}],
    "id": 1,
}


class _LuxOSHandler(socketserver.BaseRequestHandler):
    """One instance per accepted TCP connection."""

    fixtures: dict[str, Any] = {}  # populated by _make_luxos_handler()

    def handle(self) -> None:
        # Read until we have a parseable JSON object or the peer closes.
        raw = b""
        try:
            self.request.settimeout(5.0)
            while True:
                chunk = self.request.recv(4096)
                if not chunk:
                    break
                raw += chunk
                try:
                    json.loads(raw.decode("utf-8").rstrip("\x00"))
                    break   # valid JSON — stop reading
                except json.JSONDecodeError:
                    continue
        except socket.timeout:
            pass

        if not raw:
            return

        try:
            req = json.loads(raw.decode("utf-8").rstrip("\x00"))
        except json.JSONDecodeError:
            return

        cmd = req.get("command", "").lower().strip()
        key = _LUXOS_COMMANDS.get(cmd)
        payload = self.fixtures.get(key, _LUXOS_ERROR) if key else _LUXOS_ERROR

        try:
            self.request.sendall(json.dumps(payload).encode("utf-8"))
        except OSError:
            pass
        # Connection is closed automatically when handle() returns.


def _make_luxos_handler(fixtures: dict[str, Any]) -> type[_LuxOSHandler]:
    return type("Handler", (_LuxOSHandler,), {"fixtures": fixtures})


class _ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if FIRMWARE not in ("braiins", "vnish", "luxos"):
        logger.error(
            "Unknown FIRMWARE=%r — must be braiins, vnish, or luxos", FIRMWARE
        )
        sys.exit(1)

    fixtures = personalize(load_fixtures())

    if FIRMWARE in ("braiins", "vnish"):
        ThreadingHTTPServer.allow_reuse_address = True
        server = ThreadingHTTPServer(
            ("0.0.0.0", HTTP_PORT), _make_http_handler(fixtures)
        )
        logger.info(
            "fake-%s miner #%d  ready on HTTP port %d",
            FIRMWARE, MINER_INDEX, HTTP_PORT,
        )
        server.serve_forever()

    else:  # luxos
        server = _ThreadedTCPServer(
            ("0.0.0.0", LUXOS_PORT), _make_luxos_handler(fixtures)
        )
        logger.info(
            "fake-luxos miner #%d  ready on TCP port %d",
            MINER_INDEX, LUXOS_PORT,
        )
        server.serve_forever()


if __name__ == "__main__":
    main()
