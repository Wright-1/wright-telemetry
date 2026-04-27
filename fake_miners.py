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

Each fake mutates a few numeric fields per-miner (hashrate, temps, MAC
address, hostname) so they look distinct in the dashboard.

When --print-config is given, the script prints a YAML snippet you can
paste into wright-telemetry's config under ``miners:`` to point the
collector at the fakes.
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import random
import signal
import socket
import socketserver
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

logger = logging.getLogger("fake_miners")

FIXTURES = Path(__file__).parent / "tests" / "fixtures"


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
    return round(value * (1.0 + random.uniform(-pct, pct)), 3)


def _personalize_braiins(fx: dict[str, Any], idx: int) -> dict[str, Any]:
    fx = copy.deepcopy(fx)
    details = fx.get("miner_details", {})
    details["hostname"] = f"braiins-fake-{idx:03d}"
    details["mac_address"] = _fake_mac(idx)
    stats = fx.get("miner_stats", {})
    for key in ("real_hashrate", "nominal_hashrate"):
        node = stats.get(key)
        if isinstance(node, dict) and "gigahash_per_second" in node:
            node["gigahash_per_second"] = _jitter(node["gigahash_per_second"])
    return fx


def _personalize_vnish(fx: dict[str, Any], idx: int) -> dict[str, Any]:
    fx = copy.deepcopy(fx)
    info = fx.get("info", {})
    info["hostname"] = f"vnish-fake-{idx:03d}"
    info["mac"] = _fake_mac(idx + 0x1000)
    info["serial"] = f"VNFAKE{idx:05d}"
    return fx


def _personalize_luxos(fx: dict[str, Any], idx: int) -> dict[str, Any]:
    fx = copy.deepcopy(fx)
    cfg = fx.get("config", {}).get("CONFIG")
    if isinstance(cfg, list) and cfg:
        cfg[0]["Hostname"] = f"luxos-fake-{idx:03d}"
        cfg[0]["MACAddr"] = _fake_mac(idx + 0x2000)
    return fx


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


def _make_braiins_handler(fixtures: dict[str, Any]) -> type[BaseHTTPRequestHandler]:
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

        def do_POST(self):
            if self.path == "/api/v1/auth/login":
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                self._send_json(fixtures["auth_login"])
            else:
                self._send_json({"error": "not found"}, status=404)

        def do_GET(self):
            key = ROUTES_BRAIINS.get(self.path)
            if key is None:
                self._send_json({"error": "not found"}, status=404)
                return
            self._send_json(fixtures[key])

    return Handler


# ---------------------------------------------------------------------------
# Vnish HTTP server
# ---------------------------------------------------------------------------

ROUTES_VNISH = {
    "/api/v1/info":    "info",
    "/api/v1/summary": "summary",
    "/api/v1/status":  "status",
}


def _make_vnish_handler(fixtures: dict[str, Any]) -> type[BaseHTTPRequestHandler]:
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

        def do_POST(self):
            if self.path == "/api/v1/unlock":
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                self._send_json(fixtures["unlock"])
            else:
                self._send_json({"error": "not found"}, status=404)

        def do_GET(self):
            key = ROUTES_VNISH.get(self.path)
            if key is None:
                self._send_json({"error": "not found"}, status=404)
                return
            self._send_json(fixtures[key])

    return Handler


# ---------------------------------------------------------------------------
# LuxOS TCP/4028 server
# ---------------------------------------------------------------------------

# Map cgminer-style "command" strings → fixture stem
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
    fixtures: dict[str, Any] = {}

    def handle(self):
        try:
            data = b""
            self.request.settimeout(2.0)
            while True:
                chunk = self.request.recv(4096)
                if not chunk:
                    break
                data += chunk
                # cgminer protocol sends a single JSON request per connection.
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
            payload = {
                "STATUS": [{"STATUS": "E", "Code": 14, "Msg": f"Invalid command: {cmd}"}],
                "id": 1,
            }
        else:
            payload = self.fixtures[key]

        body = json.dumps(payload).encode("utf-8")
        try:
            self.request.sendall(body)
        except Exception:
            pass


class _ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

def _start_http_server(host: str, port: int, handler_cls, label: str) -> ThreadingHTTPServer:
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer((host, port), handler_cls)
    t = threading.Thread(target=server.serve_forever, name=f"{label}-{port}", daemon=True)
    t.start()
    logger.info("  %-7s up at http://%s:%d", label, host, port)
    return server


