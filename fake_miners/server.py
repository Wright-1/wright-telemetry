"""Single fake miner server — one process per Docker container.

Controlled entirely by environment variables:

    FIRMWARE      braiins | vnish | luxos      (default: braiins)
    MINER_INDEX   integer 0-255               (default: 0)
    FIXTURES_DIR  path to fixtures root       (default: /fixtures)
    HTTP_PORT     port for Braiins/Vnish      (default: 80)
    LUXOS_PORT    port for LuxOS TCP API      (default: 4028)
    CONTROL_PORT  port for the fan-control    (default: 8080)
                  HTTP server (all firmware)

Each miner gets a unique hostname, MAC address, serial number, uid, and
jittered hashrate derived deterministically from MINER_INDEX so the numbers
are stable across container restarts.

FAN RPM SIMULATION
------------------
Fan RPMs are not static — they oscillate slowly (±4 %, 60 s period) around
their base values and can be driven to 0 to simulate a Wright Fan swap:

    GET  http://<container>:<CONTROL_PORT>/     → JSON fan state
    POST http://<container>:<CONTROL_PORT>/
         {"action": "fan_dip",    "duration_s": 8}   all fans → 0 for 8 s
         {"action": "fan_restore"}                    cancel dip early

For Braiins and Vnish containers the /control route is also available on the
main HTTP port:
    GET  http://<container>:<HTTP_PORT>/control
    POST http://<container>:<HTTP_PORT>/control

AUTHENTICATION
--------------
The server enforces token-based auth exactly as real hardware does:
  • Before any login request arrives, all traffic is allowed.
  • After the first successful POST to /api/v1/auth/login (Braiins) or
    /api/v1/unlock (Vnish), every subsequent GET must carry the correct
    token header.  Missing or wrong token → HTTP 401, which exercises the
    collector's re-auth / retry path.
"""

from __future__ import annotations

import copy
import json
import logging
import math
import os
import random
import socket
import socketserver
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("fake-miner")

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------

FIRMWARE     = os.environ.get("FIRMWARE",     "braiins").lower().strip()
MINER_INDEX  = int(os.environ.get("MINER_INDEX",  "0"))
FIXTURES_DIR = Path(os.environ.get("FIXTURES_DIR", "/fixtures"))
HTTP_PORT    = int(os.environ.get("HTTP_PORT",    "80"))
LUXOS_PORT   = int(os.environ.get("LUXOS_PORT",   "4028"))
CONTROL_PORT = int(os.environ.get("CONTROL_PORT", "8080"))


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
# FanState – live RPM simulation
# ---------------------------------------------------------------------------

class FanState:
    """Per-fake-miner fan RPM controller.

    Normal mode : slow sinusoidal oscillation (±4 %, 60 s period) around
                  each fan's base RPM; MINER_INDEX phase-shifts each container
                  so they don't all peak at the same moment.
    Dip mode    : all fans drop to 0 RPM for *duration_s* seconds, then
                  ramp back up linearly over ~2 s.
    """

    _PERIOD_S  = 60.0   # sinusoidal period in seconds
    _AMPLITUDE = 0.04   # ±4 % amplitude

    def __init__(self, base_rpms: dict[int, int], idx: int) -> None:
        self._base      = dict(base_rpms)
        self._phase     = (idx * 0.37) % (2 * math.pi)
        self._dip_until = 0.0
        self._lock      = threading.Lock()

    def current_rpm(self, position: int) -> int:
        base = self._base.get(position, 0)
        if base == 0:
            return 0
        with self._lock:
            dip_until = self._dip_until
        now = time.time()
        if now < dip_until:
            return 0
        # Soft ramp-up over 2 s after a dip; at startup dip_until==0 → recovery==1
        recovery = min(1.0, (now - dip_until) / 2.0) if dip_until > 0 else 1.0
        osc = math.sin(now * 2 * math.pi / self._PERIOD_S + self._phase)
        return max(0, round(base * recovery * (1.0 + osc * self._AMPLITUDE)))

    def trigger_dip(self, duration_s: float = 8.0) -> None:
        with self._lock:
            self._dip_until = time.time() + duration_s
        logger.info("FanState: dip triggered for %.0f s", duration_s)

    def trigger_restore(self) -> None:
        with self._lock:
            self._dip_until = 0.0
        logger.info("FanState: restored to normal")

    def status(self) -> dict[str, Any]:
        with self._lock:
            remaining = max(0.0, self._dip_until - time.time())
        return {
            "mode":            "dip" if remaining > 0 else "normal",
            "dip_remaining_s": round(remaining, 1),
            "base_rpms":       dict(self._base),
        }


