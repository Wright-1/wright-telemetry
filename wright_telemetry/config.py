"""Configuration management and interactive setup wizard.

Config is stored at ``~/.wright-telemetry/config.json``.
"""

from __future__ import annotations

import base64
import copy
import json
import os
import sys

import questionary
from questionary import Choice
from pathlib import Path
from typing import Any, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from wright_telemetry.consent import DEFAULT_CONSENT, _WIZARD_STYLE, run_consent_wizard
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

_DEFAULT_CONFIG_DIR = Path.home() / ".wright-telemetry"
_CONFIG_POINTER = _DEFAULT_CONFIG_DIR / ".config_path"

if "WRIGHT_CONFIG" in os.environ:
    CONFIG_DIR = Path(os.environ["WRIGHT_CONFIG"]).parent
    CONFIG_FILE = Path(os.environ["WRIGHT_CONFIG"])
elif _CONFIG_POINTER.exists():
    CONFIG_FILE = Path(_CONFIG_POINTER.read_text().strip())
    CONFIG_DIR = CONFIG_FILE.parent
else:
    CONFIG_DIR = _DEFAULT_CONFIG_DIR
    CONFIG_FILE = CONFIG_DIR / "config.json"


console = Console()


def set_config_location(path: Path) -> None:
    """Update the active config file path (and derived dir) at runtime, and
    persist the choice so future runs start from the same location."""
    global CONFIG_FILE, CONFIG_DIR
    CONFIG_FILE = path
    CONFIG_DIR = path.parent
    _DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _CONFIG_POINTER.write_text(str(path))


def prompt_config_location(force: bool = False) -> None:
    """Use the default config if it exists, otherwise ask where to create one.
    Pass ``force=True`` (via --set-config) to always show the selection UI."""
    console.print()

    if not force and CONFIG_FILE.exists():
        console.print(f"  Found existing config at [cyan]{CONFIG_FILE}[/]")
        console.print(f"  [dim]To choose a different config file, run: [cyan]{sys.argv[0]} --set-config[/][/]")
        return

    console.print(
        "  The [bold]config file[/] stores your Wright Fan API credentials, miner\n"
        "  settings, and discovery preferences. It is read each time the\n"
        "  collector starts."
    )
    console.print()

    if CONFIG_FILE.exists():
        choices = [
            Choice(f"Keep using current config  ({CONFIG_FILE})", value="existing"),
            Choice("Use or create at a specific path", value="custom"),
        ]
    else:
        choices = [
            Choice(f"Create at default path  ({CONFIG_FILE})", value="default"),
            Choice("Use or create at a specific path", value="custom"),
        ]

    selection = questionary.select(
        "Config file:",
        choices=choices,
        style=_WIZARD_STYLE,
    ).ask()
    if selection is None:
        sys.exit(0)

    if selection in ("existing", "default"):
        console.print(f"  Using config at: [cyan]{CONFIG_FILE}[/]")
        return

    console.print("  [dim]Enter a path to an existing config to load it, or a new path to create one there.[/]")
    while True:
        # Users sometimes paste shell-escaped paths (e.g. "My\ Folder") because
        # they copied the path from a terminal.  Python's input() receives the
        # backslashes literally, so unescape them here.
        raw = _ask("Config file path", default=str(CONFIG_FILE))
        raw = raw.strip().replace("\\ ", " ")
        chosen = Path(raw).expanduser().resolve()
        if chosen.is_dir():
            chosen = chosen / "config.json"
        elif chosen.suffix.lower() != ".json":
            chosen = chosen.with_suffix(".json")

        if not chosen.parent.exists():
            console.print(f"  [red]Directory does not exist: {chosen.parent}  — please enter a valid path.[/]")
            continue

        if chosen.exists():
            console.print(f"  Found existing config at: [cyan]{chosen}[/]")
            if _confirm("Use this config?", default=True):
                set_config_location(chosen)
                break
        else:
            console.print(f"  No config found at [cyan]{chosen}[/] — a new config will be created there.")
            if _confirm("Create config here?", default=True):
                set_config_location(chosen)
                break

