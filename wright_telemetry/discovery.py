"""Network discovery of mining hardware.

Scans local subnets for miners running known firmware APIs.
Currently supports Braiins OS and LuxOS; Vnish probes can be added to
``_PROBES`` as they become available.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable, Optional

import requests

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT = 2  # seconds per host
_MAX_WORKERS = 128


# ------------------------------------------------------------------
# Data
# ------------------------------------------------------------------

@dataclass
class DiscoveredMiner:
    ip: str
    firmware: str  # "braiins", "luxos", "vnish", …
    hostname: str
    mac_address: str


# ------------------------------------------------------------------
# Local network helpers
# ------------------------------------------------------------------

def get_local_ip() -> Optional[str]:
    """Return the primary LAN IP of this machine (best-effort)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def default_subnet() -> Optional[str]:
    """Return the /24 subnet that contains the local IP, or *None*."""
    ip = get_local_ip()
    if ip is None:
        return None
    return str(ipaddress.IPv4Network(f"{ip}/24", strict=False))


# ------------------------------------------------------------------
# Firmware probes – one function per firmware family
# ------------------------------------------------------------------

def _probe_braiins(ip: str) -> Optional[DiscoveredMiner]:
    """Hit the Braiins OS REST API; 200 or 401 means it's a Braiins miner."""
    url = f"http://{ip}/api/v1/miner/details"
    try:
        resp = requests.get(url, timeout=_PROBE_TIMEOUT)
        if resp.status_code in (200, 401):
            hostname = ""
            mac = ""
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    hostname = data.get("hostname", "")
                    mac = data.get("mac_address", "")
                except Exception:
                    pass
            return DiscoveredMiner(
                ip=ip, firmware="braiins",
                hostname=hostname, mac_address=mac,
            )
    except (requests.ConnectionError, requests.Timeout, OSError):
        pass
    return None


def _probe_luxos(ip: str) -> Optional[DiscoveredMiner]:
    """Send a ``version`` command to port 4028; a LUXminer response means LuxOS."""
    import json as _json
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(_PROBE_TIMEOUT)
            sock.connect((ip, 4028))
            sock.sendall(b'{"command": "version"}')
            chunks: list[bytes] = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        body = b"".join(chunks).decode("utf-8").rstrip("\x00")
        data = _json.loads(body)
        version_list = data.get("VERSION", [])
        if not version_list:
            return None
        ver = version_list[0]
        if "LUXminer" not in ver:
            return None
        hostname = ""
        mac = ""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock2:
                sock2.settimeout(_PROBE_TIMEOUT)
                sock2.connect((ip, 4028))
                sock2.sendall(b'{"command": "config"}')
                cfg_chunks: list[bytes] = []
                while True:
                    c = sock2.recv(4096)
                    if not c:
                        break
                    cfg_chunks.append(c)
            cfg_body = b"".join(cfg_chunks).decode("utf-8").rstrip("\x00")
            cfg = _json.loads(cfg_body)
            cfg_data = (cfg.get("CONFIG") or [{}])[0]
            hostname = cfg_data.get("Hostname", "")
            mac = cfg_data.get("MACAddr", "")
        except Exception:
            pass
        return DiscoveredMiner(ip=ip, firmware="luxos", hostname=hostname, mac_address=mac)
    except (socket.error, ValueError, _json.JSONDecodeError):
        pass
    return None


def _probe_vnish(ip: str) -> Optional[DiscoveredMiner]:
    """Hit the Vnish REST API; require 200 JSON with ``firmware_version``.

    Treating 401 alone as Vnish caused false positives (e.g. other firmware
    returning 401 on ``/api/v1/info``). Miners that hide ``/api/v1/info``
    behind auth must be added manually or discovered after probe support
    for credentials is added.
    """
    url = f"http://{ip}/api/v1/info"
    try:
        resp = requests.get(url, timeout=_PROBE_TIMEOUT)
        if resp.status_code != 200:
            return None
        try:
            data = resp.json()
        except Exception:
            return None
        if not data.get("firmware_version"):
            return None
        return DiscoveredMiner(
            ip=ip, firmware="vnish",
            hostname=data.get("hostname", ""),
            mac_address=data.get("mac", ""),
        )
    except (requests.ConnectionError, requests.Timeout, OSError):
        pass
    return None


