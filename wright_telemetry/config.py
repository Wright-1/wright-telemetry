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

CONFIG_DIR = Path.home() / ".wright-telemetry"
CONFIG_FILE = CONFIG_DIR / "config.json"

_DEFAULT_WRIGHT_API_URL = "https://api.wrightfan.com"
_DEFAULT_POLL_INTERVAL = 30
_DEFAULT_COLLECTOR_TYPE = "braiins"


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
    """Walk the user through adding a single miner."""
    print(f"\n--- Miner #{index + 1} ---")
    name = _ask("Give this miner a friendly name (e.g. 'Rack A - Slot 3')")
    url = _ask("Braiins miner IP or URL (e.g. http://192.168.1.100)")
    if url and not url.startswith("http"):
        url = f"http://{url}"

    print()
    print("  If your miner requires a login, enter the credentials below.")
    print("  Press Enter to skip if your miner has no password set.")
    username = _ask("Braiins username", default="root")
    password = _ask_password("Braiins password (hidden)")

    print()
    wright_fans_ans = _ask("Is this miner using Wright fans? (y/n)", default="n")
    wright_fans = wright_fans_ans.lower() in ("y", "yes")

    miner: dict[str, Any] = {
        "name": name,
        "url": url,
        "username": username,
        "wright_fans": wright_fans,
    }
    if password:
        miner["password_b64"] = _encode_password(password)

    return miner


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
    print("    3. The IP address of each miner on your local network")
    print()

    # -- Wright Fan API credentials --
    cfg["wright_api_key"] = _ask(
        "Wright Fan API Key",
        default=cfg.get("wright_api_key", ""),
    )
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
    print("  Now let's add the miners you want to monitor.")
    print("  You can add as many as you like.\n")

    miners: list[dict[str, Any]] = []
    existing_miners: list[dict[str, Any]] = cfg.get("miners", [])

    if existing_miners:
        print(f"  You have {len(existing_miners)} miner(s) configured.")
        keep = _ask("Keep existing miners and add more? (y/n)", default="y")
        if keep.lower() in ("y", "yes"):
            miners = list(existing_miners)

    while True:
        miner = _wizard_add_miner(len(miners))
        miners.append(miner)

        more = _ask("Add another miner? (y/n)", default="n")
        if more.lower() not in ("y", "yes"):
            break

    cfg["miners"] = miners

    # -- Consent --
    cfg["consent"] = run_consent_wizard(cfg.get("consent"))

    # -- Save --
    save_config(cfg)
    print(f"\n  Configuration saved to {CONFIG_FILE}")

    return cfg