SENSITIVE_MASK = "********"

_DEFAULT_WRIGHT_API_URL = "https://api.wrightfan.com/api"
_DEFAULT_POLL_INTERVAL = 30
_DEFAULT_COLLECTOR_TYPES = ["braiins"]
_DEFAULT_SCAN_INTERVAL = 30   # seconds between runtime re-scans

_KNOWN_FIRMWARE_TYPES = ["braiins", "luxos", "vnish"]


# ------------------------------------------------------------------
# Load / save
# ------------------------------------------------------------------

_REQUIRED_FIELD_LABELS: dict[str, str] = {
    "wright_api_key":        "Wright Fan API Key",
    "wright_api_url":        "Wright Fan API URL",
    "facility_id":           "Facility ID",
    "poll_interval_seconds": "Poll Interval",
    "collector_types":       "Collector OS Types",
    "consent":               "Data Sharing Preferences",
}

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


def is_config_complete(cfg: dict[str, Any]) -> tuple[bool, list[str]]:
    """Return ``(complete, missing_labels)`` for *cfg*.

    *missing_labels* contains human-readable names for any required fields
    that are absent or empty, so callers can surface them to the user.
    """
    missing: list[str] = []

    for key in ("wright_api_key", "wright_api_url", "facility_id"):
        if not str(cfg.get(key, "")).strip():
            missing.append(_REQUIRED_FIELD_LABELS[key])

    if not cfg.get("poll_interval_seconds"):
        missing.append(_REQUIRED_FIELD_LABELS["poll_interval_seconds"])

    types = cfg.get("collector_types")
    if not types or not isinstance(types, list):
        missing.append(_REQUIRED_FIELD_LABELS["collector_types"])

    if not isinstance(cfg.get("consent"), dict):
        missing.append(_REQUIRED_FIELD_LABELS["consent"])

    return len(missing) == 0, missing


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

def _require_nonempty(val: str) -> bool | str:
    return True if val.strip() else "This field is required."



def _ask(prompt: str, default: str = "", validate=None) -> str:
    answer = questionary.text(prompt, default=default, style=_WIZARD_STYLE, validate=validate).ask()
    if answer is None:
        sys.exit(0)
    return answer


def _ask_password(prompt: str) -> str:
    answer = questionary.password(prompt, style=_WIZARD_STYLE).ask()
    return answer if answer is not None else ""


def _confirm(prompt: str, default: bool = True) -> bool:
    result = questionary.confirm(prompt, default=default, style=_WIZARD_STYLE).ask()
    if result is None:
        sys.exit(0)
    return result


def _encode_password(pw: str) -> str:
    return base64.b64encode(pw.encode("utf-8")).decode("utf-8")


def decode_password(b64: str) -> str:
    return base64.b64decode(b64.encode("utf-8")).decode("utf-8")


def _print_miners_table(found: list) -> None:
    """Render a Rich table of discovered miners."""
    table = Table(box=box.SIMPLE_HEAD, show_edge=False, padding=(0, 1))
    table.add_column("#", style="dim", justify="right")
    table.add_column("IP Address", style="cyan bold")
    table.add_column("Firmware", style="green")
    table.add_column("Hostname", style="dim")
    for i, m in enumerate(found, 1):
        table.add_row(str(i), m.ip, m.firmware, m.hostname or "")
    console.print(table)


