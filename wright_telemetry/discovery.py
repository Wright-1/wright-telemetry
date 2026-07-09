"""Network discovery of mining hardware.

Scans local subnets for miners running known firmware APIs.
Currently supports Braiins OS and LuxOS; Vnish probes can be added to
``_PROBES`` as they become available.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
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

# When enabled, the CGMiner-family probes (LuxOS, Sealminer) log the raw 4028
# response and the match outcome for every device that answers — so a field
# scan produces a diagnosable collector.log even without hardware access.
# Toggle via config ("discovery": {"debug": true}) which the GUI/scheduler push
# into this flag at startup, or the WRIGHT_DISCOVERY_DEBUG env var.
DISCOVERY_DEBUG = False


def _discovery_debug() -> bool:
    return DISCOVERY_DEBUG or os.environ.get(
        "WRIGHT_DISCOVERY_DEBUG", ""
    ).strip().lower() in ("1", "true", "yes", "on")


def apply_discovery_debug(cfg: dict[str, Any]) -> None:
    """Set the module-level :data:`DISCOVERY_DEBUG` flag from a config dict.

    Called by the GUI engine and scheduler at startup so a site can turn on
    verbose discovery logging with ``"discovery": {"debug": true}`` in its
    config, without an env var or code change.
    """
    global DISCOVERY_DEBUG
    DISCOVERY_DEBUG = bool((cfg.get("discovery") or {}).get("debug", False))


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
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0)
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
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
# CGMiner-style TCP API helpers (port 4028)
# ------------------------------------------------------------------

def _read_cgminer_response(
    sock: socket.socket, timeout: float
) -> Optional[dict[str, Any]]:
    """Read a single CGMiner-style JSON reply from an open socket.

    CGMiner-family APIs send one JSON object per command.  The classic server
    closes the connection afterwards, but some firmware — bdminer (Sealminer)
    in particular — leaves it open.  A naive read-until-EOF loop then blocks
    until the timeout and the reply is lost, so the miner is never discovered.

    We therefore return as soon as the accumulated buffer parses as JSON, and
    only fall back to EOF/timeout when it never completes.

    Returns the parsed dict, ``None`` if the peer sent nothing, and raises
    ``json.JSONDecodeError`` if bytes arrived but never formed valid JSON.
    """
    sock.settimeout(timeout)
    chunks: list[bytes] = []
    while True:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            break
        if not chunk:
            break
        chunks.append(chunk)
        buf = b"".join(chunks).rstrip(b"\x00")
        try:
            # Stop as soon as we have a complete object — don't wait for EOF.
            return json.loads(buf.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue  # reply not complete yet — keep reading

    buf = b"".join(chunks).rstrip(b"\x00")
    if not buf:
        return None
    return json.loads(buf.decode("utf-8"))


def _cgminer_query(
    ip: str, command: str, timeout: float = _PROBE_TIMEOUT
) -> Optional[dict[str, Any]]:
    """Open a fresh TCP connection to ``ip:4028`` and run one CGMiner command."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect((ip, 4028))
        sock.sendall(json.dumps({"command": command}).encode("utf-8"))
        return _read_cgminer_response(sock, timeout)


# ------------------------------------------------------------------
# Firmware probes – one function per firmware family
# ------------------------------------------------------------------

def _probe_braiins(ip: str) -> Optional[DiscoveredMiner]:
    """Hit the Braiins OS REST API; 200 or 401 (non-Digest) confirms a Braiins miner.

    Braiins OS returns 401 when API authentication is enabled, which is the
    default on most production installations.  We still treat a bare 401 as a
    positive so that auth-enabled miners are discovered and polled using the
    configured credentials.

    A 401 whose ``WWW-Authenticate`` header contains ``Digest`` is Bitmain
    stock firmware, not Braiins — those are excluded to avoid false positives.
    """
    url = f"http://{ip}/api/v1/miner/details"
    session = requests.Session()
    try:
        resp = session.get(url, timeout=_PROBE_TIMEOUT)
        if resp.status_code == 401:
            www_auth = resp.headers.get("WWW-Authenticate", "")
            if "Digest" in www_auth:
                return None
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
    finally:
        session.close()
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


