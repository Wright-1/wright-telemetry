#!/usr/bin/env python3
"""Ad-hoc Sealminer/bdminer API probe for field debugging.

Usage:
    python scripts/probe_sealminer_debug.py <miner-ip> [port]

Connects to the CGMiner-style TCP API and prints the raw response to each
command, plus timing and whether the server closed the socket.  This tells us
definitively whether:
  * port 4028 is reachable from this host (API network access / firewall)
  * the device responds to {"command": "version"} and with what keys
  * the connection closes after the reply (read-until-EOF is valid)
"""

from __future__ import annotations

import json
import socket
import sys
import time

PORT = 4028
TIMEOUT = 5  # generous for debugging


def probe(ip: str, command: str, port: int) -> None:
    print(f"\n=== command: {command!r} ===")
    payload = json.dumps({"command": command}).encode("utf-8")
    t0 = time.time()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(TIMEOUT)
            sock.connect((ip, port))
            sock.sendall(payload)
            chunks: list[bytes] = []
            closed_by_server = False
            while True:
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    print(f"  [recv timed out after {TIMEOUT}s — server did NOT close the socket]")
                    break
                if not chunk:
                    closed_by_server = True
                    break
                chunks.append(chunk)
        elapsed = time.time() - t0
        raw = b"".join(chunks)
        body = raw.decode("utf-8", errors="replace").rstrip("\x00")
        print(f"  bytes={len(raw)}  elapsed={elapsed:.2f}s  server_closed={closed_by_server}")
        print(f"  raw: {body[:1200]}")
        try:
            data = json.loads(body)
            print(f"  parsed keys: {list(data.keys())}")
            if command == "version":
                vl = data.get("VERSION", [])
                print(f"  VERSION[0] = {vl[0] if vl else '(empty)'}")
                print(f"  'Bdminer' in VERSION[0]? "
                      f"{('Bdminer' in vl[0]) if vl else False}")
        except json.JSONDecodeError as exc:
            print(f"  [NOT valid JSON: {exc}]")
    except (socket.timeout, OSError) as exc:
        print(f"  [connection failed: {type(exc).__name__}: {exc}]")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    ip = sys.argv[1]
    port = int(sys.argv[2]) if len(sys.argv) > 2 else PORT
    print(f"Probing {ip}:{port} ...")
    for cmd in ("version", "summary", "stats", "devdetails"):
        probe(ip, cmd, port)


if __name__ == "__main__":
    main()
