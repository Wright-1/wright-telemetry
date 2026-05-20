"""Discovery page — live subnet scan queue with per-host progress."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from wright_telemetry.gui.fonts import make_font
from wright_telemetry.gui import theme as T
from wright_telemetry.gui.scan_manager import SubnetScanResult

if TYPE_CHECKING:
    from wright_telemetry.gui.engine import ScanningEngine


# ── Helpers ───────────────────────────────────────────────────────────────────

def _lbl(text: str, size: int, weight: int, color: str, wrap: bool = False) -> QLabel:
    l = QLabel(text)
    l.setFont(make_font(size, weight))
    l.setStyleSheet(f"color: {color}; background: transparent;")
    if wrap:
        l.setWordWrap(True)
    return l


def _hdivider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    line.setStyleSheet(f"background: {T.BORDER_DEFAULT}; border: none;")
    return line


def _time_ago(ts: Optional[float]) -> str:
    if ts is None:
        return "—"
    secs = int(time.time() - ts)
    if secs < 10:
        return "Just now"
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def _firmware_summary(breakdown: dict[str, int]) -> str:
    if not breakdown:
        return "—"
    return "  ".join(f"{fw}:{n}" for fw, n in sorted(breakdown.items()))


# ── Alert card ────────────────────────────────────────────────────────────────

class _AlertCard(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("alert")
        self.setStyleSheet("""
            QWidget#alert {
                background: #FFF8F8;
                border: 1px solid #FECACA;
                border-left: 3px solid """ + T.ACCENT_RED + """;
                border-radius: 8px;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        title_row.addWidget(_lbl("⚠", 16, 600, T.ACCENT_RED))
        title_row.addWidget(_lbl("No miners detected", 14, 600, T.TEXT_PRIMARY))
        title_row.addStretch()
        layout.addLayout(title_row)

        desc = _lbl(
            "The scan completed but found no compatible miners. "
            "Check network connectivity, firmware selection, and that "
            "miners are on the same subnet as this host.",
            12, 400, T.TEXT_SECONDARY, wrap=True,
        )
        desc.setContentsMargins(32, 0, 0, 0)
        layout.addWidget(desc)


# ── Progress card ─────────────────────────────────────────────────────────────

