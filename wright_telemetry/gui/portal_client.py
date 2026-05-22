"""Lightweight HTTP client for agent-facing portal endpoints.

All network calls are made on a daemon thread; results are pushed into
the GUI event queue so the Qt main thread never blocks.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

import requests

logger = logging.getLogger(__name__)

_TIMEOUT = 10  # seconds


def redeem_access_key(
    api_url: str,
    access_key: str,
    callback: Any,
) -> None:
    """Exchange an access key for an api_key + facility_id.

    Calls POST /api/v2/provision/redeem and invokes *callback* with a dict:
      {"success": True, "apiKey": "...", "facilityId": "..."}
      {"success": False, "error": "..."}

    Runs on a daemon thread so the Qt main thread never blocks.
    """

    def _run() -> None:
        base = api_url.rstrip("/")
        url = f"{base}/api/provision/redeem"
        print(f"[WRIGHT] POST {url}")
        try:
            r = requests.post(
                url,
                json={"accessKey": access_key},
                timeout=_TIMEOUT,
            )
            payload = r.json()
            print(f"[WRIGHT] POST {url} → {r.status_code}")
            if r.status_code == 200 and payload.get("success"):
                data = payload.get("data", {})
                callback({"success": True, "apiKey": data["apiKey"], "facilityId": data["facilityId"]})
            else:
                err = payload.get("error") or payload.get("message") or f"HTTP {r.status_code}"
                logger.warning("access-key redeem failed: %s", err)
                callback({"success": False, "error": err})
        except Exception as exc:
            print(f"[WRIGHT] POST {url} → ERROR: {exc}")
            logger.warning("access-key redeem exception: %s", exc)
            callback({"success": False, "error": str(exc)})

    t = threading.Thread(target=_run, daemon=True, name="wright-provision-redeem")
    t.start()


def fetch_agent_info(
    api_url: str,
    api_key: str,
    push_gui_event: Any,
) -> None:
    """Fetch facility + customer info from GET /api/agent/info.

    Runs on a daemon thread.  Pushes one of:
      {"event": "agent_info", "data": {...}}
      {"event": "agent_info_error", "error": "..."}
    """

    def _run() -> None:
        base = api_url.rstrip("/")
        # Strip trailing /api if already present so we don't double it
        if base.endswith("/api"):
            base = base[: -len("/api")]
        url = f"{base}/api/agent/info"
        print(f"[WRIGHT] GET {url}")
        try:
            r = requests.get(
                url,
                headers={"x-api-key": api_key},
                timeout=_TIMEOUT,
            )
            payload = r.json()
            print(f"[WRIGHT] GET {url} → {r.status_code}")
            if r.status_code == 200 and payload.get("success"):
                push_gui_event({"event": "agent_info", "data": payload["data"]})
            else:
                err = payload.get("error") or f"HTTP {r.status_code}"
                logger.warning("agent-info fetch failed: %s", err)
                push_gui_event({"event": "agent_info_error", "error": err})
        except Exception as exc:
            print(f"[WRIGHT] GET {url} → ERROR: {exc}")
            logger.warning("agent-info fetch exception: %s", exc)
            push_gui_event({"event": "agent_info_error", "error": str(exc)})

    t = threading.Thread(target=_run, daemon=True, name="wright-portal-info")
    t.start()
