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
    DiscoveredMiner,
    default_subnet,
    default_subnets,
    discovered_to_miner_cfgs,
    firmware_types_for_collector,
    load_subnets_file,
    parse_ip_target,
    scan_hosts,
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


def print_config_summary(cfg: dict[str, Any], config_sent: Optional[bool] = None) -> None:
    """Print a human-readable summary of *cfg* to the console.

    *config_sent* is the return value from
    :py:meth:`~wright_telemetry.api_client.WrightAPIClient.send_agent_config`:
    ``True`` = success, ``False`` = failure, ``None`` = not attempted.
    """
    from wright_telemetry.consent import METRICS

    console.print()
    console.rule("[bold cyan]Configuration Summary[/]")

    # ── Cloud connection ──────────────────────────────────────────────────────
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", no_wrap=True)
    grid.add_column()

    api_key_raw = cfg.get("wright_api_key", "")
    masked_key = (api_key_raw[:6] + "…" + SENSITIVE_MASK) if len(api_key_raw) > 6 else SENSITIVE_MASK

    grid.add_row("Config file:",   f"[cyan]{CONFIG_FILE}[/]")
    grid.add_row("API URL:",       cfg.get("wright_api_url", "[red]not set[/]"))
    grid.add_row("Facility ID:",   cfg.get("facility_id",   "[red]not set[/]") or "[red]not set[/]")
    grid.add_row("API Key:",       masked_key)
    grid.add_row(
        "Poll interval:",
        f"{cfg.get('poll_interval_seconds', '?')}s",
    )

    collector_types = cfg.get("collector_types") or [cfg.get("collector_type", "?")]
    grid.add_row("Collector types:", ", ".join(collector_types))

    # ── Discovery ─────────────────────────────────────────────────────────────
    disc = cfg.get("discovery", {})
    subnets = disc.get("subnets") or []
    if disc.get("enabled"):
        grid.add_row("Auto-discovery:", "[green]enabled[/]")
        for subnet in subnets:
            grid.add_row("", f"[cyan]{subnet}[/]")
        if not subnets:
            grid.add_row("", "[dim]no subnets configured[/]")
    else:
        grid.add_row("Auto-discovery:", "[dim]disabled[/]")

    # ── Consent / metrics ─────────────────────────────────────────────────────
    consent = cfg.get("consent", {})
    enabled_labels: list[str] = []
    disabled_labels: list[str] = []
    for key, info in METRICS.items():
        if consent.get(key):
            enabled_labels.append(info["label"])
        else:
            disabled_labels.append(info["label"])

    if enabled_labels:
        grid.add_row(
            "Metrics enabled:",
            "[green]" + "[/], [green]".join(enabled_labels) + "[/]",
        )
    else:
        grid.add_row("Metrics enabled:", "[yellow]none[/]")

    if disabled_labels:
        grid.add_row(
            "Metrics disabled:",
            "[dim]" + ", ".join(disabled_labels) + "[/]",
        )

    console.print(grid)
    console.print()

    # ── Cloud sync result ─────────────────────────────────────────────────────
    if config_sent is True:
        console.print(
            "  [green]✓[/] Config snapshot sent to Wright One  "
            "[dim](agent-config endpoint)[/]"
        )
    elif config_sent is False:
        console.print(
            "  [yellow]⚠[/]  Could not reach the agent-config endpoint — "
            "config will sync on the next successful connection."
        )
    # None → don't print anything (remote config not enabled)

    console.print()
    console.rule()


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


