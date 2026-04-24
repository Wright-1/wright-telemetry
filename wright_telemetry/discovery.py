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
import threading
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


def default_subnets() -> list[str]:
    """Return /24 CIDRs for all detected local interfaces, excluding loopback.

    Uses ``socket.getaddrinfo`` on the local hostname as the primary
    cross-platform method (no psutil, no netifaces, no fcntl).  Supplements
    with the UDP-trick IP from :func:`get_local_ip` so that machines with
    unusual hostname resolution still get at least one subnet.

    Returns an empty list if nothing can be detected.
    """
    ips: list[str] = []

    # Primary: hostname-based getaddrinfo — covers most multi-interface setups
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        for info in infos:
            ip = info[4][0]
            if not ip.startswith("127."):
                ips.append(ip)
    except Exception:
        pass

    # Supplement: UDP trick picks up the default-route interface even if
    # hostname resolution is misconfigured or returns only loopback
    udp_ip = get_local_ip()
    if udp_ip and not udp_ip.startswith("127.") and udp_ip not in ips:
        ips.append(udp_ip)

    # Deduplicate and map each IP → its /24
    seen: set[str] = set()
    subnets: list[str] = []
    for ip in ips:
        subnet = str(ipaddress.IPv4Network(f"{ip}/24", strict=False))
        if subnet not in seen:
            seen.add(subnet)
            subnets.append(subnet)

    return subnets


def default_subnet() -> Optional[str]:
    """Return the primary /24 subnet, or *None* (backwards-compat wrapper)."""
    subnets = default_subnets()
    return subnets[0] if subnets else None


def _load_subnets_xlsx(path: str) -> list[str]:
    """Extract CIDR strings from an Excel workbook.

    Scans every cell in every sheet; returns any cell value that looks like a
    CIDR (contains '/') or an IP range (contains '-'), skipping the header row
    if the first sheet has one.
    """
    try:
        import openpyxl  # optional dependency
    except ImportError as exc:
        raise ImportError(
            "openpyxl is required to load .xlsx subnet files.  "
            "Install it with: pip install openpyxl"
        ) from exc

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    subnets: list[str] = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            for cell in row:
                if not isinstance(cell, str):
                    continue
                val = cell.strip()
                if ("/" in val or "-" in val) and val[0].isdigit():
                    subnets.append(val)
    wb.close()
    return subnets


def load_subnets_file(path: str) -> list[str]:
    """Parse a subnets file and return a list of CIDR/range strings.

    Supports:
        - ``.xlsx`` workbooks — any cell containing a CIDR (``x.x.x.x/n``) or
          IP range (``x.x.x.x-y.y.y.y``) is collected.  Requires ``openpyxl``.
        - Plain-text files — one entry per line; lines starting with ``#`` and
          blank lines are skipped.

    Raises:
        OSError: if the file cannot be opened
        ImportError: if an .xlsx file is given but openpyxl is not installed
    """
    if path.lower().endswith(".xlsx"):
        return _load_subnets_xlsx(path)

    subnets: list[str] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            subnets.append(stripped)
    return subnets


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


def firmware_types_for_collector(
    collector_type: "str | list[str]",
) -> Optional[list[str]]:
    """Map config ``collector_types`` (or legacy ``collector_type``) to probe keys.

    Accepts a list (new format) or a single string (backwards-compat).
    Returns only the entries that match a registered probe.
    Returns ``None`` if nothing matches so discovery falls back to all probes.
    """
    if isinstance(collector_type, list):
        types = [t.strip().lower() for t in collector_type if t]
    else:
        types = [(collector_type or "").strip().lower() or "braiins"]

    valid = [t for t in types if t in _PROBES]
    return valid if valid else None


def _register_miner_with_api(miner: "DiscoveredMiner") -> None:
    """POST a newly discovered miner to the Wright Fan miners table.

    TODO: implement once API endpoint is confirmed.
    Needs wright_api_key, facility_id, and the API base URL from config.
    """
    logger.debug(
        "TODO: register miner ip=%s firmware=%s mac=%s with API",
        miner.ip, miner.firmware, miner.mac_address,
    )


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

    If *subnets* is ``None`` all local interface subnets are auto-detected.
    """
    if not subnets:
        subnets = default_subnets()
        if not subnets:
            logger.error(
                "Could not detect local network. "
                "To fix: run 'wright-telemetry --subnets-file FILE' with a text file "
                "containing one CIDR per line, or re-run 'wright-telemetry --setup' "
                "and enter your subnet(s) manually."
            )
            return []

    all_hosts: list[str] = []
    for subnet in subnets:
        logger.info("Scanning %s for miners…", subnet)
        try:
            all_hosts.extend(parse_ip_target(subnet))
        except ValueError as exc:
            logger.error("Invalid target %r: %s", subnet, exc)

    return scan_hosts(all_hosts, firmware_types, progress_cb)


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
