"""Logging configuration: stdout, rotating file, and Loki push handler.

The Loki handler is always-on operational tooling (not user-consent gated).
It ships collector logs to logs.wrightfan.com for centralized monitoring in
Grafana.  If Loki is unreachable the handler silently drops entries so it
never blocks the telemetry loop.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import socket
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional

import requests

from wright_telemetry import __version__
from wright_telemetry.config import CONFIG_DIR

LOG_DIR = CONFIG_DIR
LOG_FILE = LOG_DIR / "collector.log"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

_LOKI_DEFAULT_URL = "https://logs.wrightfan.com/loki/api/v1/push"
_LOKI_FLUSH_INTERVAL = 10  # seconds
_LOKI_BATCH_SIZE = 100


# ------------------------------------------------------------------
# Loki push handler
# ------------------------------------------------------------------

class LokiHandler(logging.Handler):
    """Batched log handler that pushes to Grafana Loki."""

    def __init__(
        self,
        url: str,
        auth_value: Optional[str],
        labels: dict[str, str],
        flush_interval: float = _LOKI_FLUSH_INTERVAL,
        batch_size: int = _LOKI_BATCH_SIZE,
    ):
        super().__init__()
        self.url = url
        self.auth_value = auth_value
        self.labels = labels
        self.flush_interval = flush_interval
        self.batch_size = batch_size

        self._buffer: list[tuple[str, str]] = []  # (nanosecond timestamp, formatted message)
        self._lock = threading.Lock()
        self._session = requests.Session()
        self._session.headers["Content-Type"] = "application/json"
        if self.auth_value:
            self._session.headers["Authorization"] = f"Basic {self.auth_value}"

        self._timer: Optional[threading.Timer] = None
        self._schedule_flush()
        atexit.register(self.flush)

    def _schedule_flush(self) -> None:
        self._timer = threading.Timer(self.flush_interval, self._timed_flush)
        self._timer.daemon = True
        self._timer.start()

    def _timed_flush(self) -> None:
        self.flush()
        self._schedule_flush()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            ns = str(int(record.created * 1e9))
            with self._lock:
                self._buffer.append((ns, msg))
                if len(self._buffer) >= self.batch_size:
                    self._do_flush()
        except Exception:
            self.handleError(record)

    def flush(self) -> None:
        with self._lock:
            self._do_flush()

    def _do_flush(self) -> None:
        """Send buffered entries to Loki. Must be called while holding ``_lock``."""
        if not self._buffer:
            return

        entries = list(self._buffer)
        self._buffer.clear()

        body = {
            "streams": [
                {
                    "stream": self.labels,
                    "values": entries,
                }
            ]
        }

        try:
            resp = self._session.post(self.url, data=json.dumps(body), timeout=5)
            if resp.status_code >= 400:
                # Don't log through the logging system to avoid recursion
                pass
        except Exception:
            pass  # silently drop -- never block telemetry

    def close(self) -> None:
        if self._timer:
            self._timer.cancel()
        self.flush()
        super().close()


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def _get_loki_auth() -> Optional[str]:
    """Resolve Loki auth via priority chain: env var -> None (disabled)."""
    return os.environ.get("WRIGHT_LOKI_AUTH")


def configure_logging(
    facility_id: str = "unknown",
    level: int = logging.INFO,
) -> None:
    """Set up all log handlers (stdout, file, Loki)."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    # Remove any previously-attached handlers (e.g. on restart within same process)
    for h in root.handlers[:]:
        root.removeHandler(h)

    formatter = logging.Formatter(LOG_FORMAT)

    # -- stdout --
    stdout_handler = logging.StreamHandler()
    stdout_handler.setFormatter(formatter)
    root.addHandler(stdout_handler)

    # -- Rotating file --
    file_handler = RotatingFileHandler(
        str(LOG_FILE),
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=3,
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # -- Loki --
    loki_auth = _get_loki_auth()
    if loki_auth:
        labels = {
            "job": "wright-telemetry",
            "facility_id": facility_id,
            "collector_version": __version__,
            "hostname": socket.gethostname(),
        }
        loki_handler = LokiHandler(
            url=_LOKI_DEFAULT_URL,
            auth_value=loki_auth,
            labels=labels,
        )
        loki_handler.setFormatter(formatter)
        root.addHandler(loki_handler)
        logging.getLogger(__name__).debug("Loki log handler enabled -> %s", _LOKI_DEFAULT_URL)
    else:
        logging.getLogger(__name__).debug("Loki log handler disabled (WRIGHT_LOKI_AUTH not set)")
