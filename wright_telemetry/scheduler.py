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
from dataclasses import asdict
from typing import Any

from wright_telemetry.api_client import WrightAPIClient
from wright_telemetry.baseline import BaselineTracker
from wright_telemetry.collectors.base import MinerCollector
from wright_telemetry.collectors.factory import CollectorFactory
from wright_telemetry.config import decode_password
from wright_telemetry.consent import consented_metrics
from wright_telemetry.discovery import (
    discover_miners,
    discovered_to_miner_cfgs,
    merge_miners,
)
from wright_telemetry.models import MinerIdentity, TelemetryPayload

logger = logging.getLogger(__name__)

_MAX_BACKOFF = 300  # 5 minutes


def _resolve_miners(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the effective miner list (manual + freshly discovered)."""
    discovery_cfg = cfg.get("discovery", {})
    manual_miners = [m for m in cfg.get("miners", []) if not m.get("discovered")]

    if not discovery_cfg.get("enabled", False):
        return cfg.get("miners", [])

    subnets = discovery_cfg.get("subnets")
    default_user = discovery_cfg.get("default_username", "root")
    default_pw_b64 = discovery_cfg.get("default_password_b64", "")

    found = discover_miners(subnets=subnets)
    discovered_cfgs = discovered_to_miner_cfgs(found, default_user, default_pw_b64)

    merged = merge_miners(manual_miners, discovered_cfgs)
    logger.info(
        "Miner resolution: %d manual + %d discovered = %d total",
        len(manual_miners), len(discovered_cfgs), len(merged),
    )
    return merged


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
            identities[url] = identity
            # Back-propagate MAC into config so re-discovery can match by MAC
            if identity.mac_address and identity.mac_address != "unknown":
                miner_cfg["mac_address"] = identity.mac_address
            logger.info(
                "Identified miner '%s': uid=%s, serial=%s, mac=%s, ip=%s",
                name, identity.uid, identity.serial_number,
                identity.mac_address, ip,
            )
        except Exception as exc:
            logger.warning("Could not fetch identity for '%s': %s", name, exc)
            identities[url] = MinerIdentity(
                uid="unknown", serial_number="unknown",
                hostname=name, mac_address="unknown",
                ip_address=ip,
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
_DIP_THRESHOLD = 0.01               # RPM must drop >1% from rolling peak to count as a dip
_DIP_WINDOW_S = 30                 # all fans must have dipped within this window
_BASELINE_SAMPLES = 120            # rolling window size (30s at 0.25s poll)
_DETECTION_COOLDOWN_S = 300        # min seconds between detections for the same miner
_DETECTION_IDLE_TIMEOUT_S = 14400  # exit detection mode after 4 hours with no detections
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

    try:
        collectors = _build_collectors(all_miners, default_collector_type)
        _authenticate_all(collectors)
        identities = _fetch_identities(collectors)
    except KeyboardInterrupt:
        print("\n[BASELINE] Skipped.")
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

                from wright_telemetry.models import CoolingData
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
                    api_client.mark_stock_fans(mac, fan_baselines, detected_at)
                    baselined.add(url)

            time.sleep(_FAN_DETECTION_POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\n[BASELINE] Baseline collection skipped.")
        return

    done = len(baselined)
    total = len(collectors)
    print(f"\n[BASELINE] Complete — {done}/{total} miner(s) baselined as stock.\n")


def _detect_fan_dips(
    miner_url: str,
    cooling_data: Any,
    fan_rpm_history: dict[tuple[str, int], deque],
    fan_dip_times: dict[tuple[str, int], float],
    miner_last_detected: dict[str, float],
) -> list[int]:
    """Record fan RPMs and check if all fans have dipped within the window.

    A dip is defined as current RPM dropping more than _DIP_THRESHOLD below
    the rolling peak of the last _BASELINE_SAMPLES readings for that fan.
    Detection fires when every fan on the miner has a dip within the last
    _DIP_WINDOW_S seconds, subject to per-miner cooldown.

    Returns the list of fan positions that triggered the detection, or [].
    """
    from wright_telemetry.models import CoolingData
    if not isinstance(cooling_data, CoolingData) or not cooling_data.fans:
        return []

    now = time.time()

    for fan in cooling_data.fans:
        key = (miner_url, fan.position)
        if key not in fan_rpm_history:
            fan_rpm_history[key] = deque(maxlen=_BASELINE_SAMPLES)
        fan_rpm_history[key].append(fan.rpm)

        history = fan_rpm_history[key]
        if len(history) < _BASELINE_SAMPLES:
            continue  # not enough data yet to establish a baseline

        peak = max(history)
        if peak == 0:
            continue

        if fan.rpm < peak * (1 - _DIP_THRESHOLD):
            if fan_dip_times.get(key, 0.0) < now - 1:
                print(
                    f"[WRIGHT FAN] Fan dip detected on {miner_url} "
                    f"fan #{fan.position}: {fan.rpm} RPM (peak {peak} RPM)"
                )
            fan_dip_times[key] = now

    last_detected = miner_last_detected.get(miner_url, 0.0)
    if now - last_detected < _DETECTION_COOLDOWN_S:
        return []

    all_positions = [fan.position for fan in cooling_data.fans]
    cutoff = now - _DIP_WINDOW_S
    dipped = [
        pos for pos in all_positions
        if fan_dip_times.get((miner_url, pos), 0.0) >= cutoff
    ]

    if dipped and len(dipped) == len(all_positions):
        miner_last_detected[miner_url] = now
        return dipped

    return []


def run_fan_detection(cfg: dict[str, Any]) -> None:
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
        try:
            collectors = _build_collectors(all_miners, default_collector_type)
            _authenticate_all(collectors)
            identities = _fetch_identities(collectors)

            consecutive_crashes = 0
            fan_rpm_history: dict[tuple[str, int], deque] = {}
            fan_dip_times: dict[tuple[str, int], float] = {}
            miner_last_detected: dict[str, float] = {}

            while not stop_event.is_set():
                # Auto-exit if no detections in 4 hours
                if time.time() - last_detection_time >= _DETECTION_IDLE_TIMEOUT_S:
                    print("\n[WRIGHT FAN] No detections in 4 hours — exiting detection mode.")
                    print("  To re-enter detection mode: wright-telemetry --detect-wright-fans")
                    logger.info("Detection mode idle timeout (4 hours). Exiting.")
                    stop_event.set()
                    return

                for miner_cfg, collector in collectors:
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
                        dipped = _detect_fan_dips(
                            miner_cfg["url"], cooling_data_obj,
                            fan_rpm_history, fan_dip_times, miner_last_detected,
                        )
                        if dipped:
                            mac = identity.mac_address if identity else "unknown"
                            detected_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                            print(
                                f"[WRIGHT FAN] All fans dipped on '{name}' "
                                f"(positions {dipped}) — marking as Wright fans"
                            )
                            logger.info(
                                "All fans dipped on '%s' (mac=%s positions=%s) — calling wright-fans endpoint",
                                name, mac, dipped,
                            )
                            api_client.mark_wright_fans(mac, dipped, detected_at)
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
    return True


def run(cfg: dict[str, Any]) -> None:
    """Main entry point -- runs forever with crash recovery."""
    poll_interval = cfg.get("poll_interval_seconds", 30)
    facility_id = cfg.get("facility_id", "unknown")
    metrics = consented_metrics(cfg.get("consent", {}))
    default_collector_type = cfg.get("collector_type", "braiins")

    discovery_cfg = cfg.get("discovery", {})
    discovery_enabled = discovery_cfg.get("enabled", False)
    scan_interval = discovery_cfg.get("scan_interval_seconds", 300)

    if not metrics:
        logger.warning("No metrics are enabled. The collector will idle. Run --setup to enable metrics.")

    api_client = WrightAPIClient(
        api_url=cfg.get("wright_api_url", ""),
        api_key=cfg.get("wright_api_key", ""),
        facility_id=facility_id,
    )

    baseline_tracker = BaselineTracker()
    consecutive_crashes = 0

    while True:
        try:
            logger.info("Starting collection loop (poll every %ds, %d metric(s))", poll_interval, len(metrics))

            miners = _resolve_miners(cfg)
            collectors = _build_collectors(miners, default_collector_type)

            if not collectors:
                logger.error("No miners found (configured or discovered). Run --setup to add miners.")
                time.sleep(poll_interval)
                continue

            _authenticate_all(collectors)
            identities = _fetch_identities(collectors)

            consecutive_crashes = 0
            last_scan = time.time()
            known_urls = {m["url"] for m in miners}
            # Include MACs back-propagated from identity fetch
            known_macs = {m["mac_address"] for m in miners if m.get("mac_address")}

            while True:
                now = time.time()

                if discovery_enabled and (now - last_scan) >= scan_interval:
                    logger.info("Running periodic miner re-discovery…")
                    refreshed = _resolve_miners(cfg)

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
                        collectors[i] = (new_cfg, new_collector)
                        known_urls.discard(old_url)
                        known_urls.add(new_cfg["url"])

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

                _poll_cycle(collectors, identities, api_client, metrics, facility_id, baseline_tracker)

                time.sleep(poll_interval)

        except KeyboardInterrupt:
            logger.info("Shutting down (keyboard interrupt)")
            break
        except Exception:
            consecutive_crashes += 1
            backoff = min(10 * (2 ** (consecutive_crashes - 1)), _MAX_BACKOFF)
            logger.exception(
                "Unexpected error (crash #%d). Restarting in %ds...",
                consecutive_crashes, backoff,
            )
            time.sleep(backoff)