def _start_luxos_server(host: str, port: int, fixtures: dict[str, Any]) -> _ThreadedTCPServer:
    handler = type("Handler", (_LuxOSHandler,), {"fixtures": fixtures})
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
    parser.add_argument("--braiins", type=int, default=2, help="Number of fake Braiins miners")
    parser.add_argument("--vnish",   type=int, default=2, help="Number of fake Vnish miners")
    parser.add_argument("--luxos",   type=int, default=2,
                        help="Number of fake LuxOS miners (each needs a loopback alias on macOS)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Host/IP to bind HTTP fakes to (default 127.0.0.1)")
    parser.add_argument("--base-port", type=int, default=18000,
                        help="Base port: braiins=base+i, vnish=base+100+i (default 18000)")
    parser.add_argument("--luxos-base-ip", default="127.0.0.1",
                        help="First IP for LuxOS fakes; subsequent fakes use the next loopback IPs")
    parser.add_argument("--print-config", action="store_true",
                        help="Print a wright-telemetry miners: YAML snippet and exit-friendly info")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    miners: list[dict[str, str]] = []
    servers: list[Any] = []

    # --- Braiins
    if args.braiins > 0:
        base_fx = _load_fixtures("braiins")
        logger.info("Starting %d Braiins fake miner(s)…", args.braiins)
        for i in range(args.braiins):
            port = args.base_port + i
            fx = _personalize_braiins(base_fx, i)
            servers.append(_start_http_server(args.host, port, _make_braiins_handler(fx), "braiins"))
            miners.append({
                "name": f"braiins-fake-{i:03d}",
                "url":  f"http://{args.host}:{port}",
                "firmware": "braiins",
            })

    # --- Vnish
    if args.vnish > 0:
        base_fx = _load_fixtures("vnish")
        logger.info("Starting %d Vnish fake miner(s)…", args.vnish)
        for i in range(args.vnish):
            port = args.base_port + 100 + i
            fx = _personalize_vnish(base_fx, i)
            servers.append(_start_http_server(args.host, port, _make_vnish_handler(fx), "vnish"))
            miners.append({
                "name": f"vnish-fake-{i:03d}",
                "url":  f"http://{args.host}:{port}",
                "firmware": "vnish",
            })

    # --- LuxOS  (port 4028 is hard-coded in the collector)
    if args.luxos > 0:
        base_fx = _load_fixtures("luxos")
        # Increment last octet of the base IP for each subsequent fake.
        try:
            a, b, c, d = (int(p) for p in args.luxos_base_ip.split("."))
        except ValueError:
            logger.error("Invalid --luxos-base-ip %s", args.luxos_base_ip)
            return 2

        logger.info("Starting %d LuxOS fake miner(s)…", args.luxos)
        for i in range(args.luxos):
            ip = f"{a}.{b}.{c}.{d + i}"
            fx = _personalize_luxos(base_fx, i)
            try:
                servers.append(_start_luxos_server(ip, 4028, fx))
            except OSError as exc:
                logger.error(
                    "Could not bind LuxOS fake to %s:4028 — %s\n"
                    "  On macOS, add a loopback alias first:\n"
                    "      sudo ifconfig lo0 alias %s up",
                    ip, exc, ip,
                )
                continue
            miners.append({
                "name": f"luxos-fake-{i:03d}",
                "url":  f"http://{ip}",
                "firmware": "luxos",
            })

    if not servers:
        logger.error("No fake miners started — exiting.")
        return 1

    # Pretty-print the config snippet
    print()
    print("=" * 68)
    print("Fake miners running.  Add the following to your wright-telemetry")
    print("config (~/.config/wright-telemetry/config.yaml or via --setup):")
    print("=" * 68)
    print(_config_yaml(miners))
    print("=" * 68)
    print(f"Total: {len(miners)} fake miner(s).  Press Ctrl-C to stop.")
    print()

    if args.print_config:
        return 0

    # Wait for Ctrl-C
    stop = threading.Event()

    def _shutdown(signum, _frame):
        logger.info("Shutting down (signal %d)…", signum)
        stop.set()

    signal.signal(signal.SIGINT, _shutdown)
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
