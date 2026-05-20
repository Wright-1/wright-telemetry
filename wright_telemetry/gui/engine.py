"""ScanningEngine — owns the scheduler, WebSocket, and scan-queue threads.

Runs scheduler.run() and WebSocketClient in daemon threads so the Qt
event loop is never blocked.  A 250ms QTimer drains the GUI event queue
and re-emits the appropriate Qt signal on the main thread.
"""

from __future__ import annotations

import logging
import threading
import traceback
from typing import Any

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from wright_telemetry.ws_client import AgentController, WebSocketClient
from wright_telemetry.gui.scan_manager import ScanManager

logger = logging.getLogger(__name__)


class EngineSignals(QObject):
    """Qt signals emitted on the main thread from background engine events."""

    # ── WebSocket ────────────────────────────────────────────────────────────
    ws_status_changed = pyqtSignal(str)     # connecting|connected|reconnecting|disconnected

    # ── Scheduler ────────────────────────────────────────────────────────────
    miner_count_changed = pyqtSignal(int)
    poll_cycle_complete = pyqtSignal()

    # ── Discovery / scan queue ────────────────────────────────────────────────
    scan_queued = pyqtSignal(str)               # subnet
    scan_started = pyqtSignal(str, int)         # subnet, total_hosts
    scan_progress = pyqtSignal(str, int, int)   # subnet, scanned, total
    scan_complete = pyqtSignal(str, int, object)# subnet, miners_found, firmware_breakdown dict
    scan_cancelled = pyqtSignal(str)            # subnet
    scan_queue_empty = pyqtSignal()
    discovery_total_changed = pyqtSignal(int)   # total miners across all subnets
    subnet_removed = pyqtSignal(str)            # subnet removed by user

    # ── Portal metadata ──────────────────────────────────────────────────────
    agent_info_loaded = pyqtSignal(dict)        # facility + customer details
    agent_info_error  = pyqtSignal(str)         # human-readable error


