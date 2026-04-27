"""Local fake miner servers for testing wright-telemetry without real hardware.

Spins up any number of fake Braiins, Vnish, and/or LuxOS miners on
localhost and serves the JSON fixtures under ``tests/fixtures/<firmware>/``.

USAGE
-----
    python fake_miners.py                       # 2 of each firmware (defaults)
    python fake_miners.py --braiins 5 --vnish 3 --luxos 0
    python fake_miners.py --base-port 18000 --print-config

Braiins fakes live on  http://127.0.0.1:<base_port + i>
Vnish   fakes live on  http://127.0.0.1:<base_port + 100 + i>
LuxOS   fakes live on  127.0.0.<i+1>:4028  (the port is hard-coded in the
        LuxOS collector, so each LuxOS fake needs its own loopback IP).
        On Linux 127.0.0.0/8 is automatically loopback so this Just Works.
        On macOS you must pre-create aliases, e.g.:

            sudo ifconfig lo0 alias 127.0.0.2 up
            sudo ifconfig lo0 alias 127.0.0.3 up

Each fake:
  • Has a unique hostname, MAC address, serial number, and uid derived from
    its index so every miner looks like a distinct device in the dashboard.
  • Returns slightly jittered hashrate values (deterministic per index, so
    restarting the server produces identical numbers).
  • Reports dynamically-varying fan RPMs: slow sinusoidal oscillation (±4 %,
    60-second period, phase-shifted per miner) so fan-detection logic sees
    realistic movement rather than a frozen number.

FAN DIP SIMULATION
------------------
To test Wright Fan detection you can drop all fans to 0 RPM on demand:

  • SIGUSR1         — broadcast an 8-second dip to every fake miner at once
  • Global control  — one HTTP server handles all fakes:
      GET  http://127.0.0.1:<control_port>          fan status (all miners)
      POST http://127.0.0.1:<control_port>
           {"action": "fan_dip", "duration_s": 8}   start dip
           {"action": "fan_restore"}                 cancel dip early
  • Per-miner control — same API on each individual miner's /control path:
      GET  http://127.0.0.1:<miner_port>/control
      POST http://127.0.0.1:<miner_port>/control

AUTHENTICATION
--------------
Fake servers enforce the same token-based auth that real miners use:
  • The first POST to /api/v1/auth/login (Braiins) or /api/v1/unlock (Vnish)
    issues a token.  All subsequent GET requests must carry that token in the
    appropriate header, exactly as the real collectors do.  An absent or
    wrong token returns HTTP 401, exercising the collector's re-auth path.
  • Before any login has occurred the server allows all traffic so fakes
    work without credentials too.

When --print-config is given, the script prints a YAML snippet you can
paste into wright-telemetry's config under ``miners:`` and then exits
(the fake servers are NOT left running in that case).
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import math
import random
import signal
import socket
import socketserver
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("fake_miners")

FIXTURES = Path(__file__).parent / "tests" / "fixtures"


# ---------------------------------------------------------------------------
# FanState – per-miner RPM simulation
# ---------------------------------------------------------------------------

class FanState:
    """Per-fake-miner fan RPM controller.

    Normal mode : slow sinusoidal oscillation (±4 %, 60 s period) around
                  each fan's base RPM; each miner is phase-shifted so they
                  don't all peak at the same moment.
    Dip mode    : all fans drop to 0 RPM for *duration_s* seconds, then
                  ramp back up linearly over ~2 s — mimicking a Wright Fan
                  momentarily stopping before spinning up to full speed.
    """

    _PERIOD_S  = 60.0   # sinusoidal oscillation period (seconds)
    _AMPLITUDE = 0.04   # ±4 % peak-to-peak amplitude

    def __init__(self, base_rpms: dict[int, int], idx: int) -> None:
        self._base      = dict(base_rpms)
        # Phase-shift each miner so fans don't all peak simultaneously
        self._phase     = (idx * 0.37) % (2 * math.pi)
        self._dip_until = 0.0   # epoch time at which the current dip ends
        self._lock      = threading.Lock()

    # ------------------------------------------------------------------
    def current_rpm(self, position: int) -> int:
        """Return the live RPM for *position*, including oscillation / dip."""
        base = self._base.get(position, 0)
        if base == 0:
            return 0
        with self._lock:
            dip_until = self._dip_until
        now = time.time()
        if now < dip_until:
            return 0  # still dipping
        # Soft ramp-up over 2 s after the dip ends.
        # At startup dip_until == 0.0 so (now - 0) >> 2 → recovery == 1.0.
        recovery = min(1.0, (now - dip_until) / 2.0) if dip_until > 0 else 1.0
        osc = math.sin(now * 2 * math.pi / self._PERIOD_S + self._phase)
        return max(0, round(base * recovery * (1.0 + osc * self._AMPLITUDE)))

    def trigger_dip(self, duration_s: float = 8.0) -> None:
        """Drop all fans to 0 RPM for *duration_s* seconds, then ramp back up."""
        with self._lock:
            self._dip_until = time.time() + duration_s
        logger.info("FanState: dip triggered for %.0f s", duration_s)

    def trigger_restore(self) -> None:
        """Cancel any active dip immediately."""
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


# Global registry so SIGUSR1 can reach every FanState at once
_all_fan_states: list[FanState] = []


# ---------------------------------------------------------------------------
# Fixture loading + per-miner mutation
# ---------------------------------------------------------------------------

def _load_fixtures(firmware: str) -> dict[str, Any]:
    """Load every JSON fixture for *firmware* into a {stem: payload} dict."""
    folder = FIXTURES / firmware
    if not folder.is_dir():
        raise FileNotFoundError(f"No fixtures directory at {folder}")
    out: dict[str, Any] = {}
    for path in folder.glob("*.json"):
        with path.open() as fh:
            out[path.stem] = json.load(fh)
    return out


def _fake_mac(idx: int) -> str:
    return f"AA:BB:CC:{(idx >> 16) & 0xFF:02X}:{(idx >> 8) & 0xFF:02X}:{idx & 0xFF:02X}"


def _jitter(value: float, pct: float = 0.05) -> float:
    """Return *value* ± up to *pct* fractional noise (already seeded by caller)."""
    return round(value * (1.0 + random.uniform(-pct, pct)), 3)


def _personalize_braiins(fx: dict[str, Any], idx: int) -> dict[str, Any]:
    """Deep-copy *fx* and stamp per-miner identity + jittered hashrate."""
    fx = copy.deepcopy(fx)
    random.seed(idx)  # deterministic jitter: same idx → same numbers every run

    # Identity fields (miner_details.json is the entire file content under key "miner_details")
    details = fx.get("miner_details", {})
    details["hostname"]      = f"braiins-fake-{idx:03d}"
    details["mac_address"]   = _fake_mac(idx)
    details["serial_number"] = f"BRFAKE{idx:05d}"
    details["uid"]           = f"brfake{idx:012x}"

    # Hashrate jitter: real_hashrate / nominal_hashrate inside miner_stats.json
    # fx["miner_stats"] is the full miner_stats.json content, so we dereference twice.
    inner = fx.get("miner_stats", {}).get("miner_stats", {})
    for key in ("real_hashrate", "nominal_hashrate"):
        node = inner.get(key)
        if isinstance(node, dict) and "gigahash_per_second" in node:
            node["gigahash_per_second"] = _jitter(node["gigahash_per_second"])

    return fx


def _personalize_vnish(fx: dict[str, Any], idx: int) -> dict[str, Any]:
    fx = copy.deepcopy(fx)
    random.seed(idx)

    info = fx.get("info", {})
    info["hostname"] = f"vnish-fake-{idx:03d}"
    info["mac"]      = _fake_mac(idx + 0x1000)
    info["serial"]   = f"VNFAKE{idx:05d}"
    info["uid"]      = f"vnfake{idx:012x}"  # unique uid so every miner is distinct

    inner = fx.get("summary", {}).get("miner", {})
    for key in ("instant_hashrate", "average_hashrate"):
        if key in inner:
            inner[key] = _jitter(inner[key])

    return fx


def _personalize_luxos(fx: dict[str, Any], idx: int) -> dict[str, Any]:
    fx = copy.deepcopy(fx)
    random.seed(idx)

    cfg = fx.get("config", {}).get("CONFIG")
    if isinstance(cfg, list) and cfg:
        cfg[0]["Hostname"]     = f"luxos-fake-{idx:03d}"
        cfg[0]["MACAddr"]      = _fake_mac(idx + 0x2000)
        cfg[0]["SerialNumber"] = f"LXFAKE{idx:05d}"  # unique serial per miner

    # Jitter GHS values in the summary fixture
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
    """Return a copy of the cooling_state payload with live RPMs substituted."""
    payload = copy.deepcopy(payload)
    for f in payload.get("fans", []):
        f["rpm"] = fan_state.current_rpm(f["position"])
    return payload


def _inject_vnish_fans(payload: dict[str, Any], fan_state: FanState) -> dict[str, Any]:
    """Inject live fan RPMs into the Vnish status payload."""
    payload = copy.deepcopy(payload)
    for f in payload.get("fans", []):
        f["rpm"] = fan_state.current_rpm(f["id"])
    return payload


def _inject_luxos_fans(payload: dict[str, Any], fan_state: FanState) -> dict[str, Any]:
    """Inject live fan RPMs into the LuxOS fans payload."""
    payload = copy.deepcopy(payload)
    for f in payload.get("FANS", []):
        f["RPM"] = fan_state.current_rpm(f["ID"])
    return payload


# ---------------------------------------------------------------------------
# Auth state – per HTTP server instance
# ---------------------------------------------------------------------------

class _AuthState:
    """Tracks the most-recently-issued auth token for one fake server.

    Before any login the server allows all traffic (unauthenticated mode).
    Once a login/unlock has occurred every GET must carry the correct token,
    exactly as a real Braiins or Vnish miner would behave.
    """

    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._lock = threading.Lock()

    def issue(self, token: str) -> None:
        with self._lock:
            self._token = token

    def check(self, header_value: str) -> bool:
        """Return True if the request should be allowed through."""
        with self._lock:
            if self._token is None:
                return True               # no login yet → allow (no-credentials mode)
            return header_value == self._token


# ---------------------------------------------------------------------------
# Control-request dispatcher (shared between per-miner and global endpoints)
# ---------------------------------------------------------------------------

def _dispatch_control(body: bytes, fan_state: FanState) -> tuple[int, dict[str, Any]]:
    """Parse a control request and act on *fan_state*.

    Returns ``(http_status, response_dict)``.
    """
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
    # No action or action == "status": just return current state
    return 200, fan_state.status()


# ---------------------------------------------------------------------------
# Braiins HTTP server
# ---------------------------------------------------------------------------

ROUTES_BRAIINS = {
    "/api/v1/miner/details":       "miner_details",
    "/api/v1/cooling/state":       "cooling_state",
    "/api/v1/miner/stats":         "miner_stats",
    "/api/v1/miner/hw/hashboards": "hashboards",
    "/api/v1/miner/errors":        "miner_errors",
}


def _make_braiins_handler(
    fixtures: dict[str, Any],
    fan_state: FanState,
    auth: _AuthState,
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            logger.debug("braiins[%s]: " + fmt, self.server.server_address[1], *args)

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

        def do_POST(self):
            if self.path == "/api/v1/auth/login":
                self._read_body()
                # Issue the token so subsequent GETs are enforced
                token = fixtures["auth_login"]["token"]
                auth.issue(token)
                self._send_json(fixtures["auth_login"])
            elif self.path == "/control":
                status, resp = _dispatch_control(self._read_body(), fan_state)
                self._send_json(resp, status)
            else:
                self._send_json({"error": "not found"}, 404)

        def do_GET(self):
            # /control is always accessible — no auth needed for monitoring
            if self.path == "/control":
                self._send_json(fan_state.status())
                return

            # Enforce token on all data endpoints.
            # BraiinsCollector sets the header in lowercase: "authorization"
            if not auth.check(self.headers.get("authorization", "")):
                self._send_json({"error": "unauthorized"}, 401)
                return

            key = ROUTES_BRAIINS.get(self.path)
            if key is None:
                self._send_json({"error": "not found"}, 404)
                return

            # Substitute live fan RPMs for the cooling endpoint
            payload = (
                _inject_braiins_fans(fixtures[key], fan_state)
                if key == "cooling_state"
                else fixtures[key]
            )
            self._send_json(payload)

    return Handler


# ---------------------------------------------------------------------------
# Vnish HTTP server
# ---------------------------------------------------------------------------

ROUTES_VNISH = {
    "/api/v1/info":    "info",
    "/api/v1/summary": "summary",
    "/api/v1/status":  "status",
}


def _make_vnish_handler(
    fixtures: dict[str, Any],
    fan_state: FanState,
    auth: _AuthState,
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            logger.debug("vnish[%s]: " + fmt, self.server.server_address[1], *args)

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

        def do_POST(self):
            if self.path == "/api/v1/unlock":
                self._read_body()
                token = fixtures["unlock"]["token"]
                auth.issue(token)
                self._send_json(fixtures["unlock"])
            elif self.path == "/control":
                status, resp = _dispatch_control(self._read_body(), fan_state)
                self._send_json(resp, status)
            else:
                self._send_json({"error": "not found"}, 404)

        def do_GET(self):
            if self.path == "/control":
                self._send_json(fan_state.status())
                return

            # VnishCollector sets the header in titlecase: "Authorization"
            if not auth.check(self.headers.get("Authorization", "")):
                self._send_json({"error": "unauthorized"}, 401)
                return

            key = ROUTES_VNISH.get(self.path)
            if key is None:
                self._send_json({"error": "not found"}, 404)
                return

            # Substitute live fan RPMs for the status endpoint
            payload = (
                _inject_vnish_fans(fixtures[key], fan_state)
                if key == "status"
                else fixtures[key]
            )
            self._send_json(payload)

    return Handler


# ---------------------------------------------------------------------------
# LuxOS TCP/4028 server
# ---------------------------------------------------------------------------

LUXOS_COMMANDS = {
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


class _LuxOSHandler(socketserver.BaseRequestHandler):
    fixtures:  dict[str, Any] = {}
    fan_state: Optional[FanState] = None

    def handle(self):
        try:
            data = b""
            self.request.settimeout(2.0)
            while True:
                chunk = self.request.recv(4096)
                if not chunk:
                    break
                data += chunk
                try:
                    json.loads(data.decode("utf-8").rstrip("\x00"))
                    break
                except json.JSONDecodeError:
                    continue
        except socket.timeout:
            return
        except Exception:
            return

        try:
            req = json.loads(data.decode("utf-8").rstrip("\x00"))
        except Exception:
            return

        cmd = (req.get("command") or "").lower().strip()
        key = LUXOS_COMMANDS.get(cmd)
        if key is None:
            payload: dict[str, Any] = {
                "STATUS": [{"STATUS": "E", "Code": 14, "Msg": f"Invalid command: {cmd}"}],
                "id": 1,
            }
        else:
            payload = self.fixtures[key]
            # Substitute live fan RPMs for the fans command
            if key == "fans" and self.fan_state is not None:
                payload = _inject_luxos_fans(payload, self.fan_state)

        try:
            self.request.sendall(json.dumps(payload).encode("utf-8"))
        except Exception:
            pass


class _ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


# ---------------------------------------------------------------------------
# Global control HTTP server (broadcasts to all fake miners at once)
# ---------------------------------------------------------------------------

def _make_global_control_handler(fan_states: list[FanState]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            logger.debug("control: " + fmt, *args)

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
            self._send_json({
                "count":  len(fan_states),
                "miners": [fs.status() for fs in fan_states],
            })

        def do_POST(self):
            try:
                req = json.loads(self._read_body().decode() or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "invalid JSON"}, 400)
                return

            action = req.get("action", "")
            if action == "fan_dip":
                duration = float(req.get("duration_s", 8.0))
                for fs in fan_states:
                    fs.trigger_dip(duration)
                self._send_json({
                    "status": "ok", "action": "fan_dip",
                    "duration_s": duration, "miners": len(fan_states),
                })
            elif action == "fan_restore":
                for fs in fan_states:
                    fs.trigger_restore()
                self._send_json({
                    "status": "ok", "action": "fan_restore",
                    "miners": len(fan_states),
                })
            else:
                self._send_json({"error": f"unknown action: {action!r}"}, 400)

    return Handler


# ---------------------------------------------------------------------------
# Server lifecycle helpers
# ---------------------------------------------------------------------------

def _start_http_server(
    host: str,
    port: int,
    handler_cls: type[BaseHTTPRequestHandler],
    label: str,
) -> ThreadingHTTPServer:
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer((host, port), handler_cls)
    t = threading.Thread(target=server.serve_forever, name=f"{label}-{port}", daemon=True)
    t.start()
    logger.info("  %-7s up at http://%s:%d", label, host, port)
    return server


def _start_luxos_server(
    host: str,
    port: int,
    fixtures: dict[str, Any],
    fan_state: FanState,
) -> _ThreadedTCPServer:
    handler = type("Handler", (_LuxOSHandler,), {
        "fixtures":  fixtures,
        "fan_state": fan_state,
    })
    server = _ThreadedTCPServer((host, port), handler)
    t = threading.Thread(target=server.serve_forever, name=f"luxos-{host}", daemon=True)
    t.start()
    logger.info("  %-7s up at %s:%d", "luxos", host, port)
    return server


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _config_yaml(miners: list[dict[str, str]]) -> str:
    lines = ["miners:"]
    for m in miners:
        lines.append(f"  - name: {m['name']}")
        lines.append(f"    url: {m['url']}")
        lines.append(f"    firmware: {m['firmware']}")
        lines.append(f"    username: root")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run fake miners for local testing.")
    parser.add_argument("--braiins",       type=int, default=2,
                        help="Number of fake Braiins miners")
    parser.add_argument("--vnish",         type=int, default=2,
                        help="Number of fake Vnish miners")
    parser.add_argument("--luxos",         type=int, default=2,
                        help="Number of fake LuxOS miners (each needs a loopback alias on macOS)")
    parser.add_argument("--host",          default="127.0.0.1",
                        help="Host/IP to bind HTTP fakes to (default 127.0.0.1)")
    parser.add_argument("--base-port",     type=int, default=18000,
                        help="Base port: braiins=base+i, vnish=base+100+i (default 18000)")
    parser.add_argument("--luxos-base-ip", default="127.0.0.1",
                        help="First IP for LuxOS fakes; subsequent fakes use the next loopback IPs")
    parser.add_argument("--control-port",  type=int, default=18090,
                        help="Port for the global fan-control HTTP server (default 18090)")
    parser.add_argument("--print-config",  action="store_true",
                        help="Print a wright-telemetry miners: YAML snippet and exit")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    miners:  list[dict[str, str]] = []
    servers: list[Any] = []

    global _all_fan_states
    _all_fan_states = []

    # ------------------------------------------------------------------ Braiins
    if args.braiins > 0:
        base_fx = _load_fixtures("braiins")
        logger.info("Starting %d Braiins fake miner(s)…", args.braiins)
        for i in range(args.braiins):
            port     = args.base_port + i
            fx       = _personalize_braiins(base_fx, i)
            base_rpms = {f["position"]: f["rpm"]
                         for f in base_fx["cooling_state"]["fans"]}
            fs       = FanState(base_rpms, idx=i)
            auth     = _AuthState()
            _all_fan_states.append(fs)
            handler  = _make_braiins_handler(fx, fs, auth)
            servers.append(_start_http_server(args.host, port, handler, "braiins"))
            miners.append({
                "name":     f"braiins-fake-{i:03d}",
                "url":      f"http://{args.host}:{port}",
                "firmware": "braiins",
            })

    # ------------------------------------------------------------------ Vnish
    if args.vnish > 0:
        base_fx = _load_fixtures("vnish")
        logger.info("Starting %d Vnish fake miner(s)…", args.vnish)
        for i in range(args.vnish):
            port     = args.base_port + 100 + i
            fx       = _personalize_vnish(base_fx, i)
            base_rpms = {f["id"]: f["rpm"]
                         for f in base_fx["status"]["fans"]}
            fs       = FanState(base_rpms, idx=100 + i)
            auth     = _AuthState()
            _all_fan_states.append(fs)
            handler  = _make_vnish_handler(fx, fs, auth)
            servers.append(_start_http_server(args.host, port, handler, "vnish"))
            miners.append({
                "name":     f"vnish-fake-{i:03d}",
                "url":      f"http://{args.host}:{port}",
                "firmware": "vnish",
            })

    # ------------------------------------------------------------------ LuxOS
    if args.luxos > 0:
        base_fx = _load_fixtures("luxos")
        try:
            a, b, c, d = (int(p) for p in args.luxos_base_ip.split("."))
        except ValueError:
            logger.error("Invalid --luxos-base-ip %s", args.luxos_base_ip)
            return 2

        logger.info("Starting %d LuxOS fake miner(s)…", args.luxos)
        for i in range(args.luxos):
            ip       = f"{a}.{b}.{c}.{d + i}"
            fx       = _personalize_luxos(base_fx, i)
            base_rpms = {f["ID"]: f["RPM"]
                         for f in base_fx["fans"]["FANS"]}
            fs       = FanState(base_rpms, idx=200 + i)
            _all_fan_states.append(fs)
            try:
                servers.append(_start_luxos_server(ip, 4028, fx, fs))
            except OSError as exc:
                logger.error(
                    "Could not bind LuxOS fake to %s:4028 — %s\n"
                    "  On macOS, add a loopback alias first:\n"
                    "      sudo ifconfig lo0 alias %s up",
                    ip, exc, ip,
                )
                _all_fan_states.pop()  # remove the fan state we just added
                continue
            miners.append({
                "name":     f"luxos-fake-{i:03d}",
                "url":      f"http://{ip}",
                "firmware": "luxos",
            })

    if not servers:
        logger.error("No fake miners started — exiting.")
        return 1

    # ------------------------------------------------- Global control server
    ctrl_handler = _make_global_control_handler(_all_fan_states)
    servers.append(_start_http_server(args.host, args.control_port, ctrl_handler, "control"))

    # ----------------------------------------------------------------- Output
    print()
    print("=" * 68)
    print("Fake miners running.  Add the following to your wright-telemetry")
    print("config (~/.config/wright-telemetry/config.yaml or via --setup):")
    print("=" * 68)
    print(_config_yaml(miners))
    print("=" * 68)
    print(f"Total: {len(miners)} fake miner(s).  Press Ctrl-C to stop.")
    print()
    print(f"Fan control API  →  http://{args.host}:{args.control_port}")
    print(f"  GET  /           — status of all miners' fans")
    print(f"  POST /           — {{\"action\": \"fan_dip\",    \"duration_s\": 8}}")
    print(f"                     {{\"action\": \"fan_restore\"}}")
    print()
    print(f"Per-miner control →  http://<miner-host>:<port>/control  (same API)")
    print()
    print("SIGUSR1 broadcasts an 8-second fan dip to all fake miners at once.")
    print()

    if args.print_config:
        return 0

    # ------------------------------------------------------ SIGUSR1 → fan dip
    def _on_sigusr1(signum, _frame):
        n = len(_all_fan_states)
        logger.info("SIGUSR1: triggering 8 s fan dip on all %d miners", n)
        print(f"\n[SIGUSR1] Triggering 8 s fan dip on {n} fake miner(s)…")
        for fs in _all_fan_states:
            fs.trigger_dip(8.0)

    signal.signal(signal.SIGUSR1, _on_sigusr1)

    # --------------------------------------------------------- Wait for Ctrl-C
    stop = threading.Event()

    def _shutdown(signum, _frame):
        logger.info("Shutting down (signal %d)…", signum)
        stop.set()

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    try:
        stop.wait()
    finally:
        for s in servers:
            try:
                s.shutdown()
                s.server_close()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
