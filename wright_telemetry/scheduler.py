"""Polling loop with two-layer fault tolerance.

Inner layer: per-miner / per-metric try/except -- a single failure never
             kills the loop.
Outer layer: top-level crash recovery with exponential backoff -- if
             something truly unexpected happens the loop restarts from
             scratch (re-auth, re-fetch identities, resume polling).
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import asdict
from typing import Any

from wright_telemetry import __version__
from wright_telemetry.api_client import WrightAPIClient
from wright_telemetry.baseline import BaselineTracker
from wright_telemetry.collectors.base import MinerCollector
from wright_telemetry.collectors.factory import CollectorFactory
from wright_telemetry.config import decode_password, load_config, mask_config
from wright_telemetry.consent import DEFAULT_CONSENT, consented_metrics
from wright_telemetry.discovery import (
    apply_discovery_debug,
    discover_miners,
    discovered_to_miner_cfgs,
    firmware_types_for_collector,
    parse_subnet_matcher,
)
from wright_telemetry.models import (
    CoolingData,
    MinerIdentity,
    SubnetScanSummary,
    ScanSummaryData,
    TelemetryPayload,
)

logger = logging.getLogger(__name__)

_MAX_BACKOFF = 300  # 5 minutes
_FD_WARN_THRESHOLD = 100  # warn if FDs grow by this much from startup baseline
_FD_CHECK_INTERVAL = 300  # only check once every N seconds


def _check_fd_growth(baseline: int, last_check: float) -> tuple[int, float]:
    now = time.monotonic()
    if now - last_check < _FD_CHECK_INTERVAL:
        return baseline, last_check
    import psutil
    count = psutil.Process().num_fds()
    grown = count - baseline
    if grown >= _FD_WARN_THRESHOLD:
        logger.warning(
            "File descriptor count has grown by %d since startup "
            "(current: %d, baseline: %d). Possible connection leak — "
            "check logs for unclosed sessions.",
            grown, count, baseline,
        )
    return baseline, now



def _resolve_miners(cfg: dict[str, Any], controller: Any = None) -> list[dict[str, Any]]:
    """Return miners to poll: config miners merged with any discovered ones.

    In GUI mode (*controller* is provided) the shared discovery store is read
    directly — no network scan is run here.  The ScanManager owns all scanning
    and keeps the store up to date; the scheduler just consumes the results.

    In CLI / headless mode (*controller* is None) the original subnet-scan
    path is used so the agent works without the GUI.

    Miners are never written to the config file; the server upserts them
    automatically from the ``miner_identity`` field on every telemetry payload.
    """
    discovery_cfg = cfg.get("discovery", {})
    default_user   = discovery_cfg.get("default_username",    "root")
    default_pw_b64 = discovery_cfg.get("default_password_b64", "")

    # A GUI ScanManager is wired up: its scans already keep the shared store
    # up to date, so just read from it instead of scanning again here.
    if controller is not None and getattr(controller, "has_scan_manager", False):
        raw = controller.get_discovered_miners()
        return [
            {
                **m,
                "username":     m.get("username",     default_user),
                "password_b64": m.get("password_b64", default_pw_b64),
            }
            for m in raw
        ]

    # No controller (standalone / headless): run a direct subnet scan.
    if not discovery_cfg.get("enabled", False):
        return []

    apply_discovery_debug(cfg)
    subnets = discovery_cfg.get("subnets")
    collector_types = cfg.get("collector_types") or cfg.get("collector_type", "braiins")
    firmware_types = firmware_types_for_collector(collector_types)
    found = discover_miners(subnets=subnets, firmware_types=firmware_types)
    logger.info("Discovered %d miner(s) via subnet scan", len(found))
    return discovered_to_miner_cfgs(found, default_user, default_pw_b64)


def _build_collectors(
    miners: list[dict[str, Any]],
    default_collector_type: str = "braiins",
) -> list[tuple[dict[str, Any], MinerCollector]]:
    """Instantiate a collector for each miner config dict."""
    collectors: list[tuple[dict[str, Any], MinerCollector]] = []

    for miner_cfg in miners:
        password = ""
        if miner_cfg.get("password_b64"):
            password = decode_password(miner_cfg["password_b64"])

        collector_type = miner_cfg.get("firmware", default_collector_type)
        collector = CollectorFactory.create(
            name=collector_type,
            url=miner_cfg["url"],
            username=miner_cfg.get("username"),
            password=password,
        )
        collectors.append((miner_cfg, collector))
    return collectors


def _authenticate_all(collectors: list[tuple[dict[str, Any], MinerCollector]]) -> None:
    for miner_cfg, collector in collectors:
        name = miner_cfg.get("name", miner_cfg["url"])
        try:
            collector.authenticate()
        except Exception as exc:
            logger.warning("Auth failed for %s: %s", name, exc)


def _fetch_identities(
    collectors: list[tuple[dict[str, Any], MinerCollector]],
) -> dict[str, MinerIdentity]:
    """Fetch and cache miner identities keyed by miner URL.

    Also back-propagates the discovered MAC address into the miner config dict
    so that subsequent re-discovery cycles can use it for deduplication.
    """
    identities: dict[str, MinerIdentity] = {}
    for miner_cfg, collector in collectors:
        url = miner_cfg["url"]
        name = miner_cfg.get("name", url)
        # Derive the IP from the URL for storage in the identity
        ip = url.removeprefix("http://").removeprefix("https://").split("/")[0].split(":")[0]
        try:
            identity = collector.fetch_identity()
            identity.ip_address = ip
            # Back-propagate identity fields into miner_cfg for in-process use
            # (e.g. MAC-based deduplication in the re-discovery loop)
            for field, value in [
                ("uid", identity.uid),
                ("serial_number", identity.serial_number),
                ("hostname", identity.hostname),
                ("mac_address", identity.mac_address),
                ("ip_address", identity.ip_address),
                ("firmware", identity.firmware),
            ]:
                if value and miner_cfg.get(field) != value:
                    miner_cfg[field] = value
            identities[url] = identity
            logger.info(
                "Identified miner '%s': uid=%s, serial=%s, mac=%s, ip=%s, firmware=%s",
                name, identity.uid, identity.serial_number,
                identity.mac_address, ip, identity.firmware,
            )
        except Exception as exc:
            logger.warning("Could not fetch identity for '%s': %s", name, exc)
            identities[url] = MinerIdentity(
                uid="unknown", serial_number="unknown",
                hostname=name, mac_address="unknown",
                ip_address=ip,
                firmware=miner_cfg.get("firmware"),
            )
    return identities






def _print_baseline_dashboard(name: str, baseline: Any) -> None:
    """Print a human-readable baseline summary to the terminal."""
    temp_line = ""
    if baseline.baseline_temp is not None:
        temp_line = (
            f"\n  Avg Chip Temp:        {baseline.baseline_temp:.2f} "
            f"± {baseline.baseline_temp_stddev:.2f} °C"
        )
    print(
        f"[WRIGHT FAN] Baseline established for miner '{name}' fan #{baseline.fan_position}\n"
        f"  Baseline Established: Yes\n"
        f"  Sample Count:         {baseline.baseline_sample_count}\n"
        f"  Baseline Start Time:  {baseline.baseline_start_time}\n"
        f"  Baseline End Time:    {baseline.baseline_end_time}\n"
        f"  Avg RPM:              {baseline.baseline_rpm:.2f} ± {baseline.baseline_rpm_stddev:.2f}"
        f"{temp_line}"
    )


def _build_scan_summary(
    collectors: list[tuple[dict[str, Any], Any]],
    facility_id: str,
    subnets: list[str],
) -> ScanSummaryData:
    """Build a facility-level topology snapshot from the current collector list.

    Miners are grouped into their matching configured subnet (CIDR or IP
    range). Subnets with no miners are omitted from the result.
    """
    import ipaddress

    networks = []
    for cidr in subnets:
        try:
            networks.append((cidr, parse_subnet_matcher(cidr)))
        except ValueError:
            logger.warning("Invalid subnet spec in discovery config: %s", cidr)

    subnet_miners: dict[str, list[str]] = {cidr: [] for cidr, _ in networks}

    for miner_cfg, _ in collectors:
        mac = miner_cfg.get("mac_address", "")
        uid = f"{facility_id}:{mac.lower()}" if mac else miner_cfg.get("uid", "")
        if not uid:
            continue

        ip = miner_cfg.get("ip_address") or (
            miner_cfg["url"]
            .removeprefix("http://")
            .removeprefix("https://")
            .split("/")[0]
            .split(":")[0]
        )
        try:
            addr = ipaddress.ip_address(ip)
            matched = False
            for cidr, net in networks:
                if addr in net:
                    subnet_miners[cidr].append(uid)
                    matched = True
                    break
            if not matched and networks:
                subnet_miners[networks[0][0]].append(uid)
        except ValueError:
            pass

    result_subnets = [
        SubnetScanSummary(cidr=cidr, miners=miners)
        for cidr, miners in subnet_miners.items()
        if miners
    ]

    return ScanSummaryData(
        facility_id=facility_id,
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        subnets=result_subnets,
        total_miners=sum(len(s.miners) for s in result_subnets),
    )


def _poll_cycle(
    collectors: list[tuple[dict[str, Any], MinerCollector]],
    identities: dict[str, MinerIdentity],
    api_client: WrightAPIClient,
    metrics: list[str],
    facility_id: str,
    baseline_tracker: BaselineTracker,
) -> None:
    """Run one polling cycle across all miners and all consented metrics."""
    for miner_cfg, collector in collectors:
        name = miner_cfg.get("name", miner_cfg["url"])
        identity = identities.get(miner_cfg["url"])

        cooling_data_obj = None
        for metric in metrics:
            fetcher = collector.get_fetcher(metric)
            if fetcher is None:
                continue
            try:
                data_obj = fetcher()
                if metric == "cooling":
                    cooling_data_obj = data_obj
                if metric == "hashrate" and identity is not None:
                    identity.nominal_hashrate_ghs = data_obj.get_nominal_ghs()
                payload = TelemetryPayload(
                    metric_type=metric,
                    facility_id=facility_id,
                    miner_identity=identity,
                    data=asdict(data_obj),
                )
                api_client.send(payload)
            except Exception as exc:
                logger.warning(
                    "Error fetching %s from '%s': %s", metric, name, exc,
                )

        if cooling_data_obj is None:
            fan_fetcher = collector.get_fetcher("cooling")
            if fan_fetcher is not None:
                try:
                    cooling_data_obj = fan_fetcher()
                except Exception as exc:
                    logger.warning("Error fetching cooling from '%s': %s", name, exc)

        if cooling_data_obj is not None:
            try:
                new_baselines = baseline_tracker.record(identity, cooling_data_obj)
                for baseline in new_baselines:
                    _print_baseline_dashboard(name, baseline)
                    logger.info(
                        "Baseline established for miner '%s' fan #%d: "
                        "rpm=%.2f±%.2f samples=%d",
                        name, baseline.fan_position,
                        baseline.baseline_rpm, baseline.baseline_rpm_stddev,
                        baseline.baseline_sample_count,
                    )
                    api_client.send(TelemetryPayload(
                        metric_type="baseline",
                        facility_id=facility_id,
                        miner_identity=identity,
                        data=baseline.to_dict(),
                    ))
            except Exception as exc:
                logger.warning("Error updating baseline for '%s': %s", name, exc)


_FAN_DETECTION_POLL_INTERVAL = 0.25  # seconds
_DIP_RPM_MAX = 1000                # a genuine Wright Fan dip drops to (at most) this many RPM
_BASELINE_SAMPLES = 120            # rolling window size (30s at 0.25s poll) — used by run_baseline_collection only
_DETECTION_IDLE_TIMEOUT_S = 14400  # exit CLI detection mode after 4 hours with no detections
_BASELINE_COLLECTION_TIMEOUT_S = 300  # max seconds to wait for baseline collection


def run_baseline_collection(cfg: dict[str, Any]) -> None:
    """Poll fan RPM at high frequency, establish a baseline for each miner,
    and mark them as stock fans via the API.

    Intended to run once during setup, before Wright Fan detection.
    Only the mark_stock_fans() call is sent to the API — no polling data.
    """
    facility_id = cfg.get("facility_id", "unknown")
    default_collector_type = cfg.get("collector_type", "braiins")

    all_miners = _resolve_miners(cfg)
    if not all_miners:
        print("[BASELINE] No miners found. Skipping baseline collection.")
        return

    sample_time = _BASELINE_SAMPLES * _FAN_DETECTION_POLL_INTERVAL
    print(f"\n[BASELINE] Collecting fan baselines for {len(all_miners)} miner(s)...")
    print(f"  Requires ~{sample_time:.0f}s of stable readings per miner. Press Ctrl+C to skip.\n")
    for m in all_miners:
        print(f"  • {m.get('name', m['url'])} ({m['url']})")
    print()

    api_client = WrightAPIClient(
        api_url=cfg.get("wright_api_url", ""),
        api_key=cfg.get("wright_api_key", ""),
        facility_id=facility_id,
    )

    collectors: list = []
    try:
        collectors = _build_collectors(all_miners, default_collector_type)
        _authenticate_all(collectors)
        identities = _fetch_identities(collectors)
    except KeyboardInterrupt:
        print("\n[BASELINE] Skipped.")
        for _, c in collectors:
            c.close()
        return

    fan_rpm_history: dict[tuple[str, int], deque] = {}
    baselined: set[str] = set()
    start_time = time.time()

    try:
        while len(baselined) < len(collectors):
            if time.time() - start_time > _BASELINE_COLLECTION_TIMEOUT_S:
                print("[BASELINE] Timeout reached — proceeding with partial baselines.")
                break

            for miner_cfg, collector in collectors:
                url = miner_cfg["url"]
                if url in baselined:
                    continue
                name = miner_cfg.get("name", url)
                identity = identities.get(url)
                fan_fetcher = collector.get_fetcher("cooling")
                if fan_fetcher is None:
                    baselined.add(url)
                    continue

                try:
                    cooling_data = fan_fetcher()
                except Exception as exc:
                    logger.warning("Error fetching cooling from '%s': %s", name, exc)
                    continue

                if not isinstance(cooling_data, CoolingData) or not cooling_data.fans:
                    continue

                all_ready = True
                for fan in cooling_data.fans:
                    key = (url, fan.position)
                    if key not in fan_rpm_history:
                        fan_rpm_history[key] = deque(maxlen=_BASELINE_SAMPLES)
                    fan_rpm_history[key].append(fan.rpm)
                    if len(fan_rpm_history[key]) < _BASELINE_SAMPLES:
                        all_ready = False

                if all_ready:
                    mac = identity.mac_address if identity else "unknown"
                    detected_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    fan_baselines = [
                        {
                            "position": fan.position,
                            "avg_rpm": round(
                                sum(fan_rpm_history[(url, fan.position)])
                                / len(fan_rpm_history[(url, fan.position)]), 1
                            ),
                        }
                        for fan in cooling_data.fans
                    ]
                    print(f"[BASELINE] Baseline established for '{name}' — marking as stock fans")
                    logger.info("Baseline established for '%s' (mac=%s): %s", name, mac, fan_baselines)
                    baselined.add(url)

            time.sleep(_FAN_DETECTION_POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\n[BASELINE] Baseline collection skipped.")
        for _, c in collectors:
            c.close()
        return

    done = len(baselined)
    total = len(collectors)
    print(f"\n[BASELINE] Complete — {done}/{total} miner(s) baselined as stock.\n")
    for _, c in collectors:
        c.close()


_WS_FAN_SWITCH_POLL_INTERVAL = 1.0

# Per-miner cooling-fetch deadline during the 1s-cadence fan-detection loop.
# Each collector's underlying `requests` call has its own ~15s socket
# timeout, and miners are fetched sequentially — a single slow/unreachable
# miner can otherwise stall a whole tick long enough that the gateway's
# WebSocket ping/pong (15s period, 15s timeout) times out and disconnects the
# agent mid-session. Fetches run concurrently and any miner that doesn't
# respond within this deadline is skipped for that tick (it gets picked back
# up next tick); the slow request itself is left to finish in the background.
_WS_FAN_SWITCH_FETCH_TIMEOUT = 3.0


def _capture_fan_baseline(
    collectors: list[tuple[dict[str, Any], MinerCollector]],
) -> dict[tuple[str, int], int]:
    """Snapshot every fan's current RPM once, immediately.

    Called right as a detection session starts so the baseline is the
    freshest possible reading (not a rolling window, not a stale cached
    value) — it stays fixed in RAM for the whole session.
    """
    baseline: dict[tuple[str, int], int] = {}
    for miner_cfg, collector in collectors:
        url = miner_cfg["url"]
        name = miner_cfg.get("name", url)
        fan_fetcher = collector.get_fetcher("cooling")
        if fan_fetcher is None:
            continue
        try:
            cooling_data = fan_fetcher()
        except Exception as exc:
            logger.warning("Error capturing fan baseline from '%s': %s", name, exc)
            continue
        if not isinstance(cooling_data, CoolingData) or not cooling_data.fans:
            continue
        for fan in cooling_data.fans:
            baseline[(url, fan.position)] = fan.rpm
    return baseline


def _detect_fan_dips(
    miner_url: str,
    cooling_data: Any,
    baseline: dict[tuple[str, int], int],
    dipped_state: dict[tuple[str, int], bool],
) -> list[dict[str, Any]]:
    """Compare current fan RPM against the fixed session baseline.

    A dip is an isolated single fan on the miner reading at or below
    ``_DIP_RPM_MAX`` — that's what a physical switch flip looks like (the
    fan spins down to near-zero, not baseline * some percentage). If more
    than one fan on the same miner is low at the same tick, it isn't a
    valid switch-test signature (could be a power loss or unrelated
    hardware issue), so no dip event fires for anyone that tick.

    Emits a "dip" record on every tick the isolated fan reads low (dips
    only last a few seconds and we want the actual shape of the RPM trace,
    not just an edge trigger), plus a single deduped "recovered" record on
    the tick it crosses back above threshold. Recovery is evaluated
    independently per fan — it isn't gated by isolation.
    """
    if not isinstance(cooling_data, CoolingData) or not cooling_data.fans:
        return []

    readings: list[tuple[tuple[str, int], int, int]] = []  # (key, position, rpm)
    for fan in cooling_data.fans:
        key = (miner_url, fan.position)
        if key not in baseline:
            continue
        readings.append((key, fan.position, int(fan.rpm)))

    currently_low = [r for r in readings if r[2] <= _DIP_RPM_MAX]

    events: list[dict[str, Any]] = []

    if len(currently_low) == 1:
        key, position, current_rpm = currently_low[0]
        dipped_state[key] = True
        events.append({
            "fan_position": position,
            "rpm": current_rpm,
            "baseline_rpm": baseline[key],
            "direction": "dip",
        })

    for key, position, current_rpm in readings:
        if current_rpm > _DIP_RPM_MAX and dipped_state.get(key):
            dipped_state[key] = False
            events.append({
                "fan_position": position,
                "rpm": current_rpm,
                "baseline_rpm": baseline[key],
                "direction": "recovered",
            })

    return events


def _emit_fan_dip_events(
    name: str,
    miner_cfg: dict[str, Any],
    identity: Any,
    new_events: list[dict[str, Any]],
    api_client: WrightAPIClient,
    facility_id: str,
    controller: Any = None,
) -> None:
    """Send telemetry + (when running under the portal) live events for
    :func:`_detect_fan_dips` results. Always logs locally too, so the CLI
    path (no ``controller``) still gets visible output.
    """
    if not new_events:
        return

    api_client.send(
        TelemetryPayload(
            metric_type="fan_events",
            facility_id=facility_id,
            miner_identity=identity,
            data={"events": new_events},
        )
    )

    for ev in new_events:
        if ev["direction"] == "dip":
            msg = (
                f"[WRIGHT FAN] Fan dip on '{name}' fan #{ev['fan_position']}: "
                f"{ev['rpm']} RPM (baseline {ev['baseline_rpm']} RPM)"
            )
            logger.info(msg)
            print(msg)
            if controller:
                controller.push_event({
                    "event": "fan_dip",
                    "miner": name,
                    "miner_url": miner_cfg["url"],
                    "miner_uid": identity.uid if identity else None,
                    "miner_mac": identity.mac_address if identity else None,
                    "fan_position": ev["fan_position"],
                    "rpm": ev["rpm"],
                    "baseline_rpm": ev["baseline_rpm"],
                })
        else:  # "recovered"
            msg = f"[WRIGHT FAN] Wright fan detected: miner '{name}' fan #{ev['fan_position']}"
            logger.info(msg)
            print(msg)
            if controller:
                controller.push_event({
                    "event": "wright_fan_detected",
                    "miner": name,
                    "miner_url": miner_cfg["url"],
                    "miner_uid": identity.uid if identity else None,
                    "miner_mac": identity.mac_address if identity else None,
                    "fan_position": ev["fan_position"],
                })

        if controller:
            # NOTE: deprecated, needs to be removed — superseded by fan_dip / wright_fan_detected above
            controller.push_event({
                "event": "fan_transition",
                "miner": name,
                "miner_url": miner_cfg["url"],
                "fan_position": ev["fan_position"],
                "prev_rpm": ev["baseline_rpm"],
                "curr_rpm": ev["rpm"],
                "transition_type": "off" if ev["direction"] == "dip" else "on",
            })


def run_fan_detection(cfg: dict[str, Any]) -> bool:
    """Poll fan RPM on all configured miners, detecting Wright Fan dip signatures.

    Only ``cooling`` data is fetched locally — the only outbound API call is
    ``mark_wright_fans()`` when a detection fires.
    """
    facility_id = cfg.get("facility_id", "unknown")
    default_collector_type = cfg.get("collector_type", "braiins")

    all_miners = _resolve_miners(cfg)
    if not all_miners:
        logger.warning("No miners found (configured or discovered). Run --setup or enable discovery.")
        print("[WRIGHT FAN] No miners found. Press Ctrl+C to exit.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[WRIGHT FAN] Stopped.")
        return

    logger.info(
        "Starting Wright Fan detection mode: %d machine(s), polling every %ss",
        len(all_miners), _FAN_DETECTION_POLL_INTERVAL,
    )
    print(
        f"[WRIGHT FAN] Monitoring {len(all_miners)} machine(s) — "
        f"polling fan RPM every {_FAN_DETECTION_POLL_INTERVAL}s"
    )
    for m in all_miners:
        print(f"  • {m.get('name', m['url'])} ({m['url']})")

    api_client = WrightAPIClient(
        api_url=cfg.get("wright_api_url", ""),
        api_key=cfg.get("wright_api_key", ""),
        facility_id=facility_id,
    )

    stop_event = threading.Event()

    def _listen_for_quit() -> None:
        try:
            while not stop_event.is_set():
                line = sys.stdin.readline()
                if line.strip().lower() == "q":
                    print("\n[WRIGHT FAN] Stopping detection — starting normal polling loop...")
                    stop_event.set()
                    break
        except Exception:
            pass

    listener = threading.Thread(target=_listen_for_quit, daemon=True)
    listener.start()
    print("[WRIGHT FAN] Type 'q' + Enter at any time to finish detection and start normal polling.")

    consecutive_crashes = 0
    # Track last detection time for the 2-hour idle timeout (persists across crash restarts)
    last_detection_time = time.time()

    while not stop_event.is_set():
        collectors = []
        try:
            collectors = _build_collectors(all_miners, default_collector_type)
            _authenticate_all(collectors)
            identities = _fetch_identities(collectors)
            fan_baseline = _capture_fan_baseline(collectors)

            # Drop any miner with no baseline reading (auth failure,
            # unreachable, etc.) from the poll loop — retrying it every
            # _FAN_DETECTION_POLL_INTERVAL would just spam re-auth attempts
            # and never detect anything. `collectors` itself is left intact
            # so the `finally` block below still closes every connection.
            baselined_urls = {url for (url, _pos) in fan_baseline}
            skipped = [
                miner_cfg.get("name", miner_cfg["url"])
                for miner_cfg, _ in collectors
                if miner_cfg["url"] not in baselined_urls
            ]
            if skipped:
                logger.warning(
                    "Excluding %d miner(s) from fan detection — no baseline reading (auth/fetch failed): %s",
                    len(skipped), ", ".join(skipped),
                )
            active_collectors = [
                (miner_cfg, collector) for miner_cfg, collector in collectors
                if miner_cfg["url"] in baselined_urls
            ]

            consecutive_crashes = 0
            dipped_state: dict[tuple[str, int], bool] = {}

            while not stop_event.is_set():
                # Auto-exit if no detections in 4 hours
                if time.time() - last_detection_time >= _DETECTION_IDLE_TIMEOUT_S:
                    print("\n[WRIGHT FAN] No detections in 4 hours — exiting detection mode.")
                    print("  To re-enter detection mode: wright-telemetry --detect-wright-fans")
                    logger.info("Detection mode idle timeout (4 hours). Exiting.")
                    stop_event.set()
                    return True

                for miner_cfg, collector in active_collectors:
                    name = miner_cfg.get("name", miner_cfg["url"])
                    identity = identities.get(miner_cfg["url"])
                    fan_fetcher = collector.get_fetcher("cooling")
                    if fan_fetcher is None:
                        continue
                    try:
                        # Only cooling data is fetched locally — no polling data is sent to the API.
                        # The only outbound call in this loop is mark_wright_fans() on detection.
                        cooling_data_obj = fan_fetcher()
                    except Exception as exc:
                        logger.warning("Error fetching cooling from '%s': %s", name, exc)
                        continue

                    try:
                        new_events = _detect_fan_dips(
                            miner_cfg["url"], cooling_data_obj,
                            fan_baseline, dipped_state,
                        )
                        if new_events:
                            _emit_fan_dip_events(
                                name, miner_cfg, identity, new_events,
                                api_client, facility_id,
                            )
                            last_detection_time = time.time()
                    except Exception as exc:
                        logger.warning("Error in fan dip detection for '%s': %s", name, exc)

                time.sleep(_FAN_DETECTION_POLL_INTERVAL)

        except KeyboardInterrupt:
            logger.info("Wright Fan detection shutting down (keyboard interrupt)")
            print("\n[WRIGHT FAN] Stopped.")
            return False
        except Exception:
            consecutive_crashes += 1
            backoff = min(10 * (2 ** (consecutive_crashes - 1)), _MAX_BACKOFF)
            logger.exception(
                "Unexpected error in fan detection (crash #%d). Restarting in %ds...",
                consecutive_crashes, backoff,
            )
            time.sleep(backoff)
        finally:
            for _, c in collectors:
                c.close()
    return True


def _run_ws_fan_detection(
    cfg: dict[str, Any],
    controller: Any,
    api_client: WrightAPIClient,
) -> None:
    """WebSocket-triggered fan detection using the shared baseline algorithm
    (:func:`_capture_fan_baseline` + :func:`_detect_fan_dips`).

    Runs until the controller mode switches back to ``"normal"`` — either
    because the portal sent ``stop_fan_detection`` or the WebSocket
    connection dropped. There is no client-side idle timeout: the gateway
    is the source of truth for stopping an idle session (10 minutes with no
    ``fan_dip`` event), so this loop just runs until told to stop.
    """
    facility_id = cfg.get("facility_id", "unknown")
    default_collector_type = cfg.get("collector_type", "braiins")

    # Subnet scanning competes for the network with the tight 1s-cadence fan
    # polling below and can make a miner miss its per-tick deadline — suspend
    # it for the whole session and resume once detection stops (mode reverts
    # to "normal" or the miner list comes back empty), regardless of exit path.
    controller.request_discovery_pause()
    try:
        _run_ws_fan_detection_inner(cfg, controller, api_client, facility_id, default_collector_type)
    finally:
        controller.request_discovery_resume()


def _run_ws_fan_detection_inner(
    cfg: dict[str, Any],
    controller: Any,
    api_client: WrightAPIClient,
    facility_id: str,
    default_collector_type: str,
) -> None:
    miners = _resolve_miners(cfg, controller)  # pass controller for GUI store — avoid a live rescan
    if not miners:
        logger.warning("No miners found during fan detection re-discovery")
        controller.push_event({"event": "fan_detection_stopped", "reason": "no_miners"})
        controller.push_gui_event({"event": "fan_detection_stopped", "reason": "no_miners"})
        return

    collectors = _build_collectors(miners, default_collector_type)
    _authenticate_all(collectors)
    identities = _fetch_identities(collectors)

    controller.push_event({"event": "fan_detection_state", "state": "establishing_baseline"})
    controller.push_gui_event({
        "event": "fan_detection_state",
        "state": "establishing_baseline",
        "miner_count": len(collectors),
        "dip_count": 0,
    })
    fan_baseline = _capture_fan_baseline(collectors)
    dipped_state: dict[tuple[str, int], bool] = {}
    dip_count = 0

    # Drop any miner that produced no baseline reading at all (auth failure,
    # unreachable, wrong fingerprint match, etc.) — polling it every 1s for
    # the rest of the session would just spam re-auth attempts and never
    # detect anything, since _detect_fan_dips has nothing to compare against.
    baselined_urls = {url for (url, _pos) in fan_baseline}
    skipped = [
        miner_cfg.get("name", miner_cfg["url"])
        for miner_cfg, _ in collectors
        if miner_cfg["url"] not in baselined_urls
    ]
    if skipped:
        logger.warning(
            "Excluding %d miner(s) from fan detection — no baseline reading (auth/fetch failed): %s",
            len(skipped), ", ".join(skipped),
        )
    collectors = [
        (miner_cfg, collector) for miner_cfg, collector in collectors
        if miner_cfg["url"] in baselined_urls
    ]

    if not collectors:
        # Every miner failed to produce a baseline — there's nothing to poll,
        # so don't pretend detection is running (it would sit in
        # "toggle_ready" forever without ever being able to detect anything).
        logger.warning("No miners left to monitor after baseline capture — stopping fan detection")
        controller.push_event({"event": "fan_detection_stopped", "reason": "no_miners"})
        controller.push_gui_event({"event": "fan_detection_stopped", "reason": "no_miners"})
        return

    controller.push_event({
        "event": "fan_detection_started",
        "miner_count": len(collectors),
    })
    controller.push_event({"event": "fan_detection_state", "state": "toggle_ready"})
    controller.push_gui_event({
        "event": "fan_detection_state",
        "state": "toggle_ready",
        "miner_count": len(collectors),
        "dip_count": dip_count,
    })
    logger.info(
        "WebSocket fan detection started: %d miner(s), polling every %ss (baseline dip algorithm)",
        len(collectors),
        _WS_FAN_SWITCH_POLL_INTERVAL,
    )

    try:
        import psutil
        fd_baseline = psutil.Process().num_fds()
    except Exception:
        fd_baseline = 0
    fd_last_check = 0.0

    fetch_pool = ThreadPoolExecutor(
        max_workers=max(1, len(collectors)), thread_name_prefix="fan-detect-fetch",
    )
    try:
        while controller.mode == "fan_detection":
            futures = {}
            for miner_cfg, collector in collectors:
                fan_fetcher = collector.get_fetcher("cooling")
                if fan_fetcher is None:
                    continue
                futures[fetch_pool.submit(fan_fetcher)] = miner_cfg

            for future, miner_cfg in futures.items():
                name = miner_cfg.get("name", miner_cfg["url"])
                identity = identities.get(miner_cfg["url"])
                try:
                    cooling_data_obj = future.result(timeout=_WS_FAN_SWITCH_FETCH_TIMEOUT)
                except FutureTimeoutError:
                    logger.warning(
                        "Timed out fetching cooling from '%s' (>%.0fs) — skipping this tick",
                        name, _WS_FAN_SWITCH_FETCH_TIMEOUT,
                    )
                    continue
                except Exception as exc:
                    logger.warning("Error fetching cooling from '%s': %s", name, exc)
                    continue

                try:
                    new_events = _detect_fan_dips(
                        miner_cfg["url"], cooling_data_obj, fan_baseline, dipped_state,
                    )
                    if new_events:
                        _emit_fan_dip_events(
                            name, miner_cfg, identity, new_events,
                            api_client, facility_id, controller,
                        )
                        dip_count += sum(1 for ev in new_events if ev["direction"] == "recovered")
                        controller.push_gui_event({
                            "event": "fan_detection_state",
                            "state": "toggle_ready",
                            "miner_count": len(collectors),
                            "dip_count": dip_count,
                        })
                except Exception as exc:
                    logger.warning("Error checking fan RPMs for '%s': %s", name, exc)

            if fd_baseline:
                fd_baseline, fd_last_check = _check_fd_growth(fd_baseline, fd_last_check)

            # A wake signal doesn't necessarily mean "stop" — request_fan_detection()
            # bumps the same wake_seq as request_normal(), so a redundant
            # start_fan_detection while already in this mode would otherwise be
            # mistaken for a stop and tear down an in-progress session. Only
            # exit once the mode has actually changed away from fan_detection.
            if controller.wait_for_mode_change(timeout=_WS_FAN_SWITCH_POLL_INTERVAL) and controller.mode != "fan_detection":
                break
    finally:
        fetch_pool.shutdown(wait=False)

    controller.push_event({"event": "fan_detection_stopped"})
    controller.push_gui_event({"event": "fan_detection_stopped"})
    logger.info("WebSocket fan detection stopped, returning to normal mode")


def _reload_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    """Re-read config from disk, falling back to *cfg* if the file is missing."""
    fresh = load_config()
    if fresh is None:
        return cfg
    return fresh


def run(cfg: dict[str, Any], controller: Any = None) -> None:
    """Main entry point -- runs forever with crash recovery."""
    poll_interval = cfg.get("poll_interval_seconds", 30)
    facility_id = cfg.get("facility_id", "unknown")
    metrics = consented_metrics(cfg.get("consent", DEFAULT_CONSENT))  # DEFAULT_CONSENT: all ON
    default_collector_type = cfg.get("collector_type", "braiins")

    discovery_cfg = cfg.get("discovery", {})
    discovery_enabled = discovery_cfg.get("enabled", False)
    scan_interval = discovery_cfg.get("scan_interval_seconds", 300)

    api_client = WrightAPIClient(
        api_url=cfg.get("wright_api_url", ""),
        api_key=cfg.get("wright_api_key", ""),
        facility_id=facility_id,
    )

    baseline_tracker = BaselineTracker()
    consecutive_crashes = 0

    while True:
        collectors = []
        try:
            logger.info("Starting collection loop (poll every %ds, %d metric(s))", poll_interval, len(metrics))

            miners = _resolve_miners(cfg, controller)  # pass controller for GUI store
            collectors = _build_collectors(miners, default_collector_type)

            if not collectors:
                if controller is None or not getattr(controller, "has_scan_manager", False):
                    logger.error("No miners found (configured or discovered). Run --setup to add miners.")
                    time.sleep(poll_interval)
                    continue
                # GUI: miners arrive asynchronously via ScanManager — fall through to poll loop
                logger.debug("No miners yet — waiting for ScanManager discovery…")

            _authenticate_all(collectors)
            identities = _fetch_identities(collectors)

            if controller:
                controller.push_gui_event({"event": "miners_resolved", "count": len(collectors)})

            consecutive_crashes = 0
            last_scan = time.time()
            try:
                import psutil as _psutil
                _fd_baseline = _psutil.Process().num_fds()
            except Exception:
                _fd_baseline = 0
            _fd_last_check = 0.0
            known_urls = {m["url"] for m in miners}
            known_macs = {m["mac_address"] for m in miners if m.get("mac_address")}

            while True:
                now = time.time()

                # GUI always reads from the shared store (free); TUI scans on the interval.
                if (controller is not None and getattr(controller, "has_scan_manager", False)) or (
                    discovery_enabled and (now - last_scan) >= scan_interval
                ):
                    logger.info("Running periodic miner re-discovery…")
                    refreshed = _resolve_miners(cfg, controller)  # pass controller for GUI store

                    # Detect miners that moved to a new IP (MAC known, URL changed)
                    refreshed_by_mac = {
                        m["mac_address"]: m for m in refreshed if m.get("mac_address")
                    }
                    for i, (miner_cfg, _) in enumerate(collectors):
                        mac = miner_cfg.get("mac_address")
                        if not mac or mac not in refreshed_by_mac:
                            continue
                        new_cfg = refreshed_by_mac[mac]
                        if new_cfg["url"] == miner_cfg["url"]:
                            continue
                        old_url = miner_cfg["url"]
                        logger.info(
                            "Miner '%s' (%s) changed IP: %s → %s",
                            miner_cfg.get("name", mac), mac, old_url, new_cfg["url"],
                        )
                        password = decode_password(new_cfg["password_b64"]) if new_cfg.get("password_b64") else ""
                        new_collector = CollectorFactory.create(
                            name=new_cfg.get("firmware", default_collector_type),
                            url=new_cfg["url"],
                            username=new_cfg.get("username"),
                            password=password,
                        )
                        try:
                            new_collector.authenticate()
                        except Exception as exc:
                            logger.warning("Auth failed for moved miner '%s': %s", new_cfg.get("name", mac), exc)
                        # Move identity to new URL and update collector
                        old_identity = identities.pop(old_url, None)
                        if old_identity:
                            old_identity.ip_address = new_cfg["url"].removeprefix("http://").removeprefix("https://").split("/")[0].split(":")[0]
                            identities[new_cfg["url"]] = old_identity
                        collectors[i][1].close()
                        collectors[i] = (new_cfg, new_collector)
                        known_urls.discard(old_url)
                        known_urls.add(new_cfg["url"])

                    # Remove miners that are no longer in the refreshed list
                    refreshed_urls = {m["url"] for m in refreshed}
                    refreshed_macs = {m["mac_address"] for m in refreshed if m.get("mac_address")}
                    to_remove = [
                        i for i, (miner_cfg, _) in enumerate(collectors)
                        if miner_cfg["url"] not in refreshed_urls
                        and (not miner_cfg.get("mac_address") or miner_cfg["mac_address"] not in refreshed_macs)
                    ]
                    for i in reversed(to_remove):
                        miner_cfg, c = collectors[i]
                        logger.info("Miner removed from discovery: %s", miner_cfg["url"])
                        try:
                            c.close()
                        except Exception:
                            pass
                        identities.pop(miner_cfg["url"], None)
                        known_urls.discard(miner_cfg["url"])
                        known_macs.discard(miner_cfg.get("mac_address", ""))
                        collectors.pop(i)

                    # Genuinely new miners (new URL and new or absent MAC)
                    new_urls = {m["url"] for m in refreshed} - known_urls
                    new_miner_cfgs = [
                        m for m in refreshed
                        if m["url"] in new_urls
                        and (not m.get("mac_address") or m["mac_address"] not in known_macs)
                    ]

                    if new_miner_cfgs:
                        new_collectors = _build_collectors(new_miner_cfgs, default_collector_type)
                        _authenticate_all(new_collectors)
                        new_ids = _fetch_identities(new_collectors)

                        collectors.extend(new_collectors)
                        identities.update(new_ids)
                        known_urls |= {m["url"] for m in new_miner_cfgs}
                        known_macs |= {m["mac_address"] for m in new_miner_cfgs if m.get("mac_address")}
                        logger.info("Discovered %d new miner(s): %s", len(new_miner_cfgs), ", ".join(m["url"] for m in new_miner_cfgs))

                    last_scan = now

                if controller and controller.check_config_reload():
                    cfg = _reload_cfg(cfg)
                    poll_interval = cfg.get("poll_interval_seconds", 30)
                    metrics = consented_metrics(cfg.get("consent", DEFAULT_CONSENT))  # DEFAULT_CONSENT: all ON
                    default_collector_type = cfg.get("collector_type", "braiins")
                    discovery_cfg = cfg.get("discovery", {})
                    discovery_enabled = discovery_cfg.get("enabled", False)
                    scan_interval = discovery_cfg.get("scan_interval_seconds", 300)
                    logger.info("Config reloaded — active metrics: %s", ", ".join(metrics) if metrics else "(none)")
                    try:
                        safe_cfg = {k: v for k, v in cfg.items() if k != "wright_api_key"}
                        api_client.send_agent_config(safe_cfg, __version__)
                    except Exception as exc:
                        logger.warning("Failed to send agent config after reload: %s", exc)

                _poll_cycle(collectors, identities, api_client, metrics, facility_id, baseline_tracker)

                try:
                    scan_summary = _build_scan_summary(
                        collectors, facility_id, discovery_cfg.get("subnets") or []
                    )
                    api_client.send_scan_summary(scan_summary)
                except Exception as exc:
                    logger.warning("Failed to send scan summary: %s", exc)

                if controller:
                    controller.push_gui_event({"event": "poll_cycle_complete", "miner_count": len(collectors)})

                if _fd_baseline:
                    _fd_baseline, _fd_last_check = _check_fd_growth(_fd_baseline, _fd_last_check)

                if controller and controller.wait_for_mode_change(timeout=poll_interval):
                    if controller.mode == "fan_detection":
                        _run_ws_fan_detection(cfg, controller, api_client)
                else:
                    if not controller:
                        time.sleep(poll_interval)

        except KeyboardInterrupt:
            logger.info("Shutting down (keyboard interrupt)")
            api_client.close()
            break
        except Exception:
            consecutive_crashes += 1
            backoff = min(10 * (2 ** (consecutive_crashes - 1)), _MAX_BACKOFF)
            logger.exception(
                "Unexpected error (crash #%d). Restarting in %ds...",
                consecutive_crashes, backoff,
            )
            time.sleep(backoff)
        finally:
            for _, c in collectors:
                c.close()