def _wizard_range_scan(collector_types: list[str] = _DEFAULT_COLLECTOR_TYPES) -> list[dict[str, Any]]:
    """Prompt for a CIDR block or IP range, scan it, return miner configs."""
    console.print()
    console.rule("[bold]Range Scan[/]")
    console.print()
    console.print("  Enter a CIDR block or IP range to scan for miners.")
    console.print("  [dim]Examples:  192.168.1.0/24  or  192.168.1.100-192.168.1.200[/]")
    target = _ask("CIDR or range (Enter to skip)")

    if not target:
        return []

    console.print()
    console.print("  [bold]Credentials for miners found in this range:[/]")
    username = _ask("Username", default="root")
    password = _ask_password("Password (hidden)")
    pw_b64 = _encode_password(password) if password else ""

    try:
        num_hosts = len(parse_ip_target(target))
    except ValueError:
        num_hosts = 0

    console.print()
    console.print(f"  Scanning [cyan]{target}[/] for miners [dim]({num_hosts} host(s))[/]…")
    console.print("  [dim]Hang tight — probing each host for your selected firmware API.[/]")
    fw = firmware_types_for_collector(collector_types)
    found = run_interactive_range_scan(target, firmware_types=fw)

    if not found:
        console.print("  [yellow]No miners found in that range.  Double-check the range or try a broader CIDR.[/]")
        return []

    console.print(f"\n  [bold green]Found {len(found)} miner(s):[/]\n")
    _print_miners_table(found)

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
        console.print(f"  Detected local networks: [cyan]{', '.join(detected_list)}[/]")
    else:
        console.print("  [yellow]Could not auto-detect your local network.[/]")

    raw_subnets = _ask(
        "Subnet(s) to scan (comma-separated CIDRs)",
        default=", ".join(disc["subnets"])
        if disc.get("subnets")
        else ", ".join(detected_list),
    )
    subnets = [s.strip() for s in raw_subnets.split(",") if s.strip()]

    if not subnets:
        console.print("  [yellow]No subnets specified — skipping discovery.[/]")
        return [], disc

    scan_interval = _DEFAULT_SCAN_INTERVAL

    console.print()
    console.print("  [bold]Default credentials[/] applied to every discovered miner.")
    console.print("  [dim]Press Enter to skip if your miners have no password set.[/]")
    default_user = _ask("Default username", default=disc.get("default_username", "root"))
    default_pw = _ask_password("Default password (hidden)")
    default_pw_b64 = _encode_password(default_pw) if default_pw else disc.get("default_password_b64", "")

    fw = firmware_types_for_collector(collector_types)

    def _run_scan(scan_subnets: list[str]) -> list[Any]:
        console.print()
        for subnet in scan_subnets:
            console.print(f"  Scanning [cyan]{subnet}[/]…")
        miners_found = run_interactive_discovery(scan_subnets, firmware_types=fw)
        if not miners_found:
            console.print("  [yellow]No miners found.[/]")
        else:
            console.print(f"\n  [bold green]Found {len(miners_found)} miner(s):[/]\n")
            _print_miners_table(miners_found)
        return miners_found

    found = _run_scan(subnets)

    # Confirmation loop — let the user load more subnets if the count looks wrong
    while True:
        if _confirm(f"Found {len(found)} miner(s). Does this look right?", default=True):
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
            console.print(f"  [red]Could not read file: {exc}[/]")

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

    console.print()
    console.print(Panel(
        "[bold]WRIGHT TELEMETRY COLLECTOR — SETUP[/]\n\n"
        "This wizard will walk you through connecting your miners\n"
        "to your Wright Fan dashboard.  You'll need:\n\n"
        "  [bold]1.[/] Your Wright Fan API key   [dim](from the customer portal)[/]\n"
        "  [bold]2.[/] Your Facility ID          [dim](from the customer portal)[/]",
        style="cyan",
        expand=False,
    ))
    console.print()
    console.rule("[bold]Wright Fan API Credentials[/]")
    console.print()

    # -- Wright Fan API credentials --
    cfg["wright_api_key"] = _ask(
        "Wright Fan API Key",
        default=cfg.get("wright_api_key", ""),
        validate=_require_nonempty,
    )
    console.print()
    console.print("  Wright Fan API URL: use the API base from the portal")
    console.print("  e.g. [cyan]https://api.wrightfan.com/api[/] or [cyan]https://api.dev.wrightfan.com/api[/]")
    console.print("  [dim]/v1/... paths are added automatically.[/]")
    cfg["wright_api_url"] = _ask(
        "Wright Fan API URL",
        default=cfg.get("wright_api_url", _DEFAULT_WRIGHT_API_URL),
        validate=_require_nonempty,
    )
    cfg["facility_id"] = _ask(
        "Facility ID",
        default=cfg.get("facility_id", ""),
        validate=_require_nonempty,
    )
    cfg["poll_interval_seconds"] = cfg.get("poll_interval_seconds", _DEFAULT_POLL_INTERVAL)
    # Backwards-compat: old configs stored a single string in collector_type
    existing_types: list[str] = (
        cfg.get("collector_types")
        or ([cfg["collector_type"]] if cfg.get("collector_type") else _DEFAULT_COLLECTOR_TYPES)
    )
    console.print()
    console.rule("[bold]Collector Type[/]")
    console.print()
    console.print("  [dim]For mixed facilities (e.g. Braiins + LuxOS) select multiple.[/]")
    selected_types = questionary.checkbox(
        "Collector OS type(s):",
        choices=[Choice(fw, checked=(fw in existing_types)) for fw in _KNOWN_FIRMWARE_TYPES],
        instruction="(space to select, enter to confirm)",
    ).ask()
    if selected_types is None:
        sys.exit(0)
    cfg["collector_types"] = selected_types if selected_types else list(_DEFAULT_COLLECTOR_TYPES)
    # Remove the old key if present to avoid confusion
    cfg.pop("collector_type", None)

    # -- Consent --
    cfg["consent"] = run_consent_wizard(cfg.get("consent"))

    # -- Auto-update --
    current_auto_update = not cfg.get("disable_auto_update", False)
    status_str = "[bold green]ON[/]" if current_auto_update else "[bold red]OFF[/]"
    console.print()
    console.rule("[bold]Automatic Updates[/]")
    console.print()
    console.print(f"  Automatic updates are currently {status_str}.")
    console.print()
    console.print("  Wright Telemetry can check for new releases every hour and")
    console.print("  apply them automatically without any action on your part.")
    console.print()
    cfg["disable_auto_update"] = not _confirm("Enable automatic updates?", default=current_auto_update)

    # -- Summary --
    from wright_telemetry.consent import METRICS
    enabled = [METRICS[k]["label"] for k, v in cfg.get("consent", {}).items() if v]
    console.print()
    console.rule()
    console.print()
    if enabled:
        console.print(f"  [bold]Enabled metrics:[/] [green]{', '.join(enabled)}[/]")
    else:
        console.print("  [yellow]No metrics enabled. The collector will run but won't send any data.[/]")
    console.print("  You can change these any time by running: [cyan bold]wright-telemetry --setup[/]")
    console.print()
    console.rule()
    console.print()

    # Save credentials, consent, and auto-update preference so the caller
    # can POST the complete config before proceeding to miner discovery.
    save_config(cfg)
    return cfg


def run_setup_wizard_miners(cfg: dict[str, Any]) -> dict[str, Any]:
    """Phase 2 of setup: miner discovery, auto-update, and final save."""

    console.print()
    console.rule("[bold]Miners[/]")
    console.print()

    miners: list[dict[str, Any]] = []

    if _confirm("Scan your local network to discover miners automatically?", default=True):
        console.print()
        discovered_miners, discovery_cfg = _wizard_discovery(
            cfg.get("discovery"),
            collector_types=cfg.get("collector_types", _DEFAULT_COLLECTOR_TYPES),
        )
        cfg["discovery"] = discovery_cfg
        miners.extend(discovered_miners)
    else:
        cfg.setdefault("discovery", {})["enabled"] = False

    if _confirm("Would you like to scan a specific subnet or IP range?", default=False):
        range_miners = _wizard_range_scan(
            collector_types=cfg.get("collector_types", _DEFAULT_COLLECTOR_TYPES),
        )
        miners.extend(range_miners)

    save_config(cfg)
    console.print(f"\n  [green]✓[/] Configuration saved to [cyan]{CONFIG_FILE}[/]")

    return cfg
