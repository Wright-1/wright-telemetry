"""CLI entry point for the Wright Telemetry Collector.

Usage:
    wright-telemetry                     Run the collector (starts setup if first time)
    wright-telemetry --setup             Re-run the setup wizard
    wright-telemetry --discover          Scan the local network for miners and exit
    wright-telemetry --install           Register as a background service (auto-start on boot)
    wright-telemetry --uninstall         Remove the background service
    wright-telemetry --version           Print version and exit
    wright-telemetry --detect-wright-fans  Poll fan RPM every second for Wright Fan machines
"""

from __future__ import annotations

import argparse
import logging
import sys

from wright_telemetry import __version__
from wright_telemetry.config import load_config, run_setup_wizard
from wright_telemetry.logging_setup import configure_logging
from wright_telemetry.service import install_service, uninstall_service
from wright_telemetry.updater import check_for_update


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="wright-telemetry",
        description="Collects miner telemetry and sends it to the Wright Fan dashboard.",
    )
    parser.add_argument("--setup", action="store_true", help="Re-run the setup wizard")
    parser.add_argument("--discover", action="store_true", help="Scan the local network for miners and exit")
    parser.add_argument("--install", action="store_true", help="Install as a background service")
    parser.add_argument("--uninstall", action="store_true", help="Remove the background service")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--loki-auth", help="Override Loki auth (Basic base64)")
    parser.add_argument(
        "--detect-wright-fans",
        action="store_true",
        help="Poll fan RPM every second on Wright Fan machines and send RPM drop events to the server",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if args.loki_auth:
        import os
        os.environ["WRIGHT_LOKI_AUTH"] = args.loki_auth

    if args.discover:
        from wright_telemetry.discovery import default_subnet, run_interactive_discovery
        subnet = default_subnet()
        if subnet is None:
            print("Could not detect local network.")
            sys.exit(1)
        print(f"\nScanning {subnet} for miners…\n")
        found = run_interactive_discovery([subnet])
        if not found:
            print("No miners found.")
        else:
            print(f"Found {len(found)} miner(s):\n")
            for m in found:
                host = f"  hostname: {m.hostname}" if m.hostname else ""
                mac = f"  mac: {m.mac_address}" if m.mac_address else ""
                print(f"  {m.ip:<16} {m.firmware:<10}{host}{mac}")
        sys.exit(0)

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

    check_for_update(cfg)

    # Import here to avoid circular imports and to ensure collector adapters register
    import wright_telemetry.collectors.braiins  # noqa: F401  -- triggers @register
    import wright_telemetry.collectors.luxos    # noqa: F401  -- triggers @register

    if args.detect_wright_fans:
        from wright_telemetry.scheduler import run_fan_detection
        run_fan_detection(cfg)
    else:
        from wright_telemetry.scheduler import run
        run(cfg)


if __name__ == "__main__":
    main()
