"""Subnet scan queue — runs discovery scans independently of the scheduler.

A single worker thread drains the queue one subnet at a time.  Each scan
calls discovery.scan_hosts() with a progress callback that pushes live
GUI events via AgentController.push_gui_event().

Events pushed (all consumed by ScanningEngine._drain_events):
    scan_queued       {"event", "subnet"}
    scan_started      {"event", "subnet", "total"}
    scan_progress     {"event", "subnet", "scanned", "total"}
    scan_complete     {"event", "subnet", "miners_found", "firmware_breakdown", "last_scanned"}
    scan_cancelled    {"event", "subnet"}
    scan_queue_empty  {"event"}
    discovery_total   {"event", "total"}
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class SubnetScanResult:
    subnet: str
    status: str = "queued"          # queued | scanning | complete | cancelled
    total_hosts: int = 0
    scanned_hosts: int = 0
    miners_found: int = 0
    firmware_breakdown: dict[str, int] = field(default_factory=dict)
    last_scanned: Optional[float] = None
    local: bool = False             # True if auto-detected from local interfaces


class ScanManager:
    """Sequential subnet scan queue with live progress and cancel support."""

    def __init__(self, controller: Any, firmware_types: list[str]) -> None:
        self._controller = controller
        controller.has_scan_manager = True
        self._firmware_types: list[str] = list(firmware_types)
        self._queue: list[str] = []
        self._results: dict[str, SubnetScanResult] = {}
        self._local_subnets: set[str] = set()
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._current_subnet: Optional[str] = None
        # Per-subnet miner dicts (credential-free) shared with the scheduler.
        # Updated after each subnet scan; never written to the config file.
        self._found_miners: dict[str, list[dict]] = {}

    # ── Public API ──────────────────────────────────────────────────────────

    def enqueue(self, subnets: list[str], local: bool = False) -> None:
        """Add subnets to the queue; ignored if already queued or scanning."""
        with self._lock:
            for s in subnets:
                s = s.strip()
                if not s:
                    continue
                if local:
                    self._local_subnets.add(s)
                is_local = s in self._local_subnets
                if s not in self._results:
                    self._results[s] = SubnetScanResult(
                        subnet=s, status="queued", local=is_local
                    )
                    self._queue.append(s)
                    self._controller.push_gui_event({
                        "event": "scan_queued", "subnet": s, "local": is_local
                    })
                elif self._results[s].status not in ("queued", "scanning"):
                    self._results[s].status = "queued"
                    if s not in self._queue and s != self._current_subnet:
                        self._queue.append(s)
                    self._controller.push_gui_event({
                        "event": "scan_queued", "subnet": s, "local": is_local
                    })
        self._ensure_worker()

    def cancel(self) -> None:
        """Cancel the current scan and clear the entire queue."""
        with self._lock:
            self._queue.clear()
        self._cancel_event.set()

    def remove(self, subnet: str) -> None:
        """Remove a subnet from results and queue. Cancels it if currently scanning."""
        with self._lock:
            self._results.pop(subnet, None)
            self._found_miners.pop(subnet, None)
            if subnet in self._queue:
                self._queue.remove(subnet)
            if self._current_subnet == subnet:
                self._cancel_event.set()
            all_miners = [m for cfgs in self._found_miners.values() for m in cfgs]
        self._controller.set_discovered_miners(all_miners)

    def start_all(self) -> None:
        """Re-queue every known subnet and start scanning."""
        with self._lock:
            for s, result in self._results.items():
                if result.status not in ("queued", "scanning"):
                    result.status = "queued"
                    if s not in self._queue and s != self._current_subnet:
                        self._queue.append(s)
                    self._controller.push_gui_event({"event": "scan_queued", "subnet": s})
        self._ensure_worker()

    def update_firmware_types(self, firmware_types: list[str]) -> None:
        """Update firmware filter and re-queue all known subnets for a fresh scan."""
        self._firmware_types = list(firmware_types)
        with self._lock:
            for s, result in self._results.items():
                result.status = "queued"
                if s not in self._queue and s != self._current_subnet:
                    self._queue.append(s)
                self._controller.push_gui_event({"event": "scan_queued", "subnet": s})
        self._ensure_worker()

    def get_all_results(self) -> list[SubnetScanResult]:
        """Return a snapshot of all known subnet results (for page init)."""
        with self._lock:
            return [
                SubnetScanResult(
                    subnet=r.subnet,
                    status=r.status,
                    total_hosts=r.total_hosts,
                    scanned_hosts=r.scanned_hosts,
                    miners_found=r.miners_found,
                    firmware_breakdown=dict(r.firmware_breakdown),
                    last_scanned=r.last_scanned,
                    local=r.local,
                )
                for r in self._results.values()
            ]

    def total_miners(self) -> int:
        with self._lock:
            return sum(
                r.miners_found for r in self._results.values()
                if r.status == "complete"
            )

    def is_scanning(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    # ── Internal ────────────────────────────────────────────────────────────

    def _ensure_worker(self) -> None:
        # Keep the entire check-and-assign inside the lock so two concurrent
        # callers cannot both see already_running=False and both spawn a thread.
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            if not self._queue:
                return
            self._cancel_event.clear()
            self._worker = threading.Thread(
                target=self._run,
                daemon=True,
                name="wright-scanner",
            )
            self._worker.start()

    def _run(self) -> None:
        while True:
            with self._lock:
                if not self._queue:
                    self._current_subnet = None
                    break
                # Check for a pending cancel *before* clearing it, so a
                # cancel() call that arrived between two scans is honoured.
                if self._cancel_event.is_set():
                    self._current_subnet = None
                    break
                subnet = self._queue.pop(0)
                self._current_subnet = subnet
                # Clear only after we've committed to this subnet, still
                # inside the lock so cancel() cannot sneak in between.
                self._cancel_event.clear()

            self._scan_subnet(subnet)

        self._controller.push_gui_event({"event": "scan_queue_empty"})
        logger.info("Scan queue exhausted")

    def _scan_subnet(self, subnet: str) -> None:
        from wright_telemetry.discovery import parse_ip_target, scan_hosts

        # ── Expand CIDR → host list ──────────────────────────────────────────
        try:
            hosts = list(parse_ip_target(subnet))
        except Exception as exc:
            logger.warning("Invalid subnet %r: %s", subnet, exc)
            return

        total = len(hosts)
        logger.info("Scanning subnet %s (%d hosts, firmware=%s)", subnet, total, self._firmware_types)

        with self._lock:
            # Guard against the subnet being removed while we were expanding
            # the CIDR — remove() may have deleted it from _results.
            if subnet not in self._results:
                logger.debug("Subnet %s removed before scan could start", subnet)
                return
            r = self._results[subnet]
            r.status = "scanning"
            r.total_hosts = total
            r.scanned_hosts = 0

        self._controller.push_gui_event({
            "event": "scan_started",
            "subnet": subnet,
            "total": total,
        })

        if self._cancel_event.is_set():
            self._finish_cancelled(subnet)
            return

        # ── Run scan with per-host progress ──────────────────────────────────
        def progress_cb(scanned: int, _total: int) -> None:
            if self._cancel_event.is_set():
                return
            with self._lock:
                if subnet in self._results:
                    self._results[subnet].scanned_hosts = scanned
            self._controller.push_gui_event({
                "event": "scan_progress",
                "subnet": subnet,
                "scanned": scanned,
                "total": total,
            })

        fw = self._firmware_types or None
        found = scan_hosts(
            hosts,
            firmware_types=fw,
            progress_cb=progress_cb,
            cancel_event=self._cancel_event,
        )

        if self._cancel_event.is_set():
            self._finish_cancelled(subnet)
            return

        # ── Record results ────────────────────────────────────────────────────
        firmware_breakdown: dict[str, int] = {}
        for miner in found:
            firmware_breakdown[miner.firmware] = firmware_breakdown.get(miner.firmware, 0) + 1

        now = time.time()
        with self._lock:
            # Guard against removal that arrived after scan_hosts() returned.
            if subnet not in self._results:
                logger.debug("Subnet %s removed before results could be recorded", subnet)
                return
            r = self._results[subnet]
            r.status = "complete"
            r.miners_found = len(found)
            r.firmware_breakdown = firmware_breakdown
            r.last_scanned = now
            r.scanned_hosts = total

        logger.info(
            "Scan complete for %s: %d miner(s) found %s",
            subnet, len(found), firmware_breakdown,
        )

        # ── GUI events (live progress, unchanged) ────────────────────────────────
        self._controller.push_gui_event({
            "event": "scan_complete",
            "subnet": subnet,
            "miners_found": len(found),
            "firmware_breakdown": firmware_breakdown,
            "last_scanned": now,
        })
        self._controller.push_gui_event({
            "event": "discovery_total",
            "total": self.total_miners(),
        })

        # ── Shared Model update → scheduler ─────────────────────────────────
        # Convert DiscoveredMiner objects to credential-free dicts and store
        # them in the controller so the scheduler can read them directly.
        # Credentials are applied by the scheduler from the live config.
        miner_dicts = [
            {
                "name":        m.hostname or m.ip,
                "url":         f"http://{m.ip}",
                "firmware":    m.firmware,
                "mac_address": m.mac_address,
                "discovered":  True,
            }
            for m in found
        ]
        with self._lock:
            self._found_miners[subnet] = miner_dicts
            all_miners = [m for cfgs in self._found_miners.values() for m in cfgs]
        self._controller.set_discovered_miners(all_miners)

    def _finish_cancelled(self, subnet: str) -> None:
        with self._lock:
            if subnet in self._results:
                self._results[subnet].status = "cancelled"
        logger.info("Scan cancelled for %s", subnet)
        self._controller.push_gui_event({"event": "scan_cancelled", "subnet": subnet})
