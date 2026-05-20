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
        try:
            r = requests.get(
                url,
                headers={"x-api-key": api_key},
                timeout=_TIMEOUT,
            )
            payload = r.json()
            if r.status_code == 200 and payload.get("success"):
                push_gui_event({"event": "agent_info", "data": payload["data"]})
            else:
                err = payload.get("error") or f"HTTP {r.status_code}"
                logger.warning("agent-info fetch failed: %s", err)
                push_gui_event({"event": "agent_info_error", "error": err})
        except Exception as exc:
            logger.warning("agent-info fetch exception: %s", exc)
            push_gui_event({"event": "agent_info_error", "error": str(exc)})

    t = threading.Thread(target=_run, daemon=True, name="wright-portal-info")
    t.start()