def _probe_bitmain(ip: str) -> Optional[DiscoveredMiner]:
    """Hit the Antminer CGI system-info endpoint with HTTP Digest Auth.

    Positive signal: HTTP 200 JSON response containing a ``serinum`` field.
    The probe uses the default credentials (``root``/``root``); miners with
    non-default passwords will still be detected by the 401 signal but will
    need credentials supplied manually.
    """
    from requests.auth import HTTPDigestAuth

    url = f"http://{ip}/cgi-bin/get_system_info.cgi"
    session = requests.Session()
    try:
        resp = session.get(
            url,
            auth=HTTPDigestAuth("root", "root"),
            timeout=_PROBE_TIMEOUT,
        )
        if resp.status_code == 401:
            www_auth = resp.headers.get("WWW-Authenticate", "")
            if "Digest" in www_auth:
                return DiscoveredMiner(
                    ip=ip, firmware="bitmain",
                    hostname="", mac_address="",
                )
            return None
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not data.get("serinum"):
            return None
        return DiscoveredMiner(
            ip=ip,
            firmware="bitmain",
            hostname=data.get("hostname", ""),
            mac_address=data.get("macaddr", ""),
        )
    except Exception:
        return None
    finally:
        session.close()


def _probe_vnish(ip: str) -> Optional[DiscoveredMiner]:
    """Hit the Vnish REST API; require 200 JSON with ``firmware_version``.

    Treating 401 alone as Vnish caused false positives (e.g. other firmware
    returning 401 on ``/api/v1/info``). Miners that hide ``/api/v1/info``
    behind auth must be added manually or discovered after probe support
    for credentials is added.
    """
    url = f"http://{ip}/api/v1/info"
    session = requests.Session()
    try:
        resp = session.get(url, timeout=_PROBE_TIMEOUT)
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
    finally:
        session.close()
    return None


def _looks_like_sealminer(version_data: dict[str, Any]) -> bool:
    """Return True if a ``version`` reply fingerprints as Sealminer (bdminer).

    Older firmware (K10Pro) returns ``VERSION: [{"Bdminer": "4.11.1", ...}]``,
    but the model/key naming varies across the Sealminer line, so we also accept
    the ``bdminer`` marker that bdminer stamps into the STATUS ``Description``
    (e.g. ``"bdminer 4.11.1"``) or anywhere in the version object.  ``bdminer``
    is distinctive enough that this won't collide with LuxOS/Braiins/Vnish.
    """
    version_list = version_data.get("VERSION") or []
    if version_list and "BDMiner" in version_list[0]:
        return True
    blob = json.dumps(version_data).lower()
    return "bdminer" in blob


def _probe_sealminer(ip: str) -> Optional[DiscoveredMiner]:
    """Send a ``version`` command to port 4028; a ``bdminer`` marker confirms Sealminer."""
    try:
        data = _cgminer_query(ip, "version")
    except (socket.error, ValueError):
        return None

    if not data:
        return None

    # A device answered on 4028.  Log this unconditionally at INFO: it is the
    # single most useful discovery signal and is bounded by the number of hosts
    # with the API port open (i.e. miner count), not subnet size — so it stays
    # quiet on a normal network while making a failed field scan fully
    # diagnosable from a downloaded collector.log (no debug flag to set).  When
    # debug is enabled we widen the raw snippet for full-contract capture.
    matched = _looks_like_sealminer(data)
    snippet = json.dumps(data)[: 2000 if _discovery_debug() else 400]
    logger.info(
        "discovery: %s:4028 answered 'version' (sealminer match=%s): %s",
        ip, matched, snippet,
    )
    if not matched:
        return None

    mac = ""
    try:
        stats_data = _cgminer_query(ip, "stats")
        stats = (stats_data.get("STATS") or [{}])[0] if stats_data else {}
        mac = stats.get("MAC", "")
    except (socket.error, ValueError):
        pass
    return DiscoveredMiner(ip=ip, firmware="sealminer", hostname="", mac_address=mac)


_PROBES: dict[str, Callable[[str], Optional[DiscoveredMiner]]] = {
    "braiins": _probe_braiins,
    "luxos": _probe_luxos,
    "vnish": _probe_vnish,
    "bitmain": _probe_bitmain,
    "sealminer": _probe_sealminer,
}