class ScanningEngine:
    """Manages the background scanning loop, portal WebSocket, and scan queue.

    Usage::

        engine = ScanningEngine(cfg)
        engine.start()          # after QApplication is running
        engine.update_consent(consent_dict)
        engine.enqueue_subnet("192.168.1.0/24")
        engine.stop()           # from closeEvent
    """

    def __init__(self, cfg: dict[str, Any]) -> None:
        self._cfg = cfg
        self.controller = AgentController()
        self.signals = EngineSignals()

        fw_types = cfg.get("collector_types") or ["braiins", "bitmain", "luxos", "vnish"]
        self.scan_manager = ScanManager(self.controller, fw_types)

        self._scheduler_thread: threading.Thread | None = None
        self._ws_client: WebSocketClient | None = None

        self._timer = QTimer()
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._drain_events)

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start all background threads and the drain timer.

        Must be called after QApplication is running.
        """
        # Register collector adapters (triggers @register decorators)
        import wright_telemetry.collectors.bitmain  # noqa: F401
        import wright_telemetry.collectors.braiins  # noqa: F401
        import wright_telemetry.collectors.luxos    # noqa: F401
        import wright_telemetry.collectors.vnish    # noqa: F401

        # Scheduler thread
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

        # Auto-enqueue: detected local subnets + any already in config
        self._auto_enqueue_initial_subnets()

        # Fetch facility / customer info from portal
        self._fetch_agent_info()

        # GUI event drain timer
        self._timer.start()

    def stop(self) -> None:
        """Stop the drain timer.  Daemon threads are cleaned up by the OS."""
        self._timer.stop()

    # ── GUI → backend mutations ──────────────────────────────────────────────

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

    def enqueue_subnet(self, subnet: str) -> None:
        """Save subnet to config and add to the scan queue."""
        subnet = subnet.strip()
        if not subnet:
            return
        from wright_telemetry.config import load_config, save_config
        cfg = load_config() or {}
        disc = cfg.setdefault("discovery", {})
        subnets: list[str] = disc.get("subnets", [])
        if subnet not in subnets:
            subnets.append(subnet)
            disc["subnets"] = subnets
            disc.setdefault("enabled", True)
            cfg["discovery"] = disc
            save_config(cfg)
            self._cfg = cfg
            self.controller.request_config_reload()
        self.scan_manager.enqueue([subnet])

    def cancel_scan(self) -> None:
        """Cancel the currently running scan."""
        self.scan_manager.cancel()

    def start_scan(self) -> None:
        """Start or resume scanning — re-queues all known subnets."""
        self.scan_manager.start_all()

    def remove_subnet(self, subnet: str) -> None:
        """Remove subnet from scan queue, results, and saved config."""
        self.scan_manager.remove(subnet)
        from wright_telemetry.config import load_config, save_config
        cfg = load_config() or {}
        disc = cfg.get("discovery", {})
        subnets: list[str] = disc.get("subnets", [])
        if subnet in subnets:
            subnets.remove(subnet)
            disc["subnets"] = subnets
            cfg["discovery"] = disc
            save_config(cfg)
            self._cfg = cfg
            self.controller.request_config_reload()
        # Tell the GUI to remove the row
        self.controller.push_gui_event({"event": "subnet_removed", "subnet": subnet})

    def update_firmware_types(self, types: list[str]) -> None:
        """Persist firmware type selection and re-queue all subnets for rescan."""
        from wright_telemetry.config import load_config, save_config
        cfg = load_config() or {}
        cfg["collector_types"] = types
        save_config(cfg)
        self._cfg = cfg
        print(f"[WRIGHT] Firmware types updated: {types}")
        logger.info("Firmware types updated via GUI: %s", types)
        self.controller.request_config_reload()
        self.scan_manager.update_firmware_types(types)

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _fetch_agent_info(self) -> None:
        from wright_telemetry.gui.portal_client import fetch_agent_info
        fetch_agent_info(
            api_url=self._cfg.get("wright_api_url", ""),
            api_key=self._cfg.get("wright_api_key", ""),
            push_gui_event=self.controller.push_gui_event,
        )

    def _auto_enqueue_initial_subnets(self) -> None:
        """Enqueue detected local subnets + any already saved in config."""
        from wright_telemetry.discovery import default_subnets
        detected = default_subnets()
        config_subnets = self._cfg.get("discovery", {}).get("subnets", [])
        all_subnets = list(dict.fromkeys(detected + config_subnets))  # dedupe, preserve order
        if all_subnets:
            logger.info("Auto-enqueueing %d subnet(s) for initial scan", len(all_subnets))
            # Mark detected subnets as local; config subnets keep local=False
            # unless they were also detected
            self.scan_manager.enqueue(detected, local=True)
            extra = [s for s in config_subnets if s not in detected]
            if extra:
                self.scan_manager.enqueue(extra, local=False)

    # ── Event drain (Qt main thread via QTimer) ──────────────────────────────

    def _drain_events(self) -> None:
        for event in self.controller.pop_gui_events():
            etype = event.get("event")

            if etype == "ws_status":
                self.signals.ws_status_changed.emit(event.get("status", "disconnected"))

            elif etype == "miners_resolved":
                self.signals.miner_count_changed.emit(event.get("count", 0))

            elif etype == "poll_cycle_complete":
                self.signals.poll_cycle_complete.emit()

            elif etype == "scan_queued":
                self.signals.scan_queued.emit(event["subnet"])

            elif etype == "scan_started":
                self.signals.scan_started.emit(event["subnet"], event["total"])

            elif etype == "scan_progress":
                self.signals.scan_progress.emit(
                    event["subnet"], event["scanned"], event["total"]
                )

            elif etype == "scan_complete":
                self.signals.scan_complete.emit(
                    event["subnet"],
                    event["miners_found"],
                    event["firmware_breakdown"],
                )

            elif etype == "scan_cancelled":
                self.signals.scan_cancelled.emit(event["subnet"])

            elif etype == "scan_queue_empty":
                self.signals.scan_queue_empty.emit()

            elif etype == "discovery_total":
                self.signals.discovery_total_changed.emit(event["total"])

            elif etype == "subnet_removed":
                self.signals.subnet_removed.emit(event["subnet"])

            elif etype == "agent_info":
                self.signals.agent_info_loaded.emit(event["data"])

            elif etype == "agent_info_error":
                self.signals.agent_info_error.emit(event["error"])