_PROBES: dict[str, Callable[[str], Optional[DiscoveredMiner]]] = {
    "braiins": _probe_braiins,
    "luxos": _probe_luxos,
    "vnish": _probe_vnish,
}


def firmware_types_for_collector(collector_type: str) -> Optional[list[str]]:
    """Map config ``collector_type`` to discovery probe keys.

    Returns a single-firmware list so we only hit APIs for the rig the user
    selected. Returns ``None`` if *collector_type* is unknown so discovery
    falls back to probing every registered firmware (forward compatibility).
    """
    key = (collector_type or "").strip().lower() or "braiins"
    if key in _PROBES:
        return [key]
    return None


# ------------------------------------------------------------------
# Scanning
# ------------------------------------------------------------------

ProgressCallback = Callable[[int, int], None]


def parse_ip_target(target: str) -> list[str]:
    """Parse a target string into a list of individual IP addresses.

    Accepted formats:
        CIDR      – ``192.168.1.0/24``
        Range     – ``192.168.1.100-192.168.1.200``
        Single IP – ``192.168.1.50``
    """
    target = target.strip()

    if "/" in target:
        network = ipaddress.IPv4Network(target, strict=False)
        return [str(ip) for ip in network.hosts()]

    if "-" in target:
        start_str, end_str = target.split("-", 1)
        start = ipaddress.IPv4Address(start_str.strip())
        end = ipaddress.IPv4Address(end_str.strip())
        if end < start:
            start, end = end, start
        return [str(ipaddress.IPv4Address(i)) for i in range(int(start), int(end) + 1)]

    ipaddress.IPv4Address(target)
    return [target]


