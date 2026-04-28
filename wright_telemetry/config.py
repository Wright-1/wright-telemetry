"""Configuration management and interactive setup wizard.

Config is stored at ``~/.wright-telemetry/config.json``.
"""

from __future__ import annotations

import base64
import copy
import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

from wright_telemetry.consent import DEFAULT_CONSENT, run_consent_wizard
from wright_telemetry.discovery import (
    default_subnet,
    default_subnets,
    discovered_to_miner_cfgs,
    firmware_types_for_collector,
    load_subnets_file,
    parse_ip_target,
    run_interactive_discovery,
    run_interactive_range_scan,
)

CONFIG_DIR = Path(os.environ["WRIGHT_CONFIG"]).parent if "WRIGHT_CONFIG" in os.environ else Path.home() / ".wright-telemetry"
CONFIG_FILE = Path(os.environ["WRIGHT_CONFIG"]) if "WRIGHT_CONFIG" in os.environ else CONFIG_DIR / "config.json"


def set_config_location(path: Path) -> None:
    """Update the active config file path (and derived dir) at runtime."""
    global CONFIG_FILE, CONFIG_DIR
    CONFIG_FILE = path
    CONFIG_DIR = path.parent


def prompt_config_location() -> None:
    """Ask the user where they want the config file saved and update the
    active path.  Called once on first run before the setup wizard."""
    print()
    raw = _ask("Where would you like to save the config file?", default=str(CONFIG_FILE))
    chosen = Path(raw.strip()).expanduser().resolve()
    if chosen.suffix.lower() != ".json":
        chosen = chosen / "config.json"
    set_config_location(chosen)
    print(f"  Config will be saved to: {CONFIG_FILE}")

SENSITIVE_MASK = "********"

_DEFAULT_WRIGHT_API_URL = "https://api.wrightfan.com/api"
_DEFAULT_POLL_INTERVAL = 30
_DEFAULT_COLLECTOR_TYPES = ["braiins"]
_DEFAULT_SCAN_INTERVAL = 300  # seconds between runtime re-scans

_KNOWN_FIRMWARE_TYPES = ["braiins", "luxos", "vnish"]


# ------------------------------------------------------------------
# Load / save
# ------------------------------------------------------------------

def load_config() -> Optional[dict[str, Any]]:
    """Load config from disk. Returns None if the file doesn't exist."""
    if not CONFIG_FILE.exists():
        return None
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)


