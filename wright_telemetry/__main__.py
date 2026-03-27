"""CLI entry point for the Wright Telemetry Collector.

Usage:
    wright-telemetry              Run the collector (starts setup if first time)
    wright-telemetry --setup      Re-run the setup wizard
    wright-telemetry --install    Register as a background service (auto-start on boot)
    wright-telemetry --uninstall  Remove the background service
    wright-telemetry --version    Print version and exit
"""

from __future__ import annotations

import argparse
import logging
import sys

from wright_telemetry import __version__
from wright_telemetry.config import load_config, run_setup_wizard
from wright_telemetry.logging_setup import configure_logging
from wright_telemetry.service import install_service, uninstall_service


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="wright-telemetry",
        description="Collects miner telemetry and sends it to the Wright Fan dashboard.",
    )
    parser.add_argument("--setup", action="store_true", help="Re-run the setup wizard")
    parser.add_argument("--install", action="store_true", help="Install as a background service")
    parser.add_argument("--uninstall", action="store_true", help="Remove the background service")
    parser.add_argument("--version", action="store_true", help="Print version and exit")
    parser.add_argument("--loki-auth", help="Override Loki auth (Basic base64)")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if args.version:
        print(f"wright-telemetry {__version__}")
        sys.exit(0)

    if args.loki_auth:
        import os
        os.environ["WRIGHT_LOKI_AUTH"] = args.loki_auth

    if args.uninstall:
        uninstall_service()
        sys.exit(0)

    # Load or create config
    cfg = load_config()

    if cfg is None or args.setup:
        cfg = run_setup_wizard(existing=cfg)

    if cfg is None:
        print("No configuration found. Please run: wright-telemetry --setup")
        sys.exit(1)

    # Configure logging (needs facility_id from config)
    configure_logging(facility_id=cfg.get("facility_id", "unknown"))
    logger = logging.getLogger(__name__)
    logger.info("Wright Telemetry Collector v%s starting", __version__)

    if args.install:
        install_service()
        print("\n  The service has been installed and will start automatically.")
        print("  You can also run the collector manually: wright-telemetry")
        sys.exit(0)

    # Import here to avoid circular imports and to ensure collector adapters register
    import wright_telemetry.collectors.braiins  # noqa: F401  -- triggers @register
    import wright_telemetry.collectors.luxos    # noqa: F401  -- triggers @register
    from wright_telemetry.scheduler import run

    run(cfg)


if __name__ == "__main__":
    main()
