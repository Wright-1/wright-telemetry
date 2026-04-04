"""Configuration management and interactive setup wizard.

Config is stored at ``~/.wright-telemetry/config.json``.
"""

from __future__ import annotations

import base64
import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

from wright_telemetry.consent import DEFAULT_CONSENT, run_consent_wizard
from wright_telemetry.discovery import (
    default_subnet,
    discovered_to_miner_cfgs,
    parse_ip_target,
    run_interactive_discovery,
    run_interactive_range_scan,
)

CONFIG_DIR = Path.home() / ".wright-telemetry"
CONFIG_FILE = CONFIG_DIR / "config.json"

_DEFAULT_WRIGHT_API_URL = "https://api.wrightfan.com/api"
_DEFAULT_POLL_INTERVAL = 30
_DEFAULT_COLLECTOR_TYPE = "braiins"
_DEFAULT_SCAN_INTERVAL = 300  # seconds between runtime re-scans


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


def mark_miner_wright_fans(miner_url: str, wright_fans: bool = True) -> None:
    """Set ``wright_fans`` on the miner matching *miner_url* and persist."""
    cfg = load_config()
    if cfg is None:
        return
    for miner in cfg.get("miners", []):
        if miner.get("url") == miner_url:
            miner["wright_fans"] = wright_fans
            break
    save_config(cfg)


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


def _wizard_add_miner(index: int) -> dict[str, Any]:
    """Walk the user through adding a single miner manually."""
    print(f"\n--- Miner #{index + 1} ---")
    name = _ask("Give this miner a friendly name (e.g. 'Rack A - Slot 3')")
    url = _ask("Miner IP or URL (e.g. http://192.168.1.100)")
    if url and not url.startswith("http"):
        url = f"http://{url}"

    print()
    print("  If your miner requires a login, enter the credentials below.")
    print("  Press Enter to skip if your miner has no password set.")
    username = _ask("Miner username", default="root")
    password = _ask_password("Miner password (hidden)")

    miner: dict[str, Any] = {
        "name": name,
        "url": url,
        "username": username,
    }
    if password:
        miner["password_b64"] = _encode_password(password)

    return miner


def _wizard_range_scan() -> list[dict[str, Any]]:
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
    print("  Hang tight — checking every IP in the range for Braiins / LuxOS APIs.")
    found = run_interactive_range_scan(target)

    if not found:
        print("  No miners found in that range.  Double-check the range or try a broader CIDR.")
        return []

    print(f"\n  Found {len(found)} miner(s):\n")
    for i, m in enumerate(found, 1):
        host_part = f"  hostname: {m.hostname}" if m.hostname else ""
        print(f"    {i}. {m.ip:<16} {m.firmware:<10}{host_part}")
    print()

    return discovered_to_miner_cfgs(found, username, pw_b64)


def _wizard_discovery(existing_discovery: Optional[dict[str, Any]] = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run the discovery portion of the setup wizard.

    Returns ``(miners, discovery_cfg)`` where *miners* is a list of miner
    config dicts and *discovery_cfg* is the ``discovery`` section to persist.
    """
    disc = dict(existing_discovery) if existing_discovery else {}

    detected = default_subnet()
    if detected:
        print(f"  Detected local network: {detected}")
    else:
        print("  Could not auto-detect your local network.")

    raw_subnets = _ask(
        "Subnet(s) to scan (comma-separated CIDRs)",
        default=disc.get("subnets", [detected] if detected else []).__str__()
        if disc.get("subnets")
        else (detected or ""),
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

    print()
    for subnet in subnets:
        print(f"  Scanning {subnet}…")

    found = run_interactive_discovery(subnets)

    if not found:
        print("  No miners found.")
    else:
        print(f"  Found {len(found)} miner(s):\n")
        for i, m in enumerate(found, 1):
            host_part = f"  hostname: {m.hostname}" if m.hostname else ""
            print(f"    {i}. {m.ip:<16} {m.firmware:<10}{host_part}")
        print()

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
    cfg["collector_type"] = _ask(
        "Collector type",
        default=cfg.get("collector_type", _DEFAULT_COLLECTOR_TYPE),
    )

    # -- Miners --
    print("\n" + "-" * 60)
    print("  MINERS")
    print("-" * 60)

    # Carry over manually-added miners from previous config
    manual_miners = [m for m in cfg.get("miners", []) if not m.get("discovered")]

    if manual_miners:
        print(f"\n  You have {len(manual_miners)} manually-added miner(s).")
        keep = _ask("Keep existing manual miners? (y/n)", default="y")
        if keep.lower() not in ("y", "yes"):
            manual_miners = []

    miners = list(manual_miners)

    # Optional network discovery
    run_discovery = _ask(
        "\n  Scan your local network to discover miners automatically? (y/n)",
        default="y",
    )
    if run_discovery.lower() in ("y", "yes"):
        print()
        discovered_miners, discovery_cfg = _wizard_discovery(cfg.get("discovery"))
        cfg["discovery"] = discovery_cfg
        miners.extend(discovered_miners)
    else:
        cfg.setdefault("discovery", {})["enabled"] = False

    # Optional manual entry — CIDR / range first, then individual
    add_manual = _ask("Would you like to add miners manually? (y/n)", default="n")
    if add_manual.lower() in ("y", "yes"):
        range_miners = _wizard_range_scan()
        miners.extend(range_miners)

        if not range_miners:
            add_individual = _ask("Add individual miners by IP? (y/n)", default="n")
            if add_individual.lower() in ("y", "yes"):
                while True:
                    miner = _wizard_add_miner(len(miners))
                    miners.append(miner)
                    more = _ask("Add another miner? (y/n)", default="n")
                    if more.lower() not in ("y", "yes"):
                        break

    cfg["miners"] = miners

    # -- Consent --
    cfg["consent"] = run_consent_wizard(cfg.get("consent"))

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

    # -- Save --
    save_config(cfg)
    print(f"\n  Configuration saved to {CONFIG_FILE}")

    return cfg