def all_firmware_types() -> list[str]:
    """Return every firmware family with a registered probe.

    This is the single source of truth for "scan for everything".  Callers
    that need a non-empty default firmware list (e.g. the GUI engine) should
    use this instead of hardcoding names, so newly registered probes are
    picked up automatically and never silently omitted from discovery.
    """
    return list(_PROBES)


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


class IPRangeMatcher:
    """Membership test for an IP range (``start-end``), mirroring the
    ``in`` support that :class:`ipaddress.IPv4Network` gives CIDRs."""

    def __init__(self, start: "ipaddress.IPv4Address", end: "ipaddress.IPv4Address") -> None:
        self._start = int(start)
        self._end = int(end)

    def __contains__(self, addr: "ipaddress.IPv4Address") -> bool:
        return self._start <= int(addr) <= self._end


def parse_subnet_matcher(spec: str) -> "ipaddress.IPv4Network | IPRangeMatcher":
    """Parse a subnet *spec* (CIDR, range, or single IP) into an object
    supporting ``addr in matcher``, for grouping hosts by configured subnet.

    Raises ``ValueError`` if *spec* isn't a recognized format.
    """
    spec = spec.strip()

    if "/" in spec:
        return ipaddress.IPv4Network(spec, strict=False)

    if "-" in spec:
        start_str, end_str = spec.split("-", 1)
        start = ipaddress.IPv4Address(start_str.strip())
        end = ipaddress.IPv4Address(end_str.strip())
        if end < start:
            start, end = end, start
        return IPRangeMatcher(start, end)

    return ipaddress.IPv4Network(spec, strict=False)


def scan_hosts(
    hosts: list[str],
    firmware_types: Optional[list[str]] = None,
    progress_cb: Optional[ProgressCallback] = None,
    cancel_event: Optional["threading.Event"] = None,
) -> list[DiscoveredMiner]:
    """Probe a list of IP addresses for miners.

    Pass *cancel_event* to support early cancellation.  When the event is set
    the loop exits immediately and pending futures are dropped via
    ``shutdown(wait=False, cancel_futures=True)``; already-running probe
    threads finish naturally in the background (each has a short network
    timeout) without blocking the caller.
    """
    probes = {
        k: v for k, v in _PROBES.items()
        if firmware_types is None or k in firmware_types
    }
    if not probes or not hosts:
        return []

    logger.info(
        "discovery: scanning %d host(s) for firmware %s",
        len(hosts), ", ".join(probes),
    )

    total = len(hosts)
    discovered: list[DiscoveredMiner] = []
    scanned = 0
    num_probes = len(probes)
    cancelled = False

    pool = ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, total))
    try:
        future_map: dict[Any, str] = {}
        for ip in hosts:
            for probe_fn in probes.values():
                fut = pool.submit(probe_fn, ip)
                future_map[fut] = ip

        for fut in as_completed(future_map):
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
            scanned += 1
            if progress_cb and scanned % num_probes == 0:
                progress_cb(scanned // num_probes, total)
            result = fut.result()
            if result is not None:
                discovered.append(result)
    finally:
        # wait=False returns immediately; cancel_futures=True drops pending ones.
        # Already-running probes finish in daemon threads without blocking us.
        pool.shutdown(wait=False, cancel_futures=True)

    if cancelled:
        return discovered   # return partial results; caller checks cancel_event

    breakdown: dict[str, int] = {}
    for m in discovered:
        breakdown[m.firmware] = breakdown.get(m.firmware, 0) + 1
    if discovered:
        logger.info(
            "discovery: scan complete — %d miner(s) found across %d host(s): %s",
            len(discovered), total, breakdown,
        )
    else:
        # No matches.  Combined with the per-host "answered on 4028" lines above,
        # this pinpoints the cause from the log alone: if there are NO "answered
        # on 4028" lines, nothing is reachable on the API port (firewall / wrong
        # subnet / API not enabled); if there ARE such lines with match=False,
        # it's a firmware-fingerprint problem to fix in the probe.
        logger.warning(
            "discovery: scan complete — 0 miners found across %d host(s) "
            "(firmware probed: %s). If miners are present, check that they are "
            "reachable on TCP port 4028 from this machine and on the right subnet.",
            total, ", ".join(probes),
        )

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
