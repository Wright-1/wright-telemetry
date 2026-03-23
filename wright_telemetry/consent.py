"""Per-metric consent management.

Every data category is disabled by default. The setup wizard calls
``run_consent_wizard`` to walk the user through each metric, explaining
exactly what is collected and why.
"""

from __future__ import annotations

from typing import Any

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
}

DEFAULT_CONSENT: dict[str, bool] = {key: False for key in METRICS}


def run_consent_wizard(existing: dict[str, bool] | None = None) -> dict[str, bool]:
    """Interactively ask the user to opt-in to each data category.

    Returns a dict mapping metric name -> True/False.
    """
    consent = dict(existing) if existing else dict(DEFAULT_CONSENT)

    print("\n" + "=" * 60)
    print("  DATA SHARING PREFERENCES")
    print("=" * 60)
    print(
        "\nWright Telemetry collects data from your miner to power your\n"
        "dashboard.  Every category below is OFF by default.  We'll\n"
        "explain exactly what each one does so you can decide.\n"
    )

    for key, info in METRICS.items():
        current = consent.get(key, False)
        status = "ON" if current else "OFF"
        print("-" * 60)
        print(f"  {info['label']}  (currently {status})")
        print(f"  API call: {info['endpoint']}")
        print()
        print(f"  {info['description']}")
        print()

        while True:
            answer = input(f"  Enable {info['label']}? (y/n) [{('Y/n' if current else 'y/N')}]: ").strip().lower()
            if answer == "":
                break  # keep current value
            if answer in ("y", "yes"):
                consent[key] = True
                break
            if answer in ("n", "no"):
                consent[key] = False
                break
            print("  Please enter 'y' or 'n'.")

    enabled = [METRICS[k]["label"] for k, v in consent.items() if v]
    print("\n" + "=" * 60)
    if enabled:
        print("  Enabled metrics: " + ", ".join(enabled))
    else:
        print("  No metrics enabled. The collector will run but won't send any data.")
    print("  You can change these any time by running: wright-telemetry --setup")
    print("=" * 60 + "\n")

    return consent


def consented_metrics(consent: dict[str, bool]) -> list[str]:
    """Return the list of metric names the user has opted into."""
    return [k for k, v in consent.items() if v]
