"""Mining Rig Agent — lightweight, headless telemetry agent for direct deployment
on a mining rig.

Designed to run *on the rig itself* rather than on a separate collector machine.
This makes it ideal for testing in environments where you want to eliminate
network hops, validate subnet-detection improvements (PR #42), and exercise the
full GUI/backend foundation stack (PRs #50–#53) from a single binary.

Key differences from the standard collector
-------------------------------------------
* **Localhost-first discovery** — when no explicit ``miner_url`` is provided, the
  agent probes well-known localhost ports (4028 TCP for CGMiner/Antminer,
  4029 for LuxOS, and the Braiins GRPC 50051) before falling back to the
  new ``default_subnets()`` / ``ip addr`` multi-platform path introduced in
  PR #42.
* **Single-miner mode** — once the local miner is identified the agent skips
  the periodic re-discovery scan loop entirely; if the rig is the machine the
  agent runs on, the IP will never change.
* **Lean startup** — no interactive setup wizard is invoked; the agent is
  configured entirely from a ``config.json`` file or from environment
  variables (same resolution order as the rest of the codebase).
* **Heartbeat metric** — every poll cycle emits a lightweight ``rig_heartbeat``
  payload (uptime, firmware version, hostname, timestamp) even when all other
  metric categories are disabled, so the portal always knows the rig is alive.
* **Graceful degradation** — if the local miner API is temporarily unavailable
  (reboot, firmware update) the agent backs off exponentially and keeps
  retrying without crashing or losing auth state.
* **Self-contained entry point** — run directly::

      python -m wright_telemetry.mining_rig_agent          # uses config.json
      python -m wright_telemetry.mining_rig_agent --help

  Or import :func:`run_rig_agent` from another module.

Configuration
-------------
All settings are loaded through the standard :func:`~wright_telemetry.config.load_config`
path.  Extra keys recognised by this agent:

``mining_rig_agent.miner_url``
    Override the localhost probe and connect directly to this URL.
    Example: ``"http://192.168.1.50"``

``mining_rig_agent.firmware``
    Force a firmware type (``"braiins"``, ``"luxos"``, ``"vnish"``,
    ``"bitmain"``).  When absent the agent auto-detects via the probe.

``mining_rig_agent.poll_interval_seconds``
    Override the top-level poll interval for this agent only.
    Defaults to the top-level ``poll_interval_seconds`` (30 s).

``mining_rig_agent.heartbeat_only``
    When ``true`` only the ``rig_heartbeat`` metric is sent regardless of the
    consent settings.  Useful for a quick smoke-test on a new rig.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import asdict
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Localhost probe ports / paths
# ---------------------------------------------------------------------------

_LOCALHOST_PROBES: list[dict[str, Any]] = [
    # Braiins OS — gRPC-gateway REST on port 8080 (common default)
    {"firmware": "braiins", "url": "http://127.0.0.1:8080"},
    # LuxOS — CGMiner-compatible API on port 4029
    {"firmware": "luxos", "url": "http://127.0.0.1:4029"},
    # Vnish — REST API on port 80 (loopback)
    {"firmware": "vnish", "url": "http://127.0.0.1:80"},
    # Bitmain (Antminer) — CGMiner on port 4028
    {"firmware": "bitmain", "url": "http://127.0.0.1:4028"},
    # Braiins OS on port 80 (alternate)
    {"firmware": "braiins", "url": "http://127.0.0.1"},
]

_PROBE_TIMEOUT = 3  # seconds
_MAX_BACKOFF = 300  # 5 minutes ceiling for exponential backoff


# ---------------------------------------------------------------------------
# Localhost probe
# ---------------------------------------------------------------------------


def _probe_localhost(
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Try each known localhost endpoint and return the first that responds.

    Returns a dict with ``"url"`` and ``"firmware"`` keys, or ``None`` if no
    probe succeeded.
    """
    from wright_telemetry.collectors.factory import CollectorFactory

    for probe in _LOCALHOST_PROBES:
        fw = probe["firmware"]
        url = probe["url"]
        try:
            collector = CollectorFactory.create(
                name=fw,
                url=url,
                username=username,
                password=password or "",
            )
            collector.authenticate()
            identity = collector.fetch_identity()
            collector.close()
            logger.info(
                "Localhost probe succeeded: firmware=%s url=%s uid=%s",
                fw,
                url,
                identity.uid,
            )
            return {"firmware": fw, "url": url}
        except Exception as exc:
            logger.debug("Localhost probe failed for %s %s: %s", fw, url, exc)
            continue

    return None


