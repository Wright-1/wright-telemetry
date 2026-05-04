"""CLI entry point for the Wright Telemetry Collector.

Usage:
    wright-telemetry                       Run the collector (starts setup if first time)
    wright-telemetry --setup               Re-run the setup wizard
    wright-telemetry --detect-wright-fans  Start Wright Fan detection mode
    wright-telemetry --discover            Scan all local subnets for miners and exit
    wright-telemetry --subnets-file FILE   Import VLANs from file, scan, and save to config
    wright-telemetry --install             Register as a background service (auto-start on boot)
    wright-telemetry --uninstall           Remove the background service
    wright-telemetry --version             Print version and exit
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from wright_telemetry import __version__

console = Console()
from wright_telemetry.api_client import WrightAPIClient
from wright_telemetry.config import CONFIG_DIR, is_config_complete, load_config, prompt_config_location, run_setup_wizard, run_setup_wizard_miners
from wright_telemetry.logging_setup import configure_logging
from wright_telemetry.service import install_service, uninstall_service
from wright_telemetry.updater import check_for_update


def _print_help_menu() -> None:
    """Print a formatted list of available commands."""
    table = Table(box=None, show_header=False, padding=(0, 2), expand=False)
    table.add_column("Command", style="cyan bold", no_wrap=True)
    table.add_column("Description")
    table.add_row("wright-telemetry", "Start the collector")
    table.add_row("wright-telemetry --setup", "Re-run the setup wizard")
    table.add_row("wright-telemetry --detect-wright-fans", "Start Wright Fan detection mode")
    table.add_row("wright-telemetry --discover", "Scan all local subnets for miners")
    table.add_row("wright-telemetry --subnets-file FILE", "Import VLANs from file and scan")
    table.add_row("wright-telemetry --install", "Install as a background service")
    table.add_row("wright-telemetry --uninstall", "Remove the background service")
    table.add_row("wright-telemetry --version", "Print version and exit")
    console.print()
    console.print(Panel(table, title="[bold]Wright Telemetry — Available Commands[/]", style="cyan"))
    console.print()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="wright-telemetry",
        description="Collects miner telemetry and sends it to the Wright Fan dashboard.",
    )
    parser.add_argument("--setup", action="store_true", help="Re-run the setup wizard")
    parser.add_argument("--set-config", action="store_true", help="Choose or create the config file interactively")
    parser.add_argument("--discover", action="store_true", help="Scan all local subnets for miners and exit")
    parser.add_argument("--subnets-file", metavar="FILE", help="Import VLANs from a text file (one CIDR per line), save to config, and scan")
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
    # Ensure the data/log directory exists early so that launchd (macOS) can
    # open its stdout/stderr log paths on the next reboot.  Running any
    # subcommand with an updated binary is enough to heal existing installs.
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    args = _parse_args()

    if args.loki_auth:
        os.environ["WRIGHT_LOKI_AUTH"] = args.loki_auth

    if args.discover:
        from wright_telemetry.discovery import default_subnets, run_interactive_discovery
        subnets = default_subnets()
        if not subnets:
            print("Could not detect local network.")
            sys.exit(1)
        print(f"\nScanning {len(subnets)} subnet(s): {', '.join(subnets)}\n")
        found = run_interactive_discovery(subnets)
        if not found:
            print("No miners found.")
        else:
            print(f"Found {len(found)} miner(s):\n")
            for m in found:
                host = f"  hostname: {m.hostname}" if m.hostname else ""
                mac = f"  mac: {m.mac_address}" if m.mac_address else ""
                print(f"  {m.ip:<16} {m.firmware:<10}{host}{mac}")
        sys.exit(0)

    if args.subnets_file:
        from wright_telemetry.discovery import (
            discovered_to_miner_cfgs,
            firmware_types_for_collector,
            load_subnets_file,
            merge_miners,
            run_interactive_discovery,
        )
        from wright_telemetry.config import save_config
        try:
            subnets = load_subnets_file(args.subnets_file)
        except OSError as exc:
            print(f"Could not read subnets file: {exc}")
            sys.exit(1)
        if not subnets:
            print("Subnets file is empty or contains only comments.")
            sys.exit(1)
        cfg = load_config() or {}
        disc = cfg.setdefault("discovery", {})
        disc["subnets"] = subnets
        disc.setdefault("enabled", True)
        save_config(cfg)
        print(f"Imported {len(subnets)} subnet(s) from {args.subnets_file}")
        fw = firmware_types_for_collector(cfg.get("collector_type", "braiins"))
        print(f"\nScanning {len(subnets)} subnet(s) for miners…\n")
        found = run_interactive_discovery(subnets, firmware_types=fw)
        if not found:
            print("No miners found.")
        else:
            print(f"Found {len(found)} miner(s):\n")
            for m in found:
                host = f"  hostname: {m.hostname}" if m.hostname else ""
                mac = f"  mac: {m.mac_address}" if m.mac_address else ""
                print(f"  {m.ip:<16} {m.firmware:<10}{host}{mac}")

            # Persist discovered miners so baseline can reach them
            disc_cfg = cfg.get("discovery", {})
            default_user = disc_cfg.get("default_username", "root")
            default_pw_b64 = disc_cfg.get("default_password_b64", "")
            manual = [m for m in cfg.get("miners", []) if not m.get("discovered")]
            discovered_cfgs = discovered_to_miner_cfgs(found, default_user, default_pw_b64)
            cfg["miners"] = merge_miners(manual, discovered_cfgs)
            save_config(cfg)

        configure_logging(facility_id=cfg.get("facility_id", "unknown"))
        from wright_telemetry.scheduler import run_baseline_collection
        run_baseline_collection(cfg)
        # Fall through to the normal polling loop below

    if args.uninstall:
        uninstall_service()
        sys.exit(0)

    # Ask where the config file lives before we try to load it.
    # Skipped when WRIGHT_CONFIG env var is already set (service installs,
    # CI) or when stdin is not a TTY (running non-interactively).
    if sys.stdin.isatty() and (args.set_config or ("WRIGHT_CONFIG" not in os.environ)):
        prompt_config_location(force=args.set_config)

    # Load or create config
    cfg = load_config()

    # Backfill remote_config consent for users who set up before it existed.
    # They were never asked, so default to True so support can help them.
    if cfg and "consent" in cfg and "remote_config" not in cfg["consent"]:
        from wright_telemetry.config import save_config as _save
        cfg["consent"]["remote_config"] = True
        _save(cfg)

    # If an existing config is missing required fields, notify and re-run setup.
    if cfg is not None and not args.setup:
        complete, missing = is_config_complete(cfg)
        if not complete:
            console.print()
            console.print(Panel(
                "[bold]Incomplete configuration[/] — the following required fields are missing or empty:\n\n"
                + "\n".join(f"  • [cyan]{f}[/]" for f in missing)
                + "\n\n[dim]The setup wizard will run to fill them in.[/]",
                style="yellow",
                expand=False,
            ))
            args.setup = True

    ran_setup = cfg is None or args.setup

    from wright_telemetry.ws_client import AgentController, WebSocketClient
    controller = AgentController()
    ws_client = None

    if ran_setup:
        cfg = run_setup_wizard(existing=cfg)

        if cfg is None:
            print("No configuration found. Please run: wright-telemetry --setup")
            sys.exit(1)

        if cfg.get("consent", {}).get("remote_config"):
            _client = WrightAPIClient(
                api_url=cfg.get("wright_api_url", ""),
                api_key=cfg.get("wright_api_key", ""),
                facility_id=cfg.get("facility_id", ""),
            )
            safe_cfg = {k: v for k, v in cfg.items() if k not in ("wright_api_key",)}
            _client.send_agent_config(safe_cfg, __version__)
            _client.close()

        # Start websocket before discovery so the portal sees the agent online.
        ws_client = WebSocketClient(
            controller,
            api_url=cfg.get("wright_api_url", ""),
            api_key=cfg.get("wright_api_key", ""),
            facility_id=cfg.get("facility_id", ""),
        )
        ws_client.start()

        cfg = run_setup_wizard_miners(cfg)

        if cfg.get("consent", {}).get("remote_config"):
            _client = WrightAPIClient(
                api_url=cfg.get("wright_api_url", ""),
                api_key=cfg.get("wright_api_key", ""),
                facility_id=cfg.get("facility_id", ""),
            )
            safe_cfg = {k: v for k, v in cfg.items() if k not in ("wright_api_key",)}
            _client.send_agent_config(safe_cfg, __version__)
            _client.close()

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
    import wright_telemetry.collectors.vnish    # noqa: F401  -- triggers @register

    if ws_client is None:
        ws_client = WebSocketClient(
            controller,
            api_url=cfg.get("wright_api_url", ""),
            api_key=cfg.get("wright_api_key", ""),
            facility_id=cfg.get("facility_id", ""),
        )
        ws_client.start()

    # After setup: collect baselines and show help (fan detection: use --detect-wright-fans)
    if ran_setup and not args.detect_wright_fans:
        from wright_telemetry.scheduler import run_baseline_collection

        run_baseline_collection(cfg)
        _print_help_menu()
        console.print("  Wright Fan detection mode monitors fan RPM for dips that indicate Wright")
        console.print("  fans are installed. You can start it anytime with:")
        console.print("  [cyan bold]wright-telemetry --detect-wright-fans[/]\n")

    if args.detect_wright_fans:
        from wright_telemetry.scheduler import run_fan_detection

        completed = run_fan_detection(cfg)
        if not completed:
            return
        cfg["fan_detection_completed"] = True
        from wright_telemetry.config import save_config

        save_config(cfg)

    if cfg.get("consent", {}).get("remote_config"):
        _client = WrightAPIClient(
            api_url=cfg.get("wright_api_url", ""),
            api_key=cfg.get("wright_api_key", ""),
            facility_id=cfg.get("facility_id", ""),
        )
        safe_cfg = {k: v for k, v in cfg.items() if k not in ("wright_api_key",)}
        _client.send_agent_config(safe_cfg, __version__)
        _client.close()

    from wright_telemetry.scheduler import run
    run(cfg, controller=controller)


if __name__ == "__main__":
    main()
