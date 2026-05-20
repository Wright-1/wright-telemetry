"""ScanningEngine — owns the scheduler and WebSocket threads.

Runs scheduler.run() and WebSocketClient in daemon threads so the Qt
event loop is never blocked.  A 250ms QTimer drains the GUI event queue
and re-emits the appropriate Qt signal on the main thread.
"""

from __future__ import annotations

import logging
import threading
import traceback
from typing import TYPE_CHECKING, Any

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from wright_telemetry.ws_client import AgentController, WebSocketClient

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class EngineSignals(QObject):
    """Qt signals emitted on the main thread from background engine events."""

    ws_status_changed = pyqtSignal(str)   # "connecting" | "connected" | "reconnecting" | "disconnected"
    miner_count_changed = pyqtSignal(int)
    poll_cycle_complete = pyqtSignal()


class ScanningEngine:
    """Manages the background scanning loop and portal WebSocket connection.

    Usage::

        engine = ScanningEngine(cfg)
        engine.start()          # call after QApplication is running
        engine.update_consent(consent_dict)
        engine.stop()           # call from closeEvent
    """

    def __init__(self, cfg: dict[str, Any]) -> None:
        self._cfg = cfg
        self.controller = AgentController()
        self.signals = EngineSignals()

        self._scheduler_thread: threading.Thread | None = None
        self._ws_client: WebSocketClient | None = None

        self._timer = QTimer()
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._drain_events)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start both background threads and the drain timer.

        Must be called after the QApplication event loop is running so
        the QTimer fires on the correct thread.
        """
        # Register collector adapters (triggers @register decorators)
        import wright_telemetry.collectors.bitmain  # noqa: F401
        import wright_telemetry.collectors.braiins  # noqa: F401
        import wright_telemetry.collectors.luxos    # noqa: F401
        import wright_telemetry.collectors.vnish    # noqa: F401

        # Scheduler thread — wrapped so exceptions are never silently swallowed
        from wright_telemetry.scheduler import run as scheduler_run

        def _run_scheduler() -> None:
            try:
                scheduler_run(self._cfg, controller=self.controller)
            except Exception:
                logger.exception("Scheduler thread crashed")
                traceback.print_exc()

        self._scheduler_thread = threading.Thread(
            target=_run_scheduler,
            daemon=True,
            name="wright-scheduler",
        )
        self._scheduler_thread.start()

        # WebSocket client
        self._ws_client = WebSocketClient(
            self.controller,
            api_url=self._cfg.get("wright_api_url", ""),
            api_key=self._cfg.get("wright_api_key", ""),
            facility_id=self._cfg.get("facility_id", ""),
        )
        self._ws_client.start()

        # GUI event drain timer
        self._timer.start()

    def stop(self) -> None:
        """Stop the drain timer.  Daemon threads are terminated by the OS
        when the Qt process exits."""
        self._timer.stop()

    # ------------------------------------------------------------------
    # GUI → backend mutations
    # ------------------------------------------------------------------

    def update_consent(self, consent: dict[str, bool]) -> None:
        """Persist consent changes and signal the scheduler to reload."""
        from wright_telemetry.config import load_config, save_config
        cfg = load_config() or {}
        cfg["consent"] = consent
        save_config(cfg)
        self._cfg = cfg

        enabled = [k for k, v in consent.items() if v]
        disabled = [k for k, v in consent.items() if not v]
        print(f"[WRIGHT] Consent saved — enabled: {enabled or 'none'}")
        if disabled:
            print(f"[WRIGHT]              disabled: {disabled}")
        logger.info("Consent updated via GUI: enabled=%s disabled=%s", enabled, disabled)

        self.controller.request_config_reload()

    # ------------------------------------------------------------------
    # Event drain (runs on Qt main thread via QTimer)
    # ------------------------------------------------------------------

    def _drain_events(self) -> None:
        for event in self.controller.pop_gui_events():
            etype = event.get("event")
            if etype == "ws_status":
                self.signals.ws_status_changed.emit(event.get("status", "disconnected"))
            elif etype == "miners_resolved":
                self.signals.miner_count_changed.emit(event.get("count", 0))
            elif etype == "poll_cycle_complete":
                self.signals.poll_cycle_complete.emit()
