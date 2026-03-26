"""Network discovery of mining hardware.

Scans local subnets for miners running known firmware APIs.
Currently supports Braiins OS; Luxor and Vnish probes can be added to
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


# Future probes go here:
# def _probe_luxos(ip: str) -> Optional[DiscoveredMiner]: ...
# def _probe_vnish(ip: str) -> Optional[DiscoveredMiner]: ...

_PROBES: dict[str, Callable[[str], Optional[DiscoveredMiner]]] = {
    "braiins": _probe_braiins,
}


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
        if default_password_b64:
            entry["password_b64"] = default_password_b64
        cfgs.append(entry)
    return cfgs


def merge_miners(
    manual: list[dict[str, Any]],
    discovered: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge manually-configured miners with discovered ones (manual wins)."""
    manual_urls = {m["url"] for m in manual}
    merged = list(manual)
    for d in discovered:
        if d["url"] not in manual_urls:
            merged.append(d)
    return merged


# ------------------------------------------------------------------
# Interactive console helpers (used by the setup wizard)
# ------------------------------------------------------------------

def _cli_progress(scanned: int, total: int) -> None:
    sys.stdout.write(f"\r  Scanning… {scanned}/{total}")
    sys.stdout.flush()


def run_interactive_discovery(
    subnets: Optional[list[str]] = None,
) -> list[DiscoveredMiner]:
    """Run discovery with a live progress line on stdout."""
    miners = discover_miners(subnets=subnets, progress_cb=_cli_progress)
    sys.stdout.write("\r" + " " * 40 + "\r")  # clear progress line
    sys.stdout.flush()
    return miners


def run_interactive_range_scan(target: str) -> list[DiscoveredMiner]:
    """Parse *target* (CIDR, range, or single IP) and scan with progress."""
    try:
        hosts = parse_ip_target(target)
    except ValueError as exc:
        logger.error("Invalid target %r: %s", target, exc)
        return []
    miners = scan_hosts(hosts, progress_cb=_cli_progress)
    sys.stdout.write("\r" + " " * 40 + "\r")
    sys.stdout.flush()
    return miners