def _discover_local_miner(
    cfg: dict[str, Any],
    agent_cfg: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Resolve the local miner to poll.

    Resolution order:
    1. ``mining_rig_agent.miner_url`` explicit override.
    2. Localhost probe (well-known ports).
    3. Subnet scan via PR #42's improved ``default_subnets()`` + ``scan_hosts()``.

    Returns a miner config dict suitable for
    :func:`~wright_telemetry.scheduler._build_collectors`, or ``None``.
    """
    explicit_url = agent_cfg.get("miner_url", "").strip()
    explicit_fw = agent_cfg.get("firmware", "").strip()

    # 1. Explicit URL override —————————————————————————————————————————————
    if explicit_url:
        logger.info("Using explicit miner URL: %s (firmware=%s)", explicit_url, explicit_fw or "auto")
        return {
            "url": explicit_url,
            "firmware": explicit_fw or cfg.get("collector_type", "braiins"),
            "name": "local-rig",
            "username": cfg.get("discovery", {}).get("default_username", "root"),
            "password_b64": cfg.get("discovery", {}).get("default_password_b64", ""),
        }

    username = cfg.get("discovery", {}).get("default_username", "root")
    password_b64 = cfg.get("discovery", {}).get("default_password_b64", "")
    password = ""
    if password_b64:
        from wright_telemetry.config import decode_password
        password = decode_password(password_b64)

    # 2. Localhost probe ———————————————————————————————————————————————————
    logger.info("Probing localhost for a miner API…")
    result = _probe_localhost(username=username, password=password)
    if result:
        return {
            "url": result["url"],
            "firmware": explicit_fw or result["firmware"],
            "name": "local-rig",
            "username": username,
            "password_b64": password_b64,
        }

    # 3. Subnet scan (PR #42 improvements) ————————————————————————————————
    logger.info("Localhost probe found nothing — falling back to subnet scan")
    from wright_telemetry.discovery import (
        default_subnets,
        discover_miners,
        discovered_to_miner_cfgs,
        firmware_types_for_collector,
    )

    collector_types = cfg.get("collector_types") or cfg.get("collector_type", "braiins")
    fw_types = firmware_types_for_collector(collector_types)
    subnets = default_subnets()

    if not subnets:
        logger.warning(
            "Could not determine local subnets. "
            "Set mining_rig_agent.miner_url in your config."
        )
        return None

    logger.info("Scanning %d subnet(s): %s", len(subnets), ", ".join(subnets))
    found = discover_miners(subnets=subnets, firmware_types=fw_types)
    if not found:
        logger.warning("No miners found via subnet scan.")
        return None

    # Prefer the miner whose IP is the closest to a loopback / link-local address
    # on this machine; fall back to the first result.
    miner_cfgs = discovered_to_miner_cfgs(found, username, password_b64)
    chosen = miner_cfgs[0]
    logger.info(
        "Using miner from subnet scan: url=%s firmware=%s",
        chosen["url"],
        chosen.get("firmware", "?"),
    )
    return chosen


# ---------------------------------------------------------------------------
# Heartbeat metric
# ---------------------------------------------------------------------------


def _send_heartbeat(
    api_client: Any,
    identity: Any,
    facility_id: str,
    agent_version: str,
    uptime_s: Optional[int] = None,
) -> None:
    """Send a minimal ``rig_heartbeat`` payload to the pipeline."""
    from wright_telemetry.models import TelemetryPayload

    payload = TelemetryPayload(
        metric_type="rig_heartbeat",
        facility_id=facility_id,
        miner_identity=identity,
        data={
            "agent": "mining_rig_agent",
            "agent_version": agent_version,
            "uptime_s": uptime_s,
            "firmware": identity.firmware,
            "hostname": identity.hostname,
        },
    )
    api_client.send(payload)


# ---------------------------------------------------------------------------
# Main poll loop
# ---------------------------------------------------------------------------


def run_rig_agent(cfg: dict[str, Any]) -> None:  # noqa: C901 — intentionally wide
    """Start the mining rig agent polling loop.

    This function never returns under normal operation; it blocks until the
    process receives SIGINT / SIGTERM (KeyboardInterrupt) or an unrecoverable
    error occurs.

    Args:
        cfg: A config dict as returned by
            :func:`~wright_telemetry.config.load_config`.
    """
    from wright_telemetry import __version__
    from wright_telemetry.api_client import WrightAPIClient
    from wright_telemetry.baseline import BaselineTracker
    from wright_telemetry.collectors.factory import CollectorFactory
    from wright_telemetry.config import decode_password
    from wright_telemetry.consent import consented_metrics
    from wright_telemetry.models import TelemetryPayload

    agent_cfg: dict[str, Any] = cfg.get("mining_rig_agent", {})
    poll_interval = int(
        agent_cfg.get("poll_interval_seconds")
        or cfg.get("poll_interval_seconds", 30)
    )
    facility_id = cfg.get("facility_id", "unknown")
    heartbeat_only: bool = bool(agent_cfg.get("heartbeat_only", False))
    metrics = [] if heartbeat_only else consented_metrics(cfg.get("consent", {}))

    api_client = WrightAPIClient(
        api_url=cfg.get("wright_api_url", ""),
        api_key=cfg.get("wright_api_key", ""),
        facility_id=facility_id,
    )
    baseline_tracker = BaselineTracker()
    consecutive_crashes = 0

    print(
        f"[MINING RIG AGENT] Starting — poll interval {poll_interval}s  "
        f"heartbeat_only={heartbeat_only}  "
        f"metrics={metrics or ['(none — heartbeat only)']}"
    )

    while True:
        collector = None
        try:
            # ── Discover / connect to the local miner ──────────────────────
            miner_cfg = _discover_local_miner(cfg, agent_cfg)
            if miner_cfg is None:
                logger.warning(
                    "[MINING RIG AGENT] No miner found. Retrying in %ds…",
                    poll_interval,
                )
                time.sleep(poll_interval)
                continue

            fw = miner_cfg.get("firmware", cfg.get("collector_type", "braiins"))
            url = miner_cfg["url"]
            username = miner_cfg.get("username", "root")
            password = ""
            if miner_cfg.get("password_b64"):
                password = decode_password(miner_cfg["password_b64"])

            collector = CollectorFactory.create(
                name=fw,
                url=url,
                username=username,
                password=password,
            )
            collector.authenticate()

            # Fetch identity once; it won't change while we're on the same rig.
            identity = collector.fetch_identity()
            ip = url.removeprefix("http://").removeprefix("https://").split("/")[0].split(":")[0]
            identity.ip_address = ip

            print(
                f"[MINING RIG AGENT] Connected  uid={identity.uid} "
                f"hostname={identity.hostname} mac={identity.mac_address} "
                f"firmware={identity.firmware}  url={url}"
            )
            logger.info(
                "Mining rig agent connected: uid=%s hostname=%s mac=%s firmware=%s url=%s",
                identity.uid,
                identity.hostname,
                identity.mac_address,
                identity.firmware,
                url,
            )

            # ── Inner poll loop ────────────────────────────────────────────
            consecutive_crashes = 0
            while True:
                loop_start = time.monotonic()

                # Heartbeat — always sent regardless of consent settings.
                uptime_s: Optional[int] = None
                try:
                    uptime_data = collector.fetch_uptime()
                    uptime_s = uptime_data.system_uptime_s
                except Exception:
                    pass

                try:
                    _send_heartbeat(
                        api_client,
                        identity,
                        facility_id,
                        __version__,
                        uptime_s=uptime_s,
                    )
                except Exception as exc:
                    logger.warning("[MINING RIG AGENT] Heartbeat send failed: %s", exc)

                # Consented metrics.
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

                        # Baseline tracking for cooling data.
                        if metric == "cooling":
                            try:
                                new_baselines = baseline_tracker.record(identity, data_obj)
                                for baseline in new_baselines:
                                    logger.info(
                                        "Baseline established for fan #%d: "
                                        "rpm=%.2f±%.2f samples=%d",
                                        baseline.fan_position,
                                        baseline.baseline_rpm,
                                        baseline.baseline_rpm_stddev,
                                        baseline.baseline_sample_count,
                                    )
                                    api_client.send(
                                        TelemetryPayload(
                                            metric_type="baseline",
                                            facility_id=facility_id,
                                            miner_identity=identity,
                                            data=baseline.to_dict(),
                                        )
                                    )
                            except Exception as be:
                                logger.warning(
                                    "[MINING RIG AGENT] Baseline update failed: %s", be
                                )
                    except Exception as exc:
                        logger.warning(
                            "[MINING RIG AGENT] Error fetching %s: %s", metric, exc
                        )

                # Sleep for the remainder of the poll interval.
                elapsed = time.monotonic() - loop_start
                sleep_for = max(0.0, poll_interval - elapsed)
                if sleep_for > 0:
                    time.sleep(sleep_for)

        except KeyboardInterrupt:
            print("\n[MINING RIG AGENT] Shutting down (keyboard interrupt).")
            logger.info("Mining rig agent shutting down (KeyboardInterrupt)")
            api_client.close()
            if collector:
                collector.close()
            break

        except Exception:
            consecutive_crashes += 1
            backoff = min(10 * (2 ** (consecutive_crashes - 1)), _MAX_BACKOFF)
            logger.exception(
                "[MINING RIG AGENT] Unexpected error (crash #%d). "
                "Restarting in %ds…",
                consecutive_crashes,
                backoff,
            )
            time.sleep(backoff)

        finally:
            if collector:
                try:
                    collector.close()
                except Exception:
                    pass
                collector = None


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="wright-telemetry-rig",
        description=(
            "Mining Rig Agent — deploy directly on a mining rig for local "
            "telemetry collection and forwarding to the Wright Fan dashboard."
        ),
    )
    parser.add_argument(
        "--config",
        metavar="FILE",
        default="",
        help="Path to a config.json (overrides WRIGHT_CONFIG and ~/.wright-telemetry/config.json)",
    )
    parser.add_argument(
        "--miner-url",
        metavar="URL",
        default="",
        help="Connect directly to this miner URL (e.g. http://192.168.1.50)",
    )
    parser.add_argument(
        "--firmware",
        choices=["braiins", "luxos", "vnish", "bitmain"],
        default="",
        help="Force a firmware type (default: auto-detect)",
    )
    parser.add_argument(
        "--heartbeat-only",
        action="store_true",
        help="Send only rig_heartbeat metrics (quick smoke-test)",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=0,
        metavar="SECONDS",
        help="Poll interval in seconds (overrides config)",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print version and exit",
    )
    return parser.parse_args()


def main() -> None:
    """CLI entry point for the mining rig agent."""
    args = _parse_args()

    if args.version:
        from wright_telemetry import __version__
        print(f"wright-telemetry-rig {__version__}")
        sys.exit(0)

    # Override WRIGHT_CONFIG if --config was passed.
    if args.config:
        os.environ["WRIGHT_CONFIG"] = args.config

    from wright_telemetry.config import ensure_config_file, load_config
    from wright_telemetry.logging_setup import configure_logging

    ensure_config_file()
    cfg = load_config()
    if cfg is None:
        print(
            "[MINING RIG AGENT] No config found. "
            "Run  wright-telemetry --setup  first, then retry.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Apply CLI overrides into the mining_rig_agent sub-config.
    agent_cfg: dict[str, Any] = cfg.setdefault("mining_rig_agent", {})
    if args.miner_url:
        agent_cfg["miner_url"] = args.miner_url
    if args.firmware:
        agent_cfg["firmware"] = args.firmware
    if args.heartbeat_only:
        agent_cfg["heartbeat_only"] = True
    if args.poll_interval > 0:
        agent_cfg["poll_interval_seconds"] = args.poll_interval

    configure_logging(facility_id=cfg.get("facility_id", "unknown"))

    # Register all collector adapters.
    import wright_telemetry.collectors.bitmain  # noqa: F401
    import wright_telemetry.collectors.braiins  # noqa: F401
    import wright_telemetry.collectors.luxos    # noqa: F401
    import wright_telemetry.collectors.vnish    # noqa: F401

    run_rig_agent(cfg)


if __name__ == "__main__":
    main()
