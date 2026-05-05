"""Per-metric consent management.

Every data category is disabled by default. The setup wizard calls
``run_consent_wizard`` to walk the user through each metric, explaining
exactly what is collected and why.
"""

from __future__ import annotations

import sys
from typing import Any

import questionary
from rich.console import Console
from rich.panel import Panel

console = Console()

_WIZARD_STYLE = questionary.Style([
    ("qmark",       "fg:#22d3ee bold"),
    ("question",    "bold"),
    ("answer",      "fg:#22d3ee"),
    ("pointer",     "fg:#22d3ee bold"),
    ("highlighted", "fg:#22d3ee bold"),
    ("selected",    "fg:#4ade80"),
    ("instruction", "fg:#6b7280 italic"),
])

METRICS: dict[str, dict[str, str]] = {
    "cooling": {
        "label": "Temperature & Fan RPM",
        "endpoint": "GET /api/v1/cooling/state",
        "description": (
            "Reads the temperature sensors and fan speeds from your miner.\n"
            "Wright uses this data to predict the lifespan of your fans and\n"
            "monitor for degradation so we can alert you before a failure."
        ),
    },
    "hashrate": {
        "label": "Hashrate & Power Stats",
        "endpoint": "GET /api/v1/miner/stats",
        "description": (
            "Reads your miner's hashrate, pool stats, and power consumption.\n"
            "Wright uses this to show you how our fans are saving you money\n"
            "by keeping your miner running at peak efficiency."
        ),
    },
    "uptime": {
        "label": "Uptime & Firmware Info",
        "endpoint": "GET /api/v1/miner/details",
        "description": (
            "Reads how long your miner has been running and its firmware version.\n"
            "Wright uses this to show how our modular design is increasing\n"
            "your uptime compared to stock fans."
        ),
    },
    "hashboards": {
        "label": "Per-Hashboard Chip Temps",
        "endpoint": "GET /api/v1/miner/hw/hashboards",
        "description": (
            "Reads temperature and status for each hashboard in your miner.\n"
            "Wright uses this for granular degradation detection -- spotting\n"
            "hot-spots before they cause downtime."
        ),
    },
    "errors": {
        "label": "Miner Errors",
        "endpoint": "GET /api/v1/miner/errors",
        "description": (
            "Reads the error log from your miner (timestamps, error codes,\n"
            "affected components).  Wright uses this to notify you of fan\n"
            "failures and automatically file support reports on your behalf."
        ),
    },
    "auto_update": {
        "label": "Automatic Updates",
        "endpoint": "GitHub Releases API",
        "description": (
            "Allows Wright One to automatically download and apply new versions\n"
            "of this agent in the background. Checks run hourly and require no\n"
            "action on your part.\n"
            "\n"
            "By enabling this, you authorize Wright One to push code changes to\n"
            "your machine at any time. Wright One commits to ensuring that every\n"
            "update respects your data-sharing preferences and consent settings,\n"
            "and will never alter the metrics you have enabled or disabled here."
        ),
    },
    "remote_config": {
        "label": "Remote Configuration",
        "endpoint": "WebSocket command channel",
        "description": (
            "Allows Wright One support and your customer portal to view and\n"
            "update this agent's configuration remotely. This helps Wright\n"
            "One's team troubleshoot setup issues on your behalf and lets you\n"
            "adjust settings like poll intervals, miner lists, and discovery\n"
            "options from your dashboard without needing SSH access to this\n"
            "machine. Passwords are never transmitted -- they are always\n"
            "masked before leaving your machine.\n"
            "\n"
            "Additionally, this agent may periodically send diagnostic logs\n"
            "to Wright One's centralized logging service to help our team\n"
            "identify and resolve issues with your collector. These logs\n"
            "contain operational information (timestamps, error messages,\n"
            "connection status) and never include miner passwords or API keys."
        ),
    },
}

DEFAULT_CONSENT: dict[str, bool] = {key: False for key in METRICS}


def run_consent_wizard(existing: dict[str, bool] | None = None) -> dict[str, bool]:
    """Interactively ask the user to opt-in to each data category.

    Returns a dict mapping metric name -> True/False.
    """
    consent = dict(existing) if existing else dict(DEFAULT_CONSENT)


    console.print()
    console.print(Panel(
        "[bold]DATA SHARING PREFERENCES[/]",
        style="cyan",
        expand=False,
    ))
    console.print(
        "\nWright Telemetry collects data from your miner to power your\n"
        "dashboard.  Every category below is [bold]OFF[/] by default.  We'll\n"
        "explain exactly what each one does so you can decide.\n"
    )

    keys = list(METRICS.keys())
    i = 0
    while i < len(keys):
        key = keys[i]
        info = METRICS[key]
        current = consent.get(key, False)
        status_str = "[bold green]ON[/]" if current else "[dim]OFF[/]"
        console.print()
        console.rule(f"[bold]{info['label']}[/]  {status_str}")
        console.print(f"\n  [dim]API call:[/] [cyan]{info['endpoint']}[/]\n")
        for line in info["description"].split("\n"):
            console.print(f"  {line}")
        console.print()

        choices = ["Yes", "No"]
        if i > 0:
            choices.append("← Go back")

        result = questionary.select(
            f"Enable {info['label']}?",
            choices=choices,
            default="Yes" if current else "No",
            style=_WIZARD_STYLE,
        ).ask()

        if result is None:
            sys.exit(0)
        elif result == "← Go back":
            i -= 1
        elif result == "Yes":
            consent[key] = True
            i += 1
        else:
            consent[key] = False
            i += 1

    return consent


def consented_metrics(consent: dict[str, bool]) -> list[str]:
    """Return the list of metric names the user has opted into."""
    return [k for k, v in consent.items() if v]