class _ProgressCard(QWidget):
    def __init__(self, engine: Optional["ScanningEngine"], parent=None):
        super().__init__(parent)
        self._engine = engine
        self._scanning = False

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("prog_card")
        self.setStyleSheet(f"""
            QWidget#prog_card {{
                background: {T.BG_CARD};
                border: 1px solid {T.BORDER_DEFAULT};
                border-radius: 8px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        # Header row
        hdr = QHBoxLayout()
        self._title_lbl = _lbl("SCAN PROGRESS", 11, 700, T.TEXT_MUTED)
        hdr.addWidget(self._title_lbl)
        hdr.addStretch()

        self._pct_lbl = _lbl("—", 12, 700, T.TEXT_PRIMARY)
        hdr.addWidget(self._pct_lbl)

        self._action_btn = QPushButton("▷  START SCAN")
        self._action_btn.setFont(make_font(11, 600))
        self._action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._action_btn.setFixedHeight(30)
        self._action_btn.clicked.connect(self._on_action)
        self._style_btn_start()
        hdr.addSpacing(10)
        hdr.addWidget(self._action_btn)
        layout.addLayout(hdr)

        # Progress bar
        self._bar = QProgressBar()
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(8)
        self._bar.setStyleSheet(f"""
            QProgressBar {{
                background: {T.BORDER_DEFAULT};
                border-radius: 4px;
                border: none;
            }}
            QProgressBar::chunk {{
                background: {T.TEXT_PRIMARY};
                border-radius: 4px;
            }}
        """)
        layout.addWidget(self._bar)

        # Footer row
        ftr = QHBoxLayout()
        self._status_lbl = _lbl("No scan running", 12, 400, T.TEXT_SECONDARY)
        ftr.addWidget(self._status_lbl)
        ftr.addStretch()
        self._counts_lbl = _lbl("", 12, 400, T.TEXT_MUTED)
        ftr.addWidget(self._counts_lbl)
        layout.addLayout(ftr)

    # ── Slots ────────────────────────────────────────────────────────────────

    def set_scanning(self, subnet: str, total: int) -> None:
        self._scanning = True
        self._title_lbl.setText("SCANNING")
        self._status_lbl.setText(f"Scanning: {subnet}")
        self._counts_lbl.setText(f"0 / {total} hosts")
        self._bar.setMaximum(total)
        self._bar.setValue(0)
        self._pct_lbl.setText("0%")
        self._style_btn_cancel()

    def update_progress(self, subnet: str, scanned: int, total: int) -> None:
        pct = int(scanned / total * 100) if total else 0
        self._bar.setMaximum(total)
        self._bar.setValue(scanned)
        self._pct_lbl.setText(f"{pct}%")
        self._counts_lbl.setText(f"{scanned} / {total} hosts")

    def set_idle(self, last_scan_ts: Optional[float] = None) -> None:
        self._scanning = False
        self._title_lbl.setText("SCAN PROGRESS")
        checked = _time_ago(last_scan_ts)
        self._status_lbl.setText(f"Last scan: {checked}")
        self._counts_lbl.setText("")
        self._bar.setValue(0)
        self._pct_lbl.setText("—")
        self._style_btn_start()

    def set_cancelled(self, subnet: str) -> None:
        self.set_idle()
        self._status_lbl.setText(f"Cancelled: {subnet}")

    # ── Button helpers ────────────────────────────────────────────────────────

    def _style_btn_start(self) -> None:
        self._action_btn.setText("▷  START SCAN")
        self._action_btn.setStyleSheet(f"""
            QPushButton {{
                background: {T.TEXT_PRIMARY};
                color: #FFFFFF;
                border: none;
                border-radius: 6px;
                padding: 4px 14px;
            }}
            QPushButton:hover {{ background: #2A2D35; }}
        """)

    def _style_btn_cancel(self) -> None:
        self._action_btn.setText("⊗  CANCEL")
        self._action_btn.setStyleSheet(f"""
            QPushButton {{
                background: {T.ACCENT_RED};
                color: #FFFFFF;
                border: none;
                border-radius: 6px;
                padding: 4px 14px;
            }}
            QPushButton:hover {{ background: #DC2626; }}
        """)

    def _on_action(self) -> None:
        if self._engine is None:
            return
        if self._scanning:
            self._engine.cancel_scan()
        else:
            self._engine.start_scan()


# ── Subnet entry card ─────────────────────────────────────────────────────────

class _SubnetEntryCard(QWidget):
    FIRMWARE_OPTIONS = [
        ("braiins", "Braiins OS"),
        ("luxos",   "LuxOS"),
        ("vnish",   "Vnish"),
        ("bitmain", "Bitmain"),
    ]

    def __init__(self, engine: Optional["ScanningEngine"], parent=None):
        super().__init__(parent)
        self._engine = engine

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("subnet_card")
        self.setStyleSheet(f"""
            QWidget#subnet_card {{
                background: {T.BG_CARD};
                border: 1px solid {T.BORDER_DEFAULT};
                border-radius: 8px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        layout.addWidget(_lbl("Manual Subnet Entry", 15, 700, T.TEXT_PRIMARY))

        # ── Firmware checkboxes ───────────────────────────────────────────────
        layout.addWidget(_lbl("FIRMWARE TYPES", 11, 600, T.TEXT_MUTED))

        active_types: list[str] = []
        if engine is not None:
            active_types = engine._cfg.get("collector_types") or []

        self._fw_checks: dict[str, QCheckBox] = {}
        grid_row1 = QHBoxLayout()
        grid_row2 = QHBoxLayout()
        for i, (key, label) in enumerate(self.FIRMWARE_OPTIONS):
            chk = QCheckBox(label)
            chk.setFont(make_font(12, 400))
            chk.setChecked(key in active_types or not active_types)
            chk.setStyleSheet(f"color: {T.TEXT_SECONDARY}; background: transparent;")
            chk.toggled.connect(self._on_firmware_changed)
            self._fw_checks[key] = chk
            (grid_row1 if i < 2 else grid_row2).addWidget(chk)

        grid_row1.addStretch()
        grid_row2.addStretch()
        layout.addLayout(grid_row1)
        layout.addLayout(grid_row2)

        layout.addSpacing(4)

        # ── CIDR input ────────────────────────────────────────────────────────
        layout.addWidget(_lbl("CIDR RANGE", 11, 600, T.TEXT_MUTED))
        self.cidr_input = QTextEdit()
        self.cidr_input.setPlaceholderText("192.168.1.0/24")
        self.cidr_input.setFixedHeight(70)
        self.cidr_input.setFont(make_font(12, 400))
        self.cidr_input.setStyleSheet(f"""
            QTextEdit {{
                background: {T.BG_WINDOW};
                border: 1px solid {T.BORDER_DEFAULT};
                border-radius: 6px;
                padding: 8px;
                color: {T.TEXT_PRIMARY};
            }}
        """)
        layout.addWidget(self.cidr_input)

        add_btn = QPushButton("ADD TO QUEUE")
        add_btn.setFont(make_font(12, 700))
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.setStyleSheet(f"""
            QPushButton {{
                background: {T.TEXT_PRIMARY};
                color: #FFFFFF;
                border: none;
                border-radius: 6px;
                padding: 11px;
                letter-spacing: 0.5px;
            }}
            QPushButton:hover {{ background: #2A2D35; }}
        """)
        add_btn.clicked.connect(self._on_add)
        layout.addWidget(add_btn)

        layout.addStretch()

        # ── Guidance box ──────────────────────────────────────────────────────
        guidance = QWidget()
        guidance.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        guidance.setObjectName("guidance")
        guidance.setStyleSheet(f"""
            QWidget#guidance {{
                background: {T.BG_SIDEBAR};
                border: 1px solid {T.BORDER_DEFAULT};
                border-radius: 6px;
            }}
        """)
        g = QVBoxLayout(guidance)
        g.setContentsMargins(14, 12, 14, 12)
        g.setSpacing(4)
        g_hdr = QHBoxLayout()
        g_hdr.setSpacing(6)
        g_hdr.addWidget(_lbl("ⓘ", 13, 400, T.TEXT_MUTED))
        g_hdr.addWidget(_lbl("Subnet Guidance", 12, 600, T.TEXT_PRIMARY))
        g_hdr.addStretch()
        g.addLayout(g_hdr)
        g.addWidget(_lbl(
            "Enter a CIDR block (e.g. 192.168.1.0/24), an IP range "
            "(10.0.0.1-10.0.0.50), or a single IP.",
            12, 400, T.TEXT_SECONDARY, wrap=True,
        ))
        layout.addWidget(guidance)

    def _on_firmware_changed(self) -> None:
        if self._engine is None:
            return
        selected = [k for k, chk in self._fw_checks.items() if chk.isChecked()]
        self._engine.update_firmware_types(selected)

    def _on_add(self) -> None:
        if self._engine is None:
            return
        raw = self.cidr_input.toPlainText().strip()
        if not raw:
            return
        for cidr in (c.strip() for c in raw.split(",") if c.strip()):
            self._engine.enqueue_subnet(cidr)
        self.cidr_input.clear()


# ── Scan row ──────────────────────────────────────────────────────────────────

class _ScanRow(QWidget):
    """One live-updatable row in the Active Network Scans table."""

    _STATUS_STYLES = {
        "queued":    ("⊙ Queued",    T.ACCENT_ORANGE),
        "scanning":  ("↻ Scanning",  T.ACCENT_GREEN),
        "complete":  ("✓ Complete",  T.TEXT_MUTED),
        "cancelled": ("⊗ Cancelled", T.ACCENT_RED),
    }

    def __init__(self, result: SubnetScanResult, engine: Optional["ScanningEngine"],
                 parent=None):
        super().__init__(parent)
        self._engine = engine
        self.subnet = result.subnet
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("background: transparent;")

        row = QHBoxLayout(self)
        row.setContentsMargins(20, 10, 20, 10)
        row.setSpacing(0)

        self._status_lbl = _lbl("", 12, 500, T.TEXT_MUTED)
        self._status_lbl.setFixedWidth(110)
        row.addWidget(self._status_lbl)

        self._cidr_lbl = _lbl(result.subnet, 12, 400, T.TEXT_PRIMARY)
        row.addWidget(self._cidr_lbl, 2)

        self._miners_lbl = _lbl("—", 12, 400, T.TEXT_SECONDARY)
        self._miners_lbl.setFixedWidth(55)
        self._miners_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self._miners_lbl)

        row.addSpacing(16)

        self._fw_lbl = _lbl("—", 11, 400, T.TEXT_MUTED)
        row.addWidget(self._fw_lbl, 3)

        self._time_lbl = _lbl("—", 11, 400, T.TEXT_MUTED)
        self._time_lbl.setFixedWidth(72)
        self._time_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self._time_lbl)

        row.addSpacing(8)

        self._action_btn = QPushButton("↺")
        self._action_btn.setFixedSize(30, 30)
        self._action_btn.setFont(make_font(14, 400))
        self._action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._action_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {T.TEXT_MUTED};
                border: none; border-radius: 4px;
            }}
            QPushButton:hover {{
                background: {T.BG_SIDEBAR}; color: {T.TEXT_PRIMARY};
            }}
        """)
        self._action_btn.clicked.connect(self._on_action)
        row.addWidget(self._action_btn)

        self.update(result)

    def update(self, result: SubnetScanResult) -> None:  # type: ignore[override]
        label, color = self._STATUS_STYLES.get(result.status, ("—", T.TEXT_MUTED))
        self._status_lbl.setText(label)
        self._status_lbl.setStyleSheet(f"color: {color}; background: transparent;")

        cidr_color = T.TEXT_PRIMARY if result.status != "complete" else T.TEXT_MUTED
        self._cidr_lbl.setStyleSheet(f"color: {cidr_color}; background: transparent;")

        if result.status in ("queued", "cancelled"):
            self._miners_lbl.setText("—")
            self._fw_lbl.setText("—")
        elif result.status == "scanning":
            pct = int(result.scanned_hosts / result.total_hosts * 100) if result.total_hosts else 0
            self._miners_lbl.setText(f"{pct}%")
            self._fw_lbl.setText("scanning…")
        else:  # complete
            self._miners_lbl.setText(str(result.miners_found))
            self._fw_lbl.setText(_firmware_summary(result.firmware_breakdown))

        self._time_lbl.setText(_time_ago(result.last_scanned))

        # Action button
        if result.status == "scanning":
            self._action_btn.setText("⊗")
            self._action_btn.setToolTip("Cancel scan")
        elif result.status == "queued":
            self._action_btn.setText("—")
        else:
            self._action_btn.setText("↺")
            self._action_btn.setToolTip("Rescan subnet")

    def _on_action(self) -> None:
        if self._engine is None:
            return
        if self._action_btn.text() == "⊗":
            self._engine.cancel_scan()
        elif self._action_btn.text() == "↺":
            self._engine.scan_manager.enqueue([self.subnet])


# ── Active scans card ─────────────────────────────────────────────────────────

class _ActiveScansCard(QWidget):
    def __init__(self, engine: Optional["ScanningEngine"], parent=None):
        super().__init__(parent)
        self._engine = engine
        self._rows: dict[str, _ScanRow] = {}

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("scans_card")
        self.setStyleSheet(f"""
            QWidget#scans_card {{
                background: {T.BG_CARD};
                border: 1px solid {T.BORDER_DEFAULT};
                border-radius: 8px;
            }}
        """)

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)

        # ── Header ─────────────────────────────────────────────────────────────
        hdr = QWidget()
        hdr.setStyleSheet("background: transparent;")
        hdr_layout = QHBoxLayout(hdr)
        hdr_layout.setContentsMargins(20, 16, 20, 12)
        hdr_layout.addWidget(_lbl("Active Network Scans", 15, 700, T.TEXT_PRIMARY))
        hdr_layout.addStretch()

        self._badge_lbl = QLabel("0 ACTIVE")
        self._badge_lbl.setFont(make_font(11, 700))
        badge_wrap = QWidget()
        badge_wrap.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        badge_wrap.setObjectName("badge")
        badge_wrap.setStyleSheet("QWidget#badge { background: #DCFCE7; border-radius: 10px; }")
        b = QHBoxLayout(badge_wrap)
        b.setContentsMargins(10, 4, 10, 4)
        b.setSpacing(5)
        b.addWidget(_lbl("●", 8, 400, T.ACCENT_GREEN))
        self._badge_lbl.setStyleSheet(f"color: {T.ACCENT_GREEN}; background: transparent;")
        b.addWidget(self._badge_lbl)
        hdr_layout.addWidget(badge_wrap)
        self._outer.addWidget(hdr)
        self._outer.addWidget(_hdivider())

        # ── Column headers ─────────────────────────────────────────────────────
        col_hdr = QWidget()
        col_hdr.setStyleSheet("background: transparent;")
        ch = QHBoxLayout(col_hdr)
        ch.setContentsMargins(20, 8, 20, 8)
        ch.setSpacing(0)

        for text, width, stretch, align_right in [
            ("STATUS",          110, 0, False),
            ("SUBNET CIDR",     0,   2, False),
            ("MINERS",          55,  0, True),
            ("",                16,  0, False),   # spacer
            ("FIRMWARES FOUND", 0,   3, False),
            ("LAST CHECKED",    72,  0, True),
            ("",                38,  0, False),   # actions spacer
        ]:
            lbl = _lbl(text, 11, 600, T.TEXT_MUTED)
            if width:
                lbl.setFixedWidth(width)
            if align_right:
                lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            ch.addWidget(lbl, stretch)

        self._outer.addWidget(col_hdr)
        self._outer.addWidget(_hdivider())

        # ── Scrollable row area ────────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent;")

        self._rows_widget = QWidget()
        self._rows_widget.setStyleSheet("background: transparent;")
        self._rows_layout = QVBoxLayout(self._rows_widget)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(0)
        self._rows_layout.addStretch()

        scroll.setWidget(self._rows_widget)
        self._outer.addWidget(scroll, 1)

    def add_or_update_row(self, result: SubnetScanResult) -> None:
        if result.subnet in self._rows:
            self._rows[result.subnet].update(result)
        else:
            row = _ScanRow(result, self._engine)
            self._rows[result.subnet] = row
            # Insert before the trailing stretch
            insert_at = self._rows_layout.count() - 1
            self._rows_layout.insertWidget(insert_at, row)
            self._rows_layout.insertWidget(insert_at + 1, _hdivider())
        self._refresh_badge()

    def _refresh_badge(self) -> None:
        active = sum(
            1 for r in self._rows.values()
            if r._status_lbl.text().startswith("↻")
        )
        self._badge_lbl.setText(f"{active} ACTIVE")


# ── Page ──────────────────────────────────────────────────────────────────────

class DiscoveryPage(QWidget):
    """Discovery page with live subnet scan queue."""

    def __init__(self, engine: Optional["ScanningEngine"] = None, parent=None):
        super().__init__(parent)
        self._engine = engine
        self._scan_completed = False   # tracks if any scan has ever finished
        self._total_miners = 0
        self._last_scan_ts: Optional[float] = None

        self.setStyleSheet(f"background: {T.BG_WINDOW};")

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent;")

        content = QWidget()
        content.setStyleSheet(f"background: {T.BG_WINDOW};")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(T.CONTENT_PADDING, T.CONTENT_PADDING,
                                  T.CONTENT_PADDING, T.CONTENT_PADDING)
        layout.setSpacing(16)

        # ── Heading ───────────────────────────────────────────────────────────
        layout.addWidget(_lbl("Discover Miners", 22, 700, T.TEXT_PRIMARY))

        status_row = QHBoxLayout()
        status_row.setSpacing(6)
        self._status_dot = _lbl("●", 10, 400, T.ACCENT_ORANGE)
        status_row.addWidget(self._status_dot)
        self._status_lbl = _lbl(
            "Scanning local network…", 13, 400, T.TEXT_SECONDARY
        )
        status_row.addWidget(self._status_lbl)
        status_row.addStretch()
        layout.addLayout(status_row)

        # ── Alert card (hidden until a scan completes with 0 miners) ──────────
        self._alert = _AlertCard()
        self._alert.setVisible(False)
        layout.addWidget(self._alert)

        # ── Progress card ─────────────────────────────────────────────────────
        self._progress = _ProgressCard(engine)
        layout.addWidget(self._progress)

        # ── Two-column layout ─────────────────────────────────────────────────
        two_col = QHBoxLayout()
        two_col.setSpacing(16)

        self._entry = _SubnetEntryCard(engine)
        self._entry.setFixedWidth(310)
        two_col.addWidget(self._entry)

        self._scans_card = _ActiveScansCard(engine)
        two_col.addWidget(self._scans_card, 1)

        layout.addLayout(two_col)
        layout.addStretch()

        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        # ── Populate from existing scan state ─────────────────────────────────
        if engine is not None:
            for result in engine.scan_manager.get_all_results():
                self._scans_card.add_or_update_row(result)
                if result.status == "complete":
                    self._scan_completed = True
                    if result.last_scanned:
                        if self._last_scan_ts is None or result.last_scanned > self._last_scan_ts:
                            self._last_scan_ts = result.last_scanned

            if engine.scan_manager.is_scanning():
                self._status_dot.setStyleSheet(f"color: {T.ACCENT_ORANGE}; background: transparent;")
                self._status_lbl.setText("Scan in progress…")
            else:
                self._status_dot.setStyleSheet(f"color: {T.TEXT_MUTED}; background: transparent;")
                self._status_lbl.setText("No active scan")

            self._connect_signals(engine)

        # ── Refresh "last checked" timestamps every 30s ───────────────────────
        self._ts_timer = QTimer(self)
        self._ts_timer.setInterval(30_000)
        self._ts_timer.timeout.connect(self._refresh_timestamps)
        self._ts_timer.start()

    # ── Signal connections ────────────────────────────────────────────────────

    def _connect_signals(self, engine: "ScanningEngine") -> None:
        engine.signals.scan_queued.connect(self._on_scan_queued)
        engine.signals.scan_started.connect(self._on_scan_started)
        engine.signals.scan_progress.connect(self._on_scan_progress)
        engine.signals.scan_complete.connect(self._on_scan_complete)
        engine.signals.scan_cancelled.connect(self._on_scan_cancelled)
        engine.signals.scan_queue_empty.connect(self._on_queue_empty)
        engine.signals.discovery_total_changed.connect(self._on_total_changed)

    # ── Signal slots ──────────────────────────────────────────────────────────

    def _on_scan_queued(self, subnet: str) -> None:
        from wright_telemetry.gui.scan_manager import SubnetScanResult
        result = SubnetScanResult(subnet=subnet, status="queued")
        self._scans_card.add_or_update_row(result)
        self._status_dot.setStyleSheet(f"color: {T.ACCENT_ORANGE}; background: transparent;")
        self._status_lbl.setText("Scan queued…")

    def _on_scan_started(self, subnet: str, total: int) -> None:
        from wright_telemetry.gui.scan_manager import SubnetScanResult
        result = SubnetScanResult(subnet=subnet, status="scanning", total_hosts=total)
        self._scans_card.add_or_update_row(result)
        self._progress.set_scanning(subnet, total)
        self._status_dot.setStyleSheet(f"color: {T.ACCENT_ORANGE}; background: transparent;")
        self._status_lbl.setText(f"Scanning {subnet}…")

    def _on_scan_progress(self, subnet: str, scanned: int, total: int) -> None:
        self._progress.update_progress(subnet, scanned, total)
        from wright_telemetry.gui.scan_manager import SubnetScanResult
        result = SubnetScanResult(
            subnet=subnet, status="scanning",
            total_hosts=total, scanned_hosts=scanned,
        )
        self._scans_card.add_or_update_row(result)

    def _on_scan_complete(self, subnet: str, miners_found: int, firmware_breakdown: object) -> None:
        self._scan_completed = True
        now = __import__("time").time()
        self._last_scan_ts = now
        from wright_telemetry.gui.scan_manager import SubnetScanResult
        result = SubnetScanResult(
            subnet=subnet, status="complete",
            miners_found=miners_found,
            firmware_breakdown=firmware_breakdown,  # type: ignore[arg-type]
            last_scanned=now,
        )
        self._scans_card.add_or_update_row(result)

    def _on_scan_cancelled(self, subnet: str) -> None:
        from wright_telemetry.gui.scan_manager import SubnetScanResult
        result = SubnetScanResult(subnet=subnet, status="cancelled")
        self._scans_card.add_or_update_row(result)
        self._progress.set_cancelled(subnet)

    def _on_queue_empty(self) -> None:
        self._progress.set_idle(self._last_scan_ts)
        self._status_dot.setStyleSheet(f"color: {T.ACCENT_GREEN}; background: transparent;")
        miners = self._total_miners
        self._status_lbl.setText(
            f"{miners} miner{'s' if miners != 1 else ''} found" if miners
            else "Scan complete — no miners found"
        )

    def _on_total_changed(self, total: int) -> None:
        self._total_miners = total
        self._alert.setVisible(self._scan_completed and total == 0)
        if total > 0:
            self._status_lbl.setText(
                f"{total} miner{'s' if total != 1 else ''} found across "
                f"{len(self._scans_card._rows)} subnet(s)"
            )
            self._status_dot.setStyleSheet(f"color: {T.ACCENT_GREEN}; background: transparent;")

    def _refresh_timestamps(self) -> None:
        """Refresh 'last checked' labels on all rows."""
        if self._engine is None:
            return
        for result in self._engine.scan_manager.get_all_results():
            if result.subnet in self._scans_card._rows:
                self._scans_card._rows[result.subnet].update(result)