def scan_hosts(
    hosts: list[str],
    firmware_types: Optional[list[str]] = None,
    progress_cb: Optional[ProgressCallback] = None,
) -> list[DiscoveredMiner]:
    """Probe a list of IP addresses for miners."""
    probes = {
        k: v for k, v in _PROBES.items()
        if firmware_types is None or k in firmware_types
    }
    if not probes or not hosts:
        return []

    total = len(hosts)
    discovered: list[DiscoveredMiner] = []
    scanned = 0
    num_probes = len(probes)

    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, total)) as pool:
        future_map: dict[Any, str] = {}
        for ip in hosts:
            for probe_fn in probes.values():
                fut = pool.submit(probe_fn, ip)
                future_map[fut] = ip

        for fut in as_completed(future_map):
            scanned += 1
            if progress_cb and scanned % num_probes == 0:
                progress_cb(scanned // num_probes, total)
            result = fut.result()
            if result is not None:
                discovered.append(result)

    discovered.sort(key=lambda m: tuple(int(p) for p in m.ip.split(".")))
    return discovered


def scan_subnet(
    subnet: str,
    firmware_types: Optional[list[str]] = None,
    progress_cb: Optional[ProgressCallback] = None,
) -> list[DiscoveredMiner]:
    """Scan *subnet* (CIDR) for miners, returning those that respond."""
    try:
        hosts = parse_ip_target(subnet)
    except ValueError as exc:
        logger.error("Invalid target %r: %s", subnet, exc)
        return []
    return scan_hosts(hosts, firmware_types, progress_cb)


def discover_miners(
    subnets: Optional[list[str]] = None,
    firmware_types: Optional[list[str]] = None,
    progress_cb: Optional[ProgressCallback] = None,
) -> list[DiscoveredMiner]:
    """High-level entry point: scan one or more subnets for miners.

    If *subnets* is ``None`` the local /24 is auto-detected.
    """
    if not subnets:
        auto = default_subnet()
        if auto is None:
            logger.error("Could not detect local network — specify subnets manually.")
            return []
        subnets = [auto]

    all_miners: list[DiscoveredMiner] = []
    for subnet in subnets:
        logger.info("Scanning %s for miners…", subnet)
        all_miners.extend(scan_subnet(subnet, firmware_types, progress_cb))

    return all_miners


# ------------------------------------------------------------------
# Helpers used by the scheduler to convert discovery results → config
# ------------------------------------------------------------------

def discovered_to_miner_cfgs(
    miners: list[DiscoveredMiner],
    default_username: str = "root",
    default_password_b64: str = "",
) -> list[dict[str, Any]]:
    """Convert a list of :class:`DiscoveredMiner` to miner config dicts."""
    cfgs: list[dict[str, Any]] = []
    for m in miners:
        entry: dict[str, Any] = {
            "name": m.hostname or m.ip,
            "url": f"http://{m.ip}",
            "username": default_username,
            "discovered": True,
            "firmware": m.firmware,
        }
        if m.mac_address:
            entry["mac_address"] = m.mac_address
        if default_password_b64:
            entry["password_b64"] = default_password_b64
        cfgs.append(entry)
    return cfgs


def merge_miners(
    manual: list[dict[str, Any]],
    discovered: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge manually-configured miners with discovered ones.

    MAC address is the primary deduplication key; URL is the fallback.
    When a discovered miner's MAC matches an existing entry at a different
    URL, the existing entry's URL is updated to reflect the new IP so that
    a miner that obtained a new DHCP lease is not treated as a new device.
    """
    merged = [dict(m) for m in manual]

    # Build MAC → index map for fast lookup (only entries that have a MAC)
    mac_to_idx: dict[str, int] = {
        m["mac_address"]: i
        for i, m in enumerate(merged)
        if m.get("mac_address")
    }
    known_urls = {m["url"] for m in merged}

    for d in discovered:
        d_mac = d.get("mac_address")
        if d_mac and d_mac in mac_to_idx:
            # Known miner — update its URL if the IP changed
            idx = mac_to_idx[d_mac]
            if merged[idx]["url"] != d["url"]:
                logger.info(
                    "Miner %s (%s) moved: %s → %s",
                    merged[idx].get("name", d_mac), d_mac,
                    merged[idx]["url"], d["url"],
                )
                merged[idx]["url"] = d["url"]
                known_urls = {m["url"] for m in merged}  # refresh after update
        elif d["url"] not in known_urls:
            merged.append(d)
            if d_mac:
                mac_to_idx[d_mac] = len(merged) - 1
            known_urls.add(d["url"])

    return merged


# ------------------------------------------------------------------
# Interactive console helpers (used by the setup wizard)
# ------------------------------------------------------------------

def _cli_progress(scanned: int, total: int) -> None:
    sys.stdout.write(f"\r  Scanning… {scanned}/{total}")
    sys.stdout.flush()


def run_interactive_discovery(
    subnets: Optional[list[str]] = None,
    firmware_types: Optional[list[str]] = None,
) -> list[DiscoveredMiner]:
    """Run discovery with a live progress line on stdout."""
    miners = discover_miners(
        subnets=subnets, firmware_types=firmware_types, progress_cb=_cli_progress,
    )
    sys.stdout.write("\r" + " " * 40 + "\r")  # clear progress line
    sys.stdout.flush()
    return miners


def run_interactive_range_scan(
    target: str,
    firmware_types: Optional[list[str]] = None,
) -> list[DiscoveredMiner]:
    """Parse *target* (CIDR, range, or single IP) and scan with progress."""
    try:
        hosts = parse_ip_target(target)
    except ValueError as exc:
        logger.error("Invalid target %r: %s", target, exc)
        return []
    miners = scan_hosts(hosts, firmware_types, progress_cb=_cli_progress)
    sys.stdout.write("\r" + " " * 40 + "\r")
    sys.stdout.flush()
    return miners
