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

        from wright_telemetry.discovery import all_firmware_types
        fw_types = cfg.get("collector_types") or all_firmware_types()
        self.scan_manager = ScanManager(self.controller, fw_types)

        self._scheduler_thread: threading.Thread | None = None
        self._ws_client: WebSocketClient | None = None

        self._timer = QTimer()
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._drain_events)

        # Periodic re-discovery timer (Qt main thread).
        # Interval is read from config; defaults to 5 minutes.
        # Fires scan_manager.start_all() which re-queues every known subnet
        # through the existing wright-scanner thread — GUI progress events
        # flow exactly as for a user-triggered scan.
        discovery_interval_ms = int(
            cfg.get("discovery", {}).get("scan_interval_seconds", 300) * 1000
        )
        self._discovery_timer = QTimer()
        self._discovery_timer.setInterval(discovery_interval_ms)
        self._discovery_timer.timeout.connect(self._on_discovery_timer)

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start all background threads and the drain timer.

        Must be called after QApplication is running.
        """
        # Register collector adapters (triggers @register decorators)
        import wright_telemetry.collectors.bitmain  # noqa: F401
        import wright_telemetry.collectors.braiins  # noqa: F401
        import wright_telemetry.collectors.luxos    # noqa: F401
        import wright_telemetry.collectors.sealminer  # noqa: F401
        import wright_telemetry.collectors.vnish    # noqa: F401

        # Mark GUI mode BEFORE starting the scheduler thread so it never
        # races into the CLI discovery path on its very first _resolve_miners call.


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
        # Periodic discovery timer — first fire after one full interval so the
        # initial auto-scan on startup always runs before the first re-scan.
        self._discovery_timer.start()

    def stop(self) -> None:
        """Stop the drain timer, cancel any in-flight scan, and close the
        WebSocket connection gracefully."""
        self._timer.stop()
        self._discovery_timer.stop()
        self.scan_manager.cancel()
        if self._ws_client is not None:
            self._ws_client.stop()

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
        logger.info("Consent updated via GUI: enabled=%s disabled=%s", enabled, disabled)

        self.controller.request_config_reload()

    def enqueue_subnets(self, subnets: list[str]) -> None:
        """Save subnets to config and add them all to the scan queue in one batch."""
        cleaned = [s.strip() for s in subnets if s.strip()]
        if not cleaned:
            return
        from wright_telemetry.config import load_config, save_config
        cfg = load_config() or {}
        disc = cfg.setdefault("discovery", {})
        saved: list[str] = disc.get("subnets", [])
        added = [s for s in cleaned if s not in saved]
        if added:
            saved.extend(added)
            disc["subnets"] = saved
            disc.setdefault("enabled", True)
            cfg["discovery"] = disc
            save_config(cfg)
            self._cfg = cfg
            self.controller.request_config_reload()
        self.scan_manager.enqueue(cleaned)

    def enqueue_subnet(self, subnet: str) -> None:
        """Save subnet to config and add to the scan queue."""
        self.enqueue_subnets([subnet])

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

    #TODO - we may want a unique credential per subnet at some point
    def update_discovery_credentials(self, username: str, password: str) -> None:
        """Persist default miner credentials used during subnet discovery."""
        from wright_telemetry.config import encode_password, load_config, save_config
        cfg = load_config() or {}
        disc = cfg.setdefault("discovery", {})
        disc["default_username"] = username or "root"
        if password:
            disc["default_password_b64"] = encode_password(password)
        else:
            disc.pop("default_password_b64", None)
        save_config(cfg)
        self._cfg = cfg
        self.controller.request_config_reload()

    def update_firmware_types(self, types: list[str]) -> None:
        """Persist firmware type selection and re-queue all subnets for rescan."""
        from wright_telemetry.config import load_config, save_config
        cfg = load_config() or {}
        cfg["collector_types"] = types
        save_config(cfg)
        self._cfg = cfg
        logger.info("Firmware types updated via GUI: %s", types)
        self.controller.request_config_reload()
        self.scan_manager.update_firmware_types(types)

    # ── Internal helpers ─────────────────────────────────────────────────────

    def seconds_until_next_discovery(self) -> int:
        """Remaining seconds until the next automatic re-scan fires.

        Returns 0 when the timer is inactive or has already elapsed.
        Safe to call from the Qt main thread only.
        """
        ms = self._discovery_timer.remainingTime()
        return max(0, ms // 1000) if ms >= 0 else 0

    def _on_discovery_timer(self) -> None:
        """Periodic re-scan: re-queue every known subnet through the scanner.

        Only fires if there are subnets to re-scan.  The ScanManager skips
        subnets that are already queued or actively scanning, so this is safe
        to call even if a scan is still in progress.
        """
        if self.scan_manager.get_all_results():
            logger.info("Discovery timer fired — re-queuing all known subnets")
            self.scan_manager.start_all()

    def _fetch_agent_info(self) -> None:
        from wright_telemetry.portal_client import fetch_agent_info
        fetch_agent_info(
            api_key=self._cfg.get("wright_api_key", ""),
            facility_id=self._cfg.get("facility_id", ""),
            push_gui_event=self.controller.push_gui_event,
        )

    def _auto_enqueue_initial_subnets(self) -> None:
        """Enqueue detected local subnets + any already saved in config.

        Auto-detected subnets are also persisted to config so that deleting
        one from the GUI removes it permanently rather than having it
        reappear on the next restart.
        """
        from wright_telemetry.discovery import default_subnets
        from wright_telemetry.config import load_config, save_config
        detected = default_subnets()
        cfg = load_config() or self._cfg
        disc = cfg.setdefault("discovery", {})
        config_subnets: list[str] = disc.get("subnets", [])

        # Merge auto-detected subnets into config so the full list is
        # managed in one place and deletions are permanent.
        new_subnets = [s for s in detected if s not in config_subnets]
        if new_subnets:
            config_subnets = config_subnets + new_subnets
            disc["subnets"] = config_subnets
            disc.setdefault("enabled", True)
            save_config(cfg)
            self._cfg = cfg

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

            elif etype == "request_scan":
                # Scheduler is asking for a periodic re-discovery.
                # Route it through the ScanManager so the GUI gets live
                # progress events exactly as if the user had triggered the scan.
                subnets = event.get("subnets")
                if subnets:
                    self.scan_manager.enqueue(subnets)
                else:
                    # No specific subnets: re-queue everything already known
                    self.scan_manager.start_all()

            elif etype == "discovery_total":
                self.signals.discovery_total_changed.emit(event["total"])

            elif etype == "subnet_removed":
                self.signals.subnet_removed.emit(event["subnet"])

            elif etype == "agent_info":
                self.signals.agent_info_loaded.emit(event["data"])

            elif etype == "agent_info_error":
                self.signals.agent_info_error.emit(event["error"])
