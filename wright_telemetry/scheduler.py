"""Polling loop with two-layer fault tolerance.

Inner layer: per-miner / per-metric try/except -- a single failure never
             kills the loop.
Outer layer: top-level crash recovery with exponential backoff -- if
             something truly unexpected happens the loop restarts from
             scratch (re-auth, re-fetch identities, resume polling).
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict
from typing import Any

from wright_telemetry.api_client import WrightAPIClient
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
    """Fetch and cache miner identities keyed by miner URL."""
    identities: dict[str, MinerIdentity] = {}
    for miner_cfg, collector in collectors:
        name = miner_cfg.get("name", miner_cfg["url"])
        wright_fans = bool(miner_cfg.get("wright_fans", False))
        try:
            identity = collector.fetch_identity()
            identity.wright_fans = wright_fans
            identities[miner_cfg["url"]] = identity
            logger.info(
                "Identified miner '%s': uid=%s, serial=%s",
                name, identity.uid, identity.serial_number,
            )
        except Exception as exc:
            logger.warning("Could not fetch identity for '%s': %s", name, exc)
            identities[miner_cfg["url"]] = MinerIdentity(
                uid="unknown", serial_number="unknown",
                hostname=name, mac_address="unknown",
                wright_fans=wright_fans,
            )
    return identities


def _poll_cycle(
    collectors: list[tuple[dict[str, Any], MinerCollector]],
    identities: dict[str, MinerIdentity],
    api_client: WrightAPIClient,
    metrics: list[str],
    facility_id: str,
) -> None:
    """Run one polling cycle across all miners and all consented metrics."""
    for miner_cfg, collector in collectors:
        name = miner_cfg.get("name", miner_cfg["url"])
        identity = identities.get(miner_cfg["url"])

        for metric in metrics:
            fetcher = collector.get_fetcher(metric)
            if fetcher is None:
                continue
            try:
                data_obj = fetcher()
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

            while True:
                if discovery_enabled and (time.time() - last_scan) >= scan_interval:
                    logger.info("Running periodic miner re-discovery…")
                    refreshed = _resolve_miners(cfg)
                    new_urls = {m["url"] for m in refreshed} - known_urls

                    if new_urls:
                        new_miner_cfgs = [m for m in refreshed if m["url"] in new_urls]
                        new_collectors = _build_collectors(new_miner_cfgs, default_collector_type)
                        _authenticate_all(new_collectors)
                        new_ids = _fetch_identities(new_collectors)

                        collectors.extend(new_collectors)
                        identities.update(new_ids)
                        known_urls |= new_urls
                        logger.info("Discovered %d new miner(s): %s", len(new_urls), ", ".join(sorted(new_urls)))

                    last_scan = time.time()

                _poll_cycle(collectors, identities, api_client, metrics, facility_id)
                time.sleep(poll_interval)

        except KeyboardInterrupt:
            logger.info("Shutting down (keyboard interrupt)")
            break
        except Exception as exc:
            consecutive_crashes += 1
            backoff = min(10 * (2 ** (consecutive_crashes - 1)), _MAX_BACKOFF)
            logger.exception(
                "Unexpected error (crash #%d). Restarting in %ds...",
                consecutive_crashes, backoff,
            )
            time.sleep(backoff)