# ---------------------------------------------------------------------------
# Per-miner personalisation
# ---------------------------------------------------------------------------

def _mac(offset: int) -> str:
    """Derive a unique-looking MAC from MINER_INDEX + an offset."""
    n = (MINER_INDEX + offset) & 0xFFFFFF
    return f"AA:BB:CC:{(n >> 16) & 0xFF:02X}:{(n >> 8) & 0xFF:02X}:{n & 0xFF:02X}"


def _jitter(value: float, pct: float = 0.05) -> float:
    """Slightly randomise *value* — caller must seed random first."""
    return round(value * (1.0 + random.uniform(-pct, pct)), 3)


def personalize(fixtures: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of *fixtures* with per-miner identity + jittered metrics."""
    random.seed(MINER_INDEX)  # deterministic: same index → same numbers on every restart
    fx = copy.deepcopy(fixtures)

    if FIRMWARE == "braiins":
        details = fx.get("miner_details", {})
        details["hostname"]      = f"braiins-fake-{MINER_INDEX:03d}"
        details["mac_address"]   = _mac(0x000000)
        details["serial_number"] = f"BRFAKE{MINER_INDEX:05d}"
        details["uid"]           = f"brfake{MINER_INDEX:012x}"

        # Jitter real_hashrate / nominal_hashrate.
        # fx["miner_stats"] is the full miner_stats.json content, so we dereference
        # into the nested "miner_stats" key to reach the actual stats dict.
        inner = fx.get("miner_stats", {}).get("miner_stats", {})
        for key in ("real_hashrate", "nominal_hashrate"):
            node = inner.get(key)
            if isinstance(node, dict) and "gigahash_per_second" in node:
                node["gigahash_per_second"] = _jitter(node["gigahash_per_second"])

    elif FIRMWARE == "vnish":
        info = fx.get("info", {})
        info["hostname"] = f"vnish-fake-{MINER_INDEX:03d}"
        info["mac"]      = _mac(0x001000)
        info["serial"]   = f"VNFAKE{MINER_INDEX:05d}"
        info["uid"]      = f"vnfake{MINER_INDEX:012x}"

        inner = fx.get("summary", {}).get("miner", {})
        for key in ("instant_hashrate", "average_hashrate"):
            if key in inner:
                inner[key] = _jitter(inner[key])

    elif FIRMWARE == "luxos":
        cfg_list = fx.get("config", {}).get("CONFIG")
        if isinstance(cfg_list, list) and cfg_list:
            cfg_list[0]["Hostname"]     = f"luxos-fake-{MINER_INDEX:03d}"
            cfg_list[0]["MACAddr"]      = _mac(0x002000)
            cfg_list[0]["SerialNumber"] = f"LXFAKE{MINER_INDEX:05d}"

        summary_list = fx.get("summary", {}).get("SUMMARY")
        if isinstance(summary_list, list) and summary_list:
            for key in ("GHS 5s", "GHS 30m", "GHS av"):
                if key in summary_list[0]:
                    summary_list[0][key] = _jitter(summary_list[0][key])

    return fx


# ---------------------------------------------------------------------------
# Fan RPM injection helpers
# ---------------------------------------------------------------------------

def _inject_braiins_fans(payload: dict[str, Any], fan_state: FanState) -> dict[str, Any]:
    payload = copy.deepcopy(payload)
    for f in payload.get("fans", []):
        f["rpm"] = fan_state.current_rpm(f["position"])
    return payload


def _inject_vnish_fans(payload: dict[str, Any], fan_state: FanState) -> dict[str, Any]:
    payload = copy.deepcopy(payload)
    for f in payload.get("fans", []):
        f["rpm"] = fan_state.current_rpm(f["id"])
    return payload


def _inject_luxos_fans(payload: dict[str, Any], fan_state: FanState) -> dict[str, Any]:
    payload = copy.deepcopy(payload)
    for f in payload.get("FANS", []):
        f["RPM"] = fan_state.current_rpm(f["ID"])
    return payload


# ---------------------------------------------------------------------------
# Auth state
# ---------------------------------------------------------------------------

class _AuthState:
    """Tracks the most-recently-issued auth token.

    Before any login request arrives, all traffic is allowed.
    After a login/unlock, every GET must carry the matching token.
    """

    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._lock = threading.Lock()

    def issue(self, token: str) -> None:
        with self._lock:
            self._token = token

    def check(self, header_value: str) -> bool:
        with self._lock:
            if self._token is None:
                return True   # no login yet → allow
            return header_value == self._token


# ---------------------------------------------------------------------------
# Control-request dispatcher
# ---------------------------------------------------------------------------

def _dispatch_control(body: bytes, fan_state: FanState) -> tuple[int, dict[str, Any]]:
    try:
        req = json.loads(body.decode()) if body else {}
    except json.JSONDecodeError:
        return 400, {"error": "invalid JSON"}

    action = req.get("action", "")
    if action == "fan_dip":
        duration = float(req.get("duration_s", 8.0))
        fan_state.trigger_dip(duration)
        return 200, {"status": "ok", "action": "fan_dip", "duration_s": duration}
    if action == "fan_restore":
        fan_state.trigger_restore()
        return 200, {"status": "ok", "action": "fan_restore"}
    return 200, fan_state.status()


# ---------------------------------------------------------------------------
# HTTP server — Braiins and Vnish
# ---------------------------------------------------------------------------

_BRAIINS_GET = {
    "/api/v1/miner/details":       "miner_details",
    "/api/v1/cooling/state":       "cooling_state",
    "/api/v1/miner/stats":         "miner_stats",
    "/api/v1/miner/hw/hashboards": "hashboards",
    "/api/v1/miner/errors":        "miner_errors",
}
_BRAIINS_POST = {"/api/v1/auth/login": "auth_login"}

_VNISH_GET  = {
    "/api/v1/info":    "info",
    "/api/v1/summary": "summary",
    "/api/v1/status":  "status",
}
_VNISH_POST = {"/api/v1/unlock": "unlock"}

_HTTP_ROUTES: dict[str, dict[str, dict[str, str]]] = {
    "braiins": {"GET": _BRAIINS_GET, "POST": _BRAIINS_POST},
    "vnish":   {"GET": _VNISH_GET,   "POST": _VNISH_POST},
}

# Which fixture key carries fan data (needs live RPM injection)
_FAN_KEY = {"braiins": "cooling_state", "vnish": "status"}

# Which header name each firmware's collector sends the token in
_AUTH_HEADER = {
    "braiins": "authorization",    # BraiinsCollector sets lowercase
    "vnish":   "Authorization",    # VnishCollector sets titlecase
}

# Which fixture key holds the token to issue on auth
_TOKEN_KEY = {"braiins": ("auth_login", "token"), "vnish": ("unlock", "token")}


def _make_http_handler(
    fixtures:  dict[str, Any],
    fan_state: FanState,
    auth:      _AuthState,
) -> type[BaseHTTPRequestHandler]:
    routes      = _HTTP_ROUTES[FIRMWARE]
    fan_key     = _FAN_KEY.get(FIRMWARE)
    auth_header = _AUTH_HEADER[FIRMWARE]
    inject_fn   = _inject_braiins_fans if FIRMWARE == "braiins" else _inject_vnish_fans
    tok_fixture, tok_field = _TOKEN_KEY[FIRMWARE]

    # Which POST path triggers token issuance
    auth_path = "/api/v1/auth/login" if FIRMWARE == "braiins" else "/api/v1/unlock"

    class _Handler(BaseHTTPRequestHandler):
        server_version = "FakeMiner/1.0"
        sys_version    = ""

        def log_message(self, fmt, *args):
            logger.debug(fmt, *args)

        def _send_json(self, payload: Any, status: int = 200) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self) -> bytes:
            n = int(self.headers.get("Content-Length", "0"))
            return self.rfile.read(n) if n else b""

        def do_GET(self):
            # /control is always public — no token required for monitoring
            if self.path == "/control":
                self._send_json(fan_state.status())
                return

            if not auth.check(self.headers.get(auth_header, "")):
                self._send_json({"error": "unauthorized"}, 401)
                return

            key = routes.get("GET", {}).get(self.path)
            if key is None:
                self._send_json({"error": "not found"}, 404)
                return

            payload = fixtures[key]
            if key == fan_key:
                payload = inject_fn(payload, fan_state)
            self._send_json(payload)

        def do_POST(self):
            if self.path == "/control":
                status, resp = _dispatch_control(self._read_body(), fan_state)
                self._send_json(resp, status)
                return

            key = routes.get("POST", {}).get(self.path)
            if key is None:
                self._send_json({"error": "not found"}, 404)
                return

            self._read_body()  # consume request body (credentials not validated)

            # Issue the token so subsequent GETs are enforced
            if self.path == auth_path:
                auth.issue(fixtures[tok_fixture][tok_field])

            self._send_json(fixtures[key])

    return _Handler


# ---------------------------------------------------------------------------
# TCP server — LuxOS port 4028
# ---------------------------------------------------------------------------

_LUXOS_COMMANDS = {
    "version": "version",
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

    fixtures:  dict[str, Any] = {}
    fan_state: Optional[FanState] = None

    def handle(self) -> None:
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
                    break
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
        if key:
            payload = self.fixtures.get(key, _LUXOS_ERROR)
            if key == "fans" and self.fan_state is not None:
                payload = _inject_luxos_fans(payload, self.fan_state)
        else:
            payload = _LUXOS_ERROR

        try:
            self.request.sendall(json.dumps(payload).encode("utf-8"))
        except OSError:
            pass


def _make_luxos_handler(
    fixtures:  dict[str, Any],
    fan_state: FanState,
) -> type[_LuxOSHandler]:
    return type("Handler", (_LuxOSHandler,), {
        "fixtures":  fixtures,
        "fan_state": fan_state,
    })


class _ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


# ---------------------------------------------------------------------------
# Fan-control HTTP server (used by all firmware, main HTTP port for
# Braiins/Vnish via /control; standalone on CONTROL_PORT for LuxOS)
# ---------------------------------------------------------------------------

def _make_control_handler(fan_state: FanState) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        server_version = "FakeMiner-Control/1.0"
        sys_version    = ""

        def log_message(self, fmt, *args):
            logger.debug(fmt, *args)

        def _send_json(self, payload: Any, status: int = 200) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self) -> bytes:
            n = int(self.headers.get("Content-Length", "0"))
            return self.rfile.read(n) if n else b""

        def do_GET(self):
            self._send_json(fan_state.status())

        def do_POST(self):
            status, resp = _dispatch_control(self._read_body(), fan_state)
            self._send_json(resp, status)

    return _Handler


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if FIRMWARE not in ("braiins", "vnish", "luxos"):
        logger.error(
            "Unknown FIRMWARE=%r — must be braiins, vnish, or luxos", FIRMWARE
        )
        sys.exit(1)

    raw_fixtures = load_fixtures()
    fixtures     = personalize(raw_fixtures)

    # Build FanState from the un-personalised base fixture so every restart
    # begins from the same base RPMs regardless of jitter seed.
    if FIRMWARE == "braiins":
        base_rpms = {
            f["position"]: f["rpm"]
            for f in raw_fixtures.get("cooling_state", {}).get("fans", [])
        }
    elif FIRMWARE == "vnish":
        base_rpms = {
            f["id"]: f["rpm"]
            for f in raw_fixtures.get("status", {}).get("fans", [])
        }
    else:  # luxos
        base_rpms = {
            f["ID"]: f["RPM"]
            for f in raw_fixtures.get("fans", {}).get("FANS", [])
        }

    fan_state = FanState(base_rpms, idx=MINER_INDEX)
    auth      = _AuthState()

    if FIRMWARE in ("braiins", "vnish"):
        ThreadingHTTPServer.allow_reuse_address = True
        server = ThreadingHTTPServer(
            ("0.0.0.0", HTTP_PORT),
            _make_http_handler(fixtures, fan_state, auth),
        )
        logger.info(
            "fake-%s miner #%d  ready on HTTP port %d  (control: /control)",
            FIRMWARE, MINER_INDEX, HTTP_PORT,
        )
        server.serve_forever()

    else:  # luxos — TCP API + standalone control HTTP server
        tcp_server = _ThreadedTCPServer(
            ("0.0.0.0", LUXOS_PORT),
            _make_luxos_handler(fixtures, fan_state),
        )
        t_tcp = threading.Thread(target=tcp_server.serve_forever, daemon=True)
        t_tcp.start()
        logger.info(
            "fake-luxos miner #%d  TCP API ready on port %d",
            MINER_INDEX, LUXOS_PORT,
        )

        # Standalone HTTP control server for LuxOS containers
        ThreadingHTTPServer.allow_reuse_address = True
        ctrl_server = ThreadingHTTPServer(
            ("0.0.0.0", CONTROL_PORT),
            _make_control_handler(fan_state),
        )
        logger.info(
            "fake-luxos control server ready on HTTP port %d  "
            "(GET / → status, POST / → {\"action\":\"fan_dip\"})",
            CONTROL_PORT,
        )
        ctrl_server.serve_forever()


if __name__ == "__main__":
    main()
