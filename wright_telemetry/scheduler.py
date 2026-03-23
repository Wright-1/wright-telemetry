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
from typing import Any, Optional

from wright_telemetry.api_client import WrightAPIClient
from wright_telemetry.collectors.base import MinerCollector
from wright_telemetry.collectors.factory import CollectorFactory
from wright_telemetry.config import decode_password
from wright_telemetry.consent import consented_metrics
from wright_telemetry.models import MinerIdentity, TelemetryPayload

logger = logging.getLogger(__name__)

_MAX_BACKOFF = 300  # 5 minutes


def _build_collectors(cfg: dict[str, Any]) -> list[tuple[dict[str, Any], MinerCollector]]:
    """Instantiate a collector for each configured miner."""
    collector_type = cfg.get("collector_type", "braiins")
    collectors: list[tuple[dict[str, Any], MinerCollector]] = []

    for miner_cfg in cfg.get("miners", []):
        password = ""
        if miner_cfg.get("password_b64"):
            password = decode_password(miner_cfg["password_b64"])

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
        try:
            identity = collector.fetch_identity()
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
            collectors = _build_collectors(cfg)

            if not collectors:
                logger.error("No miners configured. Run --setup to add miners.")
                time.sleep(poll_interval)
                continue

            _authenticate_all(collectors)
            identities = _fetch_identities(collectors)

            consecutive_crashes = 0  # reset on successful startup

            while True:
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