def _wizard_discovery(
    existing_discovery: Optional[dict[str, Any]] = None,
    collector_types: list[str] = _DEFAULT_COLLECTOR_TYPES,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Unified miner discovery wizard.

    Shows an overview, lets the user choose how to supply subnet targets,
    collects credentials, runs a Rich progress-bar scan, shows results, and
    loops until the user accepts or skips.

    Returns ``(miners, discovery_cfg)``.
    """
    from rich.progress import (
        BarColumn, Progress, SpinnerColumn,
        TaskProgressColumn, TextColumn, TimeRemainingColumn,
    )

    disc = dict(existing_discovery) if existing_discovery else {}
    fw = firmware_types_for_collector(collector_types)
    fw_labels: list[str] = fw if fw else list(_KNOWN_FIRMWARE_TYPES)

    # ── Overview ──────────────────────────────────────────────────────────────
    console.print()
    fw_badge_str = "  ".join(f"[bold cyan][{f}][/]" for f in fw_labels)
    console.print(Panel(
        "[bold]How miner discovery works[/]\n\n"
        "Wright Data connects to each IP in your network and checks whether it "
        "responds to a known miner firmware API.  It probes for:\n\n"
        f"  {fw_badge_str}\n\n"
        "Probes run in parallel so even large subnets scan quickly.\n\n"
        "  [dim]· Nothing is written to your miners — read-only\n"
        "  · Discovered miners are saved and re-checked on every poll cycle\n"
        "  · Re-run any time with [cyan bold]wright-telemetry --setup[/dim][/]",
        title="[bold]Miner Discovery[/]",
        style="cyan",
        expand=False,
        padding=(1, 2),
    ))

    # ── State that survives across loop iterations ─────────────────────────────
    found:          list[DiscoveredMiner] = []
    subnets:        list[str]             = []
    default_user:   str                   = disc.get("default_username", "root")
    default_pw_b64: str                   = disc.get("default_password_b64", "")

    # ── Main retry loop ───────────────────────────────────────────────────────
    while True:
        console.print()
        console.rule("[bold]Step 1 — Choose how to find your miners[/]")
        console.print()

        method = questionary.select(
            "Discovery method:",
            choices=[
                Choice(
                    "Auto-detect my network    scan all detected local subnets",
                    value="auto",
                ),
                Choice(
                    "Enter subnet(s) manually  type one or more CIDRs",
                    value="manual",
                ),
                Choice(
                    "Import from file          load subnets from .xlsx or .txt",
                    value="file",
                ),
                Choice(
                    "Scan an IP range          e.g. 10.0.1.100-10.0.1.200 or CIDR",
                    value="range",
                ),
                Choice("Skip — I'll add miners later", value="skip"),
            ],
            style=_WIZARD_STYLE,
        ).ask()
        if method is None:
            sys.exit(0)
        if method == "skip":
            return [], disc

        # ── Collect targets ───────────────────────────────────────────────────
        subnets = []

        if method == "auto":
            detected = default_subnets()
            console.print()
            if detected:
                console.print(f"  Detected local networks: [cyan]{', '.join(detected)}[/]")
                subnets = detected
            else:
                console.print("  [yellow]Could not auto-detect your local network.[/]")
                console.print("  [dim]Try 'Enter subnet(s) manually' or 'Import from file'.[/]")
                continue

        elif method == "manual":
            console.print()
            raw = _ask(
                "Subnet(s) to scan  (comma-separated CIDRs)",
                default=", ".join(disc["subnets"]) if disc.get("subnets") else "",
                validate=_require_nonempty,
            )
            subnets = [s.strip() for s in raw.split(",") if s.strip()]
            if not subnets:
                console.print("  [yellow]No subnets entered.[/]")
                continue

        elif method == "file":
            console.print()
            console.print(Panel(
                "[bold]Supported file formats[/]\n\n"
                "[bold cyan]Excel  (.xlsx)[/]\n"
                "  Put one CIDR or IP range per cell — any sheet, any column.\n"
                "  Wright Data scans the entire workbook automatically.\n"
                "  [dim]Example:  A1: Subnet   A2: 10.98.1.0/24   A3: 10.98.2.0/27[/]\n\n"
                "[bold cyan]Text  (.txt)[/]\n"
                "  One CIDR or IP range per line.\n"
                "  Lines starting with [cyan]#[/] and blank lines are ignored.\n"
                "  [dim]Example:\n"
                "    # Rack row A\n"
                "    10.98.1.0/24\n"
                "    10.98.2.0/27[/]",
                style="dim",
                expand=False,
                padding=(1, 2),
            ))
            file_path = _ask("Path to .xlsx or .txt file", validate=_require_nonempty)
            # Strip whitespace and any newlines/extra spaces that terminal
            # word-wrap injects into long paths (e.g. "Wright\n One" → "Wright One")
            import re as _re
            file_path = _re.sub(r"\s+", " ", file_path).strip().replace("\\ ", " ")
            try:
                subnets = load_subnets_file(file_path)
            except ImportError:
                console.print(
                    "  [red]openpyxl is required for .xlsx files.  "
                    "Install it: pip install openpyxl[/]"
                )
                continue
            except OSError as exc:
                console.print(f"  [red]Could not read file: {exc}[/]")
                continue
            if not subnets:
                console.print("  [yellow]No valid subnets found in that file.[/]")
                continue
            console.print(f"  Loaded [cyan]{len(subnets)}[/] subnet(s) from file.")

        elif method == "range":
            console.print()
            console.print(
                "  [dim]Examples:  192.168.1.0/24  "
                " ·   192.168.1.100-192.168.1.200   ·   192.168.1.50[/]"
            )
            raw = _ask("CIDR or IP range", validate=_require_nonempty)
            subnets = [raw.strip()]

        # ── Step 2: Credentials ───────────────────────────────────────────────
        console.print()
        console.rule("[bold]Step 2 — Miner credentials[/]")
        console.print()
        console.print(
            "  These credentials are applied to [bold]every[/] discovered miner.\n"
            "  [dim]Leave blank if your miners have no password set.[/]"
        )
        console.print()
        default_user   = _ask("Username", default=disc.get("default_username", "root"))
        default_pw     = _ask_password("Password (hidden)")
        default_pw_b64 = (
            _encode_password(default_pw) if default_pw
            else disc.get("default_password_b64", "")
        )

        # ── Step 3: Expand subnets → host list ────────────────────────────────
        all_hosts: list[str] = []
        for s in subnets:
            try:
                all_hosts.extend(parse_ip_target(s))
            except ValueError as exc:
                console.print(f"  [red]Invalid target {s!r}: {exc}[/]")
        if not all_hosts:
            console.print("  [yellow]No valid hosts to scan — check your input.[/]")
            continue

        # ── Step 4: Scan with Rich progress bar ───────────────────────────────
        console.print()
        console.rule("[bold]Step 3 — Scanning[/]")
        console.print()
        fw_badge_inline = "  ".join(f"[bold cyan]{f}[/]" for f in fw_labels)
        console.print(f"  Firmware: {fw_badge_inline}")
        console.print(
            f"  [dim]{len(all_hosts):,} host(s) across "
            f"{len(subnets)} subnet(s)[/]"
        )
        console.print()

        scan_desc = "Scanning for " + ", ".join(fw_labels) + "…"
        found = []
        with Progress(
            SpinnerColumn(style="cyan"),
            TextColumn(f"[cyan]{{task.description}}"),
            BarColumn(bar_width=None, style="dim cyan", complete_style="bold cyan"),
            TaskProgressColumn(),
            TextColumn("[dim]{task.completed:,}/{task.total:,} hosts[/]"),
            TimeRemainingColumn(),
            console=console,
            transient=False,
        ) as progress:
            task_id = progress.add_task(scan_desc, total=len(all_hosts))

            def _progress_cb(scanned: int, _total: int) -> None:
                progress.update(task_id, completed=scanned)

            found = scan_hosts(all_hosts, firmware_types=fw, progress_cb=_progress_cb)
            progress.update(task_id, completed=len(all_hosts))

        # ── Step 5: Results ───────────────────────────────────────────────────
        console.print()
        console.rule("[bold]Step 4 — Results[/]")
        console.print()

        if not found:
            console.print(Panel(
                "[bold yellow]No miners found.[/]\n\n"
                "Things to check:\n"
                "  · Is this machine on the same network as your miners?\n"
                "  · Run [cyan]ping <miner-ip>[/] to confirm reachability\n"
                "  · Double-check the subnet CIDR or IP range",
                style="yellow",
                expand=False,
                padding=(1, 2),
            ))
        else:
            console.print(f"  [bold green]Found {len(found)} miner(s):[/]\n")
            _print_miners_table(found)

        console.print()
        if found:
            action_choices = [
                Choice(f"Accept — continue with {len(found)} miner(s)", value="accept"),
                Choice("Scan again with different settings", value="retry"),
            ]
        else:
            action_choices = [
                Choice("Try again with different settings", value="retry"),
                Choice("Skip — I'll add miners later", value="skip"),
            ]

        action = questionary.select(
            "What would you like to do?",
            choices=action_choices,
            style=_WIZARD_STYLE,
        ).ask()
        if action is None:
            sys.exit(0)
        if action == "accept":
            break
        if action == "skip":
            return [], disc
        # action == "retry" → loop back to Step 1

    # ── Build config ──────────────────────────────────────────────────────────
    discovery_cfg: dict[str, Any] = {
        "enabled": True,
        "subnets": subnets,
        "scan_interval_seconds": _DEFAULT_SCAN_INTERVAL,
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

    # -- Auto-update: derived from consent so updater.py needs no changes --
    cfg["disable_auto_update"] = not cfg["consent"].get("auto_update", False)

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
    """Phase 2 of setup: miner discovery and final save."""
    from wright_telemetry.discovery import merge_miners

    discovered_miners, discovery_cfg = _wizard_discovery(
        cfg.get("discovery"),
        collector_types=cfg.get("collector_types", _DEFAULT_COLLECTOR_TYPES),
    )
    cfg["discovery"] = discovery_cfg
    if discovered_miners:
        existing = cfg.get("miners", [])
        cfg["miners"] = merge_miners(
            [m for m in existing if not m.get("discovered")],
            discovered_miners,
        )

    save_config(cfg)
    console.print(f"\n  [green]\u2713[/] Configuration saved to [cyan]{CONFIG_FILE}[/]")
    return cfg