def save_config(cfg: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
    # Restrict permissions on config (best-effort -- Windows may ignore this)
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except OSError:
        pass


def mask_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of *cfg* with sensitive fields replaced."""
    masked = copy.deepcopy(cfg)
    if "wright_api_key" in masked:
        masked["wright_api_key"] = SENSITIVE_MASK
    discovery = masked.get("discovery", {})
    if "default_password_b64" in discovery:
        discovery["default_password_b64"] = SENSITIVE_MASK
    return masked


# ------------------------------------------------------------------
# Setup wizard
# ------------------------------------------------------------------

def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"  {prompt}{suffix}: ").strip()
    return answer or default


def _ask_password(prompt: str) -> str:
    """Read a password without echoing it to the terminal."""
    try:
        return getpass.getpass(f"  {prompt}: ")
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def _encode_password(pw: str) -> str:
    return base64.b64encode(pw.encode("utf-8")).decode("utf-8")


def decode_password(b64: str) -> str:
    return base64.b64decode(b64.encode("utf-8")).decode("utf-8")


def _wizard_range_scan(collector_types: list[str] = _DEFAULT_COLLECTOR_TYPES) -> list[dict[str, Any]]:
    """Prompt for a CIDR block or IP range, scan it, return miner configs."""
    print()
    print("  Enter a CIDR block or IP range to scan for miners.")
    print("  Examples:  192.168.1.0/24  or  192.168.1.100-192.168.1.200")
    target = _ask("CIDR or range (Enter to skip)")

    if not target:
        return []

    print()
    print("  Credentials for miners found in this range:")
    username = _ask("Username", default="root")
    password = _ask_password("Password (hidden)")
    pw_b64 = _encode_password(password) if password else ""

    try:
        num_hosts = len(parse_ip_target(target))
    except ValueError:
        num_hosts = 0

    print()
    print(f"  Scanning {target} for miners ({num_hosts} host(s))…")
    print("  Hang tight — probing each host for your selected firmware API.")
    fw = firmware_types_for_collector(collector_types)
    found = run_interactive_range_scan(target, firmware_types=fw)

    if not found:
        print("  No miners found in that range.  Double-check the range or try a broader CIDR.")
        return []

    print(f"\n  Found {len(found)} miner(s):\n")
    for i, m in enumerate(found, 1):
        host_part = f"  hostname: {m.hostname}" if m.hostname else ""
        print(f"    {i}. {m.ip:<16} {m.firmware:<10}{host_part}")
    print()

    return discovered_to_miner_cfgs(found, username, pw_b64)


def _wizard_discovery(
    existing_discovery: Optional[dict[str, Any]] = None,
    collector_types: list[str] = _DEFAULT_COLLECTOR_TYPES,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run the discovery portion of the setup wizard.

    Returns ``(miners, discovery_cfg)`` where *miners* is a list of miner
    config dicts and *discovery_cfg* is the ``discovery`` section to persist.
    """
    disc = dict(existing_discovery) if existing_discovery else {}

    detected_list = default_subnets()
    if detected_list:
        print(f"  Detected local networks: {', '.join(detected_list)}")
    else:
        print("  Could not auto-detect your local network.")

    raw_subnets = _ask(
        "Subnet(s) to scan (comma-separated CIDRs)",
        default=", ".join(disc["subnets"])
        if disc.get("subnets")
        else ", ".join(detected_list),
    )
    subnets = [s.strip() for s in raw_subnets.split(",") if s.strip()]

    if not subnets:
        print("  No subnets specified — skipping discovery.")
        return [], disc

    scan_interval = int(_ask(
        "Re-scan interval in seconds (0 = disable runtime re-scan)",
        default=str(disc.get("scan_interval_seconds", _DEFAULT_SCAN_INTERVAL)),
    ))

    print()
    print("  Default credentials applied to every discovered miner.")
    print("  Press Enter to skip if your miners have no password set.")
    default_user = _ask("Default username", default=disc.get("default_username", "root"))
    default_pw = _ask_password("Default password (hidden)")
    default_pw_b64 = _encode_password(default_pw) if default_pw else disc.get("default_password_b64", "")

    fw = firmware_types_for_collector(collector_types)

    def _run_scan(scan_subnets: list[str]) -> list[Any]:
        print()
        for subnet in scan_subnets:
            print(f"  Scanning {subnet}…")
        miners_found = run_interactive_discovery(scan_subnets, firmware_types=fw)
        if not miners_found:
            print("  No miners found.")
        else:
            print(f"  Found {len(miners_found)} miner(s):\n")
            for i, m in enumerate(miners_found, 1):
                host_part = f"  hostname: {m.hostname}" if m.hostname else ""
                print(f"    {i}. {m.ip:<16} {m.firmware:<10}{host_part}")
            print()
        return miners_found

    found = _run_scan(subnets)

    # Confirmation loop — let the user load more subnets if the count looks wrong
    while True:
        confirm = _ask(
            f"Found {len(found)} miner(s). Does this look right? (y/n)",
            default="y",
        )
        if confirm.lower() in ("y", "yes"):
            break
        file_path = _ask(
            "Path to subnets file to load additional VLANs (Enter to skip)"
        )
        if not file_path:
            break
        try:
            extra = load_subnets_file(file_path)
            merged = list(dict.fromkeys(subnets + extra))  # dedupe, preserve order
            subnets = merged
            found = _run_scan(subnets)
        except OSError as exc:
            print(f"  Could not read file: {exc}")

    discovery_cfg: dict[str, Any] = {
        "enabled": scan_interval > 0,
        "subnets": subnets,
        "scan_interval_seconds": scan_interval,
        "default_username": default_user,
    }
    if default_pw_b64:
        discovery_cfg["default_password_b64"] = default_pw_b64

    miners = discovered_to_miner_cfgs(found, default_user, default_pw_b64)
    return miners, discovery_cfg


def run_setup_wizard(existing: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Interactive first-time setup.  Returns a complete config dict."""
    cfg: dict[str, Any] = dict(existing) if existing else {}

    print("\n" + "=" * 60)
    print("  WRIGHT TELEMETRY COLLECTOR -- SETUP")
    print("=" * 60)
    print()
    print("  This wizard will walk you through connecting your miners to")
    print("  your Wright Fan dashboard.  You'll need:")
    print("    1. Your Wright Fan API key   (from the customer portal)")
    print("    2. Your Facility ID           (from the customer portal)")
    print()

    # -- Wright Fan API credentials --
    cfg["wright_api_key"] = _ask(
        "Wright Fan API Key",
        default=cfg.get("wright_api_key", ""),
    )
    print()
    print("  Wright Fan API URL: use the API base from the portal (e.g. https://api.wrightfan.com/api")
    print("  or https://dev.wrightfan.com/api). /v1/... paths are added automatically.")
    cfg["wright_api_url"] = _ask(
        "Wright Fan API URL",
        default=cfg.get("wright_api_url", _DEFAULT_WRIGHT_API_URL),
    )
    cfg["facility_id"] = _ask(
        "Facility ID",
        default=cfg.get("facility_id", ""),
    )
    cfg["poll_interval_seconds"] = int(
        _ask(
            "Poll interval in seconds",
            default=str(cfg.get("poll_interval_seconds", _DEFAULT_POLL_INTERVAL)),
        )
    )
    # Backwards-compat: old configs stored a single string in collector_type
    existing_types: list[str] = (
        cfg.get("collector_types")
        or ([cfg["collector_type"]] if cfg.get("collector_type") else _DEFAULT_COLLECTOR_TYPES)
    )
    print()
    print(f"  Available OS types: {', '.join(_KNOWN_FIRMWARE_TYPES)}")
    print("  For mixed facilities (e.g. Braiins + LuxOS) enter multiple, comma-separated.")
    raw_types = _ask(
        "Collector OS type(s)",
        default=", ".join(existing_types),
    )
    parsed_types = [t.strip().lower() for t in raw_types.split(",") if t.strip()]
    valid_types = [t for t in parsed_types if t in _KNOWN_FIRMWARE_TYPES]
    cfg["collector_types"] = valid_types if valid_types else list(_DEFAULT_COLLECTOR_TYPES)
    # Remove the old key if present to avoid confusion
    cfg.pop("collector_type", None)

    # -- Consent --
    cfg["consent"] = run_consent_wizard(cfg.get("consent"))

    # Save credentials + consent so the caller can POST the config and start
    # the websocket before we proceed to miner discovery.
    save_config(cfg)
    return cfg


def run_setup_wizard_miners(cfg: dict[str, Any]) -> dict[str, Any]:
    """Phase 2 of setup: miner discovery, auto-update, and final save."""

    # -- Miners --
    print("\n" + "-" * 60)
    print("  MINERS")
    print("-" * 60)

    miners: list[dict[str, Any]] = []

    run_discovery = _ask(
        "\n  Scan your local network to discover miners automatically? (y/n)",
        default="y",
    )
    if run_discovery.lower() in ("y", "yes"):
        print()
        discovered_miners, discovery_cfg = _wizard_discovery(
            cfg.get("discovery"),
            collector_types=cfg.get("collector_types", _DEFAULT_COLLECTOR_TYPES),
        )
        cfg["discovery"] = discovery_cfg
        miners.extend(discovered_miners)
    else:
        cfg.setdefault("discovery", {})["enabled"] = False

    scan_range = _ask("Would you like to scan a specific subnet or IP range? (y/n)", default="n")
    if scan_range.lower() in ("y", "yes"):
        range_miners = _wizard_range_scan(
            collector_types=cfg.get("collector_types", _DEFAULT_COLLECTOR_TYPES),
        )
        miners.extend(range_miners)

    # -- Auto-update --
    print("\n" + "-" * 60)
    print("  AUTO-UPDATE")
    print("-" * 60)
    print()
    print("  Wright Telemetry can check for new releases every hour and")
    print("  apply them automatically without any action on your part.")
    print()
    current_auto_update = not cfg.get("disable_auto_update", False)
    default_ans = "y" if current_auto_update else "n"
    ans = _ask("Enable automatic updates? (y/n)", default=default_ans)
    cfg["disable_auto_update"] = ans.lower() not in ("y", "yes")

    save_config(cfg)
    print(f"\n  Configuration saved to {CONFIG_FILE}")

    return cfg
