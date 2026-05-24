"""Lightweight HTTP client for agent-facing portal endpoints.

All portal routes are unauthenticated or use per-request auth headers —
they do not go through WrightAPIClient's session.  URLs are built via
build_url(..., pipeline=False) from api_client so the base URL is always
read from settings in one place.

Network calls that serve the GUI run on a daemon thread so the Qt main
thread never blocks.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

import requests

from wright_telemetry.api_client import build_url

logger = logging.getLogger(__name__)

_TIMEOUT = 10  # seconds


def _redeem(access_key: str) -> dict:
    """Core redeem logic shared by the sync and async variants."""
    url = build_url("internal/provision/redeem", pipeline=False)
    print(f"[WRIGHT] POST {url}")
    try:
        r = requests.post(url, json={"accessKey": access_key}, timeout=_TIMEOUT)
        payload = r.json()
        print(f"[WRIGHT] POST {url} → {r.status_code}")
        if r.status_code == 200 and payload.get("success"):
            data = payload.get("data", {})
            print(f"[WRIGHT] Provisioned — facilityId: {data['facilityId']}  apiKey: {data['apiKey']}")
            return {"success": True, "apiKey": data["apiKey"], "facilityId": data["facilityId"]}
        err = payload.get("error") or payload.get("message") or f"HTTP {r.status_code}"
        logger.warning("access-key redeem failed: %s", err)
        return {"success": False, "error": err}
    except Exception as exc:
        print(f"[WRIGHT] POST {url} → ERROR: {exc}")
        logger.warning("access-key redeem exception: %s", exc)
        return {"success": False, "error": str(exc)}


def redeem_access_key_sync(access_key: str) -> dict:
    """Synchronous access-key redeem for TUI/CLI use.

    Returns:
      {"success": True,  "apiKey": "...", "facilityId": "..."}
      {"success": False, "error": "..."}
    """
    return _redeem(access_key)


def redeem_access_key(access_key: str, callback: Any) -> None:
    """Async access-key redeem for GUI use.

    Runs on a daemon thread and invokes *callback* with the same dict
    shape as redeem_access_key_sync.  The callback is responsible for
    marshalling back to the Qt main thread (use a pyqtSignal).
    """
    t = threading.Thread(
        target=lambda: callback(_redeem(access_key)),
        daemon=True,
        name="wright-provision-redeem",
    )
    t.start()


def fetch_agent_info(api_key: str, push_gui_event: Any) -> None:
    """Fetch facility + customer info from GET /api/agent/info.

    Runs on a daemon thread.  Pushes one of:
      {"event": "agent_info",       "data": {...}}
      {"event": "agent_info_error", "error": "..."}
    """

    def _run() -> None:
        url = build_url("agent/info", pipeline=False)
        print(f"[WRIGHT] GET {url}")
        try:
            r = requests.get(url, headers={"x-api-key": api_key}, timeout=_TIMEOUT)
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

    threading.Thread(target=_run, daemon=True, name="wright-portal-info").start()
