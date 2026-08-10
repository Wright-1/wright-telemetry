"""Discovery page — live subnet scan queue with per-host progress."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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
from wright_telemetry.gui.widgets import PrimaryButton, ToggleSwitch

if TYPE_CHECKING:
    from wright_telemetry.gui.engine import ScanningEngine


# ── Shared column widths (header + rows must match exactly) ───────────────────

_W_STATUS   = 108
_W_MINERS   =  56
_W_FW       = 168
_W_CHECKED  =  86
_W_ACTIONS  =  70   # two 30px buttons + gap
_W_GAP      =  12   # spacing between fixed cols


# ── Helpers ───────────────────────────────────────────────────────────────────

def _lbl(text: str, size: int, weight: int, color: str,
         wrap: bool = False, fixed_w: int = 0) -> QLabel:
    l = QLabel(text)
    l.setFont(make_font(size, weight))
    l.setStyleSheet(f"color: {color}; background: transparent;")
    if wrap:
        l.setWordWrap(True)
    if fixed_w:
        l.setFixedWidth(fixed_w)
    return l


def _hdiv() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setFixedHeight(1)
    f.setStyleSheet(f"background: {T.BORDER_DEFAULT}; border: none;")
    return f


def _time_ago(ts: Optional[float]) -> str:
    if ts is None:
        return "—"
    secs = int(time.time() - ts)
    if secs < 15:
        return "Just now"
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def _fw_summary(breakdown: dict) -> str:
    if not breakdown:
        return "—"
    return "  ".join(f"{fw}:{n}" for fw, n in sorted(breakdown.items()))


# ── Warning card (inline with heading — no side stripe) ───────────────────────

class _WarningCard(QWidget):
    """Urgent checklist shown when a scan completes with zero miners found."""

    _CHECKS = [
        "Miners are on the same subnet as this host.",
        "No firewall is blocking port 4028.",
        "You have selected the correct firmware types.",
        "Miners are powered on and running.",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("warn_card")
        self.setStyleSheet("""
            QWidget#warn_card {
                background: #FEF2F2;
                border: 1px solid #F87171;
                border-radius: 8px;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)

        # Title row
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        icon = _lbl("⚠", 15, 700, T.ACCENT_RED)
        icon.setFixedWidth(20)
        title_row.addWidget(icon)
        title = _lbl("No miners found", 13, 700, T.ACCENT_RED)
        title_row.addWidget(title)
        title_row.addStretch()
        layout.addLayout(title_row)

        sub = _lbl(
            "Scan finished with 0 results. Verify each of the following:",
            11, 400, "#9CA3AF",
        )
        layout.addWidget(sub)

        # Numbered checklist
        for i, item in enumerate(self._CHECKS, 1):
            row = QHBoxLayout()
            row.setSpacing(10)
            row.setContentsMargins(2, 0, 0, 0)
            num = _lbl(str(i), 11, 700, T.ACCENT_RED)
            num.setFixedWidth(14)
            row.addWidget(num)
            row.addWidget(_lbl(item, 12, 400, "#374151", wrap=True))
            row.addStretch()
            layout.addLayout(row)


# ── Firmware toggle row ───────────────────────────────────────────────────────

class _FirmwareToggle(QWidget):
    """Single firmware type toggle: [switch] [label] — matches permissions style."""

    def __init__(self, key: str, label: str, checked: bool, parent=None):
        super().__init__(parent)
        self.key = key
        self.setStyleSheet("background: transparent;")
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        self.toggle = ToggleSwitch(checked=checked)
        row.addWidget(self.toggle)
        row.addWidget(_lbl(label, 12, 500, T.TEXT_PRIMARY))

    def isChecked(self) -> bool:
        return self.toggle.isChecked()


# ── Per-firmware credential row ───────────────────────────────────────────────

_CRED_INPUT_STYLE = f"""
    QLineEdit {{
        background: {T.BG_WINDOW};
        border: 1px solid {T.BORDER_DEFAULT};
        border-radius: 6px;
        padding: 6px 10px;
        color: {T.TEXT_PRIMARY};
        font-size: 13px;
    }}
    QLineEdit:focus {{
        border-color: {T.ACCENT_BLUE};
    }}
"""

_CRED_LABEL_W = 96


class _CredentialRow(QWidget):
    """Username + password inputs for a single firmware type."""

    changed = pyqtSignal()

    def __init__(self, key: str, label: str, username: str,
                 has_saved_password: bool, parent=None):
        super().__init__(parent)
        self.key = key
        # None until the user edits the field, so an untouched blank password
        # never wipes the one already saved in config.
        self._password_touched = False
        self.setStyleSheet("background: transparent;")

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        row.addWidget(_lbl(label, 12, 600, T.TEXT_PRIMARY, fixed_w=_CRED_LABEL_W))

        self._username_input = QLineEdit()
        self._username_input.setPlaceholderText("root")
        self._username_input.setFont(make_font(12, 400))
        self._username_input.setFixedHeight(34)
        self._username_input.setStyleSheet(_CRED_INPUT_STYLE)
        self._username_input.setText(username)
        self._username_input.textChanged.connect(self.changed)
        row.addWidget(self._username_input, 1)

        self._password_input = QLineEdit()
        self._password_input.setPlaceholderText(
            "(saved — type to change)" if has_saved_password else "Leave blank if none"
        )
        self._password_input.setFont(make_font(12, 400))
        self._password_input.setFixedHeight(34)
        self._password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._password_input.setStyleSheet(_CRED_INPUT_STYLE)
        self._password_input.textChanged.connect(self._on_password_edited)
        row.addWidget(self._password_input, 1)

        self._pw_toggle_btn = QPushButton("Show")
        self._pw_toggle_btn.setFixedHeight(34)
        self._pw_toggle_btn.setFixedWidth(52)
        self._pw_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pw_toggle_btn.setCheckable(True)
        self._pw_toggle_btn.setFont(make_font(11, 600))
        self._pw_toggle_btn.setStyleSheet(f"""
            QPushButton {{
                background: {T.BG_WINDOW};
                border: 1px solid {T.BORDER_DEFAULT};
                border-radius: 6px;
                color: {T.TEXT_MUTED};
            }}
            QPushButton:checked {{
                color: {T.ACCENT_BLUE};
                border-color: {T.ACCENT_BLUE};
            }}
            QPushButton:hover {{
                background: {T.BG_CARD};
            }}
        """)
        self._pw_toggle_btn.toggled.connect(self._on_pw_visibility_toggled)
        row.addWidget(self._pw_toggle_btn)

    def _on_password_edited(self) -> None:
        self._password_touched = True
        self.changed.emit()

    def _on_pw_visibility_toggled(self, visible: bool) -> None:
        mode = QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        self._password_input.setEchoMode(mode)
        self._pw_toggle_btn.setText("Hide" if visible else "Show")

    def values(self) -> dict[str, Optional[str]]:
        """Current entry: ``password`` is None when the field was never edited."""
        return {
            "username": self._username_input.text().strip(),
            # do not strip the password — it can legitimately contain spaces
            "password": self._password_input.text() if self._password_touched else None,
        }


# ── Combined progress + subnet entry card ─────────────────────────────────────

class _ProgressEntryCard(QWidget):
    """Scan progress bar (top) and subnet entry form (bottom) in one card."""

    subnets_queued = pyqtSignal(list)   # emitted from _on_add with the entered CIDRs

    FIRMWARE_OPTIONS = [
        ("braiins", "Braiins OS"),
        ("luxos",   "LuxOS"),
        ("vnish",   "Vnish"),
        ("bitmain", "Bitmain"),
        ("sealminer", "Sealminer"),
    ]

    def __init__(self, engine: Optional["ScanningEngine"], parent=None):
        super().__init__(parent)
        self._engine = engine
        self._scanning = False
        self._fw_toggles: dict[str, _FirmwareToggle] = {}
        self._cred_rows: dict[str, _CredentialRow] = {}

        # Debounce credential saves — wait 500ms after the last keystroke
        self._cred_debounce = QTimer()
        self._cred_debounce.setSingleShot(True)
        self._cred_debounce.setInterval(500)
        self._cred_debounce.timeout.connect(self._flush_credentials)

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("pe_card")
        self.setStyleSheet(f"""
            QWidget#pe_card {{
                background: {T.BG_CARD};
                border: 1px solid {T.BORDER_DEFAULT};
                border-radius: 8px;
            }}
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Scan progress section ─────────────────────────────────────────────
        progress_widget = QWidget()
        progress_widget.setStyleSheet("background: transparent;")
        prog = QVBoxLayout(progress_widget)
        prog.setContentsMargins(22, 18, 22, 18)
        prog.setSpacing(10)

        hdr = QHBoxLayout()
        self._prog_title = _lbl("SCAN QUEUE", 11, 700, T.TEXT_MUTED)
        hdr.addWidget(self._prog_title)
        hdr.addStretch()
        self._pct_lbl = _lbl("—", 12, 700, T.TEXT_PRIMARY)
        hdr.addWidget(self._pct_lbl)
        hdr.addSpacing(12)

        self._action_btn = QPushButton("▷  START SCAN")
        self._action_btn.setFont(make_font(11, 600))
        self._action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._action_btn.setFixedHeight(32)
        self._action_btn.clicked.connect(self._on_action)
        self._style_start()
        hdr.addWidget(self._action_btn)
        prog.addLayout(hdr)

        self._bar = QProgressBar()
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(6)
        self._bar.setStyleSheet(f"""
            QProgressBar {{
                background: {T.BORDER_DEFAULT};
                border-radius: 3px;
                border: none;
            }}
            QProgressBar::chunk {{
                background: {T.TEXT_PRIMARY};
                border-radius: 3px;
            }}
        """)
        prog.addWidget(self._bar)

        ftr = QHBoxLayout()
        self._status_lbl = _lbl("No scan running", 12, 400, T.TEXT_SECONDARY)
        ftr.addWidget(self._status_lbl)
        ftr.addStretch()
        self._next_scan_lbl = _lbl("", 11, 400, T.TEXT_MUTED)
        ftr.addWidget(self._next_scan_lbl)
        ftr.addSpacing(12)
        self._counts_lbl = _lbl("", 12, 400, T.TEXT_MUTED)
        ftr.addWidget(self._counts_lbl)
        prog.addLayout(ftr)

        # 1-second ticker to keep the countdown fresh
        self._countdown_timer = QTimer()
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._refresh_countdown)
        self._countdown_timer.start()

        outer.addWidget(progress_widget)
        outer.addWidget(_hdiv())

        # ── Subnet entry section ──────────────────────────────────────────────
        entry_widget = QWidget()
        entry_widget.setStyleSheet("background: transparent;")
        entry = QVBoxLayout(entry_widget)
        entry.setContentsMargins(22, 18, 22, 18)
        entry.setSpacing(14)

        entry.addWidget(_lbl("ADD SUBNET", 11, 700, T.TEXT_MUTED))

        # Firmware toggles in 2-col grid
        entry.addWidget(_lbl("FIRMWARE TYPES", 10, 600, T.TEXT_MUTED))

        active_types: list[str] = []
        if engine is not None:
            active_types = engine._cfg.get("collector_types") or []

        # Firmware toggles (left) + warning card (right) side by side
        fw_and_warn = QHBoxLayout()
        fw_and_warn.setSpacing(20)
        fw_and_warn.setAlignment(Qt.AlignmentFlag.AlignTop)

        fw_row_widget = QWidget()
        fw_row_widget.setStyleSheet("background: transparent;")
        fw_row_widget.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred
        )
        fw_row = QHBoxLayout(fw_row_widget)
        fw_row.setContentsMargins(0, 0, 0, 0)
        fw_row.setSpacing(28)
        for key, label in self.FIRMWARE_OPTIONS:
            checked = key in active_types or not active_types
            toggle = _FirmwareToggle(key, label, checked)
            toggle.toggle.toggled.connect(self._on_firmware_changed)
            self._fw_toggles[key] = toggle
            fw_row.addWidget(toggle)

        fw_and_warn.addWidget(fw_row_widget)
        fw_and_warn.addStretch()

        entry.addLayout(fw_and_warn)

        # ── Credentials — one row per firmware ─────────────────────────────
        entry.addSpacing(4)
        entry.addWidget(_lbl("MINER CREDENTIALS", 10, 600, T.TEXT_MUTED))
        entry.addWidget(_lbl(
            "Credentials used when connecting to discovered miners, per firmware. "
            "Only enabled firmware types are shown.",
            11, 400, T.TEXT_MUTED, wrap=True,
        ))

        # Column headers, aligned with the inputs below
        cred_hdr = QHBoxLayout()
        cred_hdr.setSpacing(12)
        cred_hdr.addWidget(_lbl("FIRMWARE", 10, 600, T.TEXT_MUTED, fixed_w=_CRED_LABEL_W))
        cred_hdr.addWidget(_lbl("USERNAME", 10, 600, T.TEXT_MUTED), 1)
        cred_hdr.addWidget(_lbl("PASSWORD", 10, 600, T.TEXT_MUTED), 1)
        cred_hdr.addSpacing(52 + 12)   # show/hide button + row spacing
        entry.addLayout(cred_hdr)

        from wright_telemetry.config import firmware_credentials_map
        saved_creds = firmware_credentials_map(
            engine._cfg if engine else {}, [k for k, _ in self.FIRMWARE_OPTIONS],
        )
        for key, label in self.FIRMWARE_OPTIONS:
            saved = saved_creds[key]
            row = _CredentialRow(
                key, label,
                username=saved["username"],
                has_saved_password=bool(saved["password_b64"]),
            )
            row.changed.connect(self._on_credentials_changed)
            self._cred_rows[key] = row
            entry.addWidget(row)

        self._sync_credential_row_visibility()

        # ── CIDR input — multi-value, comma-separated ──────────────────────
        entry.addSpacing(4)
        entry.addWidget(_lbl("CIDR RANGE", 10, 600, T.TEXT_MUTED))
        self._cidr_input = QTextEdit()
        self._cidr_input.setPlaceholderText("192.168.1.0/24, 10.0.1.0/24")
        self._cidr_input.setFont(make_font(12, 400))
        self._cidr_input.setFixedHeight(58)
        self._cidr_input.setStyleSheet(f"""
            QTextEdit {{
                background: {T.BG_WINDOW};
                border: 1px solid {T.BORDER_DEFAULT};
                border-radius: 6px;
                padding: 6px 10px;
                color: {T.TEXT_PRIMARY};
            }}
            QTextEdit:focus {{
                border-color: {T.ACCENT_BLUE};
            }}
        """)
        entry.addWidget(self._cidr_input)

        add_btn = PrimaryButton("ADD SUBNETS")
        add_btn.setFont(make_font(13, 700))
        add_btn.setFixedHeight(44)
        add_btn.clicked.connect(self._on_add)
        entry.addWidget(add_btn)

        # Format guidance
        entry.addSpacing(6)
        fmt_title = QHBoxLayout()
        fmt_title.setSpacing(6)
        fmt_title.addWidget(_lbl("ⓘ", 12, 400, T.TEXT_MUTED))
        fmt_title.addWidget(_lbl("Accepted formats", 12, 600, T.TEXT_SECONDARY))
        fmt_title.addStretch()
        entry.addLayout(fmt_title)

        _fmt_style = f"color: {T.TEXT_MUTED}; background: transparent; font-family: monospace;"

        _FORMATS = [
            ("192.168.1.0/24",          "CIDR — all hosts in the range"),
            ("10.0.0.1-10.0.0.100",     "IP range — start address to end address"),
            ("192.168.1.50",            "Single host"),
        ]
        for fmt, desc in _FORMATS:
            row = QHBoxLayout()
            row.setContentsMargins(18, 0, 0, 0)
            row.setSpacing(10)
            fmt_lbl = QLabel(fmt)
            fmt_lbl.setFont(make_font(11, 500))
            fmt_lbl.setStyleSheet(_fmt_style)
            fmt_lbl.setFixedWidth(180)
            row.addWidget(fmt_lbl)
            row.addWidget(_lbl(desc, 11, 400, T.TEXT_MUTED))
            row.addStretch()
            entry.addLayout(row)

        entry.addSpacing(4)
        entry.addWidget(_lbl(
            "Separate multiple entries with commas or new lines.",
            11, 400, T.TEXT_MUTED,
        ))

        outer.addWidget(entry_widget)

    def sync_firmware_toggles(self, engine: "ScanningEngine") -> None:
        """Update toggle states to match the engine's collector_types config."""
        active_types: list[str] = engine._cfg.get("collector_types") or []
        for key, toggle in self._fw_toggles.items():
            # Block the toggled signal while we programmatically update state
            # to avoid triggering _on_firmware_changed and a spurious rescan.
            toggle.toggle.blockSignals(True)
            toggle.toggle.setChecked(key in active_types or not active_types)
            toggle.toggle.blockSignals(False)
        self._sync_credential_row_visibility()

    # ── Progress slots ────────────────────────────────────────────────────────

    def set_scanning(self, subnet: str, completed_before: int,
                     total_subnets: int) -> None:
        """Called when a subnet starts scanning. Bar reflects queue-level progress."""
        self._scanning = True
        self._prog_title.setText("SCANNING")
        self._status_lbl.setText(f"Scanning {subnet}")
        self._counts_lbl.setText("")
        self._bar.setMaximum(max(total_subnets, 1))
        self._bar.setValue(completed_before)
        self._pct_lbl.setText(f"{completed_before} / {total_subnets}")
        self._next_scan_lbl.setVisible(False)
        self._style_cancel()

    def update_progress(self, subnet: str, scanned: int, total: int) -> None:
        """Update per-host progress subtitle; bar is queue-level only."""
        self._counts_lbl.setText(f"{scanned} / {total} hosts")

    def set_idle(self, completed: int = 0, total_subnets: int = 0,
                 last_scan_ts: Optional[float] = None) -> None:
        """Called when the queue is empty or no scan is running."""
        self._scanning = False
        self._prog_title.setText("SCAN QUEUE")
        self._counts_lbl.setText("")
        self._bar.setMaximum(max(total_subnets, 1))
        self._bar.setValue(completed)
        if total_subnets > 0:
            self._pct_lbl.setText(f"{completed} / {total_subnets}")
            if completed == total_subnets and last_scan_ts is not None:
                self._status_lbl.setText(f"Last scan: {_time_ago(last_scan_ts)}")
            elif completed == total_subnets:
                self._status_lbl.setText("Ready to scan")
            else:
                self._status_lbl.setText(f"{completed} / {total_subnets} subnets complete")
        else:
            self._pct_lbl.setText("—")
            self._status_lbl.setText("No subnets configured")
        self._next_scan_lbl.setVisible(True)
        self._refresh_countdown()
        self._style_start()

    def set_cancelled(self, subnet: str, completed: int = 0,
                      total_subnets: int = 0) -> None:
        self._scanning = False
        self._counts_lbl.setText("")
        self._bar.setMaximum(max(total_subnets, 1))
        self._bar.setValue(completed)
        if total_subnets:
            self._pct_lbl.setText(f"{completed} / {total_subnets}")
        self._status_lbl.setText(f"Cancelled: {subnet}")
        self._next_scan_lbl.setVisible(True)
        self._refresh_countdown()
        self._style_start()

    def _refresh_countdown(self) -> None:
        """Update the 'Next auto scan in X:XX' label from the engine timer."""
        if self._scanning or self._engine is None:
            self._next_scan_lbl.setVisible(False)
            return
        secs = self._engine.seconds_until_next_discovery()
        if secs <= 0:
            self._next_scan_lbl.setText("")
            self._next_scan_lbl.setVisible(False)
            return
        mins, s = divmod(secs, 60)
        self._next_scan_lbl.setText(f"Next auto scan in {mins}:{s:02d}")
        self._next_scan_lbl.setVisible(True)

    # ── Button styles ─────────────────────────────────────────────────────────

    def _style_start(self) -> None:
        self._action_btn.setText("▷  START SCAN")
        self._action_btn.setStyleSheet(f"""
            QPushButton {{
                background: {T.TEXT_PRIMARY};
                color: #FFFFFF;
                border: none;
                border-radius: 6px;
                padding: 0 16px;
            }}
            QPushButton:hover {{ background: #2A2D35; }}
        """)

    def _style_cancel(self) -> None:
        self._action_btn.setText("⊗  CANCEL")
        self._action_btn.setStyleSheet(f"""
            QPushButton {{
                background: {T.ACCENT_RED};
                color: #FFFFFF;
                border: none;
                border-radius: 6px;
                padding: 0 16px;
            }}
            QPushButton:hover {{ background: #DC2626; }}
        """)

    # ── Firmware / add handlers ───────────────────────────────────────────────

    def _on_firmware_changed(self) -> None:
        self._sync_credential_row_visibility()
        if self._engine is None:
            return
        selected = [k for k, t in self._fw_toggles.items() if t.isChecked()]
        self._engine.update_firmware_types(selected)

    def _sync_credential_row_visibility(self) -> None:
        """Show a credential row only for firmware types that are enabled.

        With no types enabled every firmware is scanned, so show them all.
        """
        any_checked = any(t.isChecked() for t in self._fw_toggles.values())
        for key, row in self._cred_rows.items():
            toggle = self._fw_toggles.get(key)
            row.setVisible(not any_checked or (toggle is not None and toggle.isChecked()))

    def _on_credentials_changed(self) -> None:
        """Restart the debounce timer on every keystroke."""
        self._cred_debounce.start()

    def _flush_credentials(self) -> None:
        """Persist per-firmware credentials to config via the engine."""
        if self._engine is None:
            return
        self._engine.update_firmware_credentials(
            {key: row.values() for key, row in self._cred_rows.items()}
        )

    def _on_add(self) -> None:
        if self._engine is None:
            return
        # Flush any pending credential changes immediately before scanning
        self._cred_debounce.stop()
        self._flush_credentials()
        raw = self._cidr_input.toPlainText().strip()
        if not raw:
            return
        cidrs = [c.strip() for c in raw.replace("\n", ",").split(",") if c.strip()]
        self._engine.enqueue_subnets(cidrs)
        self._cidr_input.clear()
        # Notify immediately so the table updates without waiting for the drain timer
        self.subnets_queued.emit(cidrs)

    def _on_action(self) -> None:
        if self._engine is None:
            return
        if self._scanning:
            self._engine.cancel_scan()
        else:
            self._engine.start_scan()


# ── Scan row ──────────────────────────────────────────────────────────────────

class _ScanRow(QWidget):
    """One live-updatable row. Column widths match _ActiveScansCard header."""

    _STATUS_CFG = {
        "queued":    ("⊙  Queued",    T.ACCENT_ORANGE),
        "scanning":  ("↻  Scanning",  T.ACCENT_GREEN),
        "complete":  ("✓  Complete",  T.TEXT_MUTED),
        "cancelled": ("⊗  Cancelled", T.ACCENT_RED),
    }

    def __init__(self, result: SubnetScanResult,
                 engine: Optional["ScanningEngine"], parent=None):
        super().__init__(parent)
        self._engine = engine
        self.subnet = result.subnet
        self._status: str = result.status  # explicit state, never derive from label text
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("background: transparent;")
        self.setFixedHeight(40)

        row = QHBoxLayout(self)
        row.setContentsMargins(20, 0, 20, 0)
        row.setSpacing(0)

        self._status_lbl = _lbl("", 12, 500, T.TEXT_MUTED, fixed_w=_W_STATUS)
        row.addWidget(self._status_lbl)

        self._cidr_lbl = QLabel()
        self._cidr_lbl.setFont(make_font(12, 400))
        self._cidr_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._cidr_lbl.setStyleSheet("background: transparent;")
        self._cidr_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        row.addWidget(self._cidr_lbl, 1)

        row.addSpacing(_W_GAP)

        self._miners_lbl = _lbl("—", 12, 400, T.TEXT_SECONDARY, fixed_w=_W_MINERS)
        self._miners_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        row.addWidget(self._miners_lbl)

        row.addSpacing(_W_GAP)

        self._fw_lbl = _lbl("—", 11, 400, T.TEXT_MUTED, fixed_w=_W_FW)
        row.addWidget(self._fw_lbl)

        row.addSpacing(_W_GAP)

        self._time_lbl = _lbl("—", 11, 400, T.TEXT_MUTED, fixed_w=_W_CHECKED)
        self._time_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        row.addWidget(self._time_lbl)

        row.addSpacing(_W_GAP)

        btn_style = f"""
            QPushButton {{
                background: transparent;
                color: {T.TEXT_MUTED};
                border: none;
                border-radius: 4px;
            }}
            QPushButton:hover {{
                background: {T.BG_SIDEBAR};
                color: {T.TEXT_PRIMARY};
            }}
        """
        del_hover = f"""
            QPushButton {{
                background: transparent;
                color: {T.TEXT_MUTED};
                border: none;
                border-radius: 4px;
            }}
            QPushButton:hover {{
                background: #FEE2E2;
                color: {T.ACCENT_RED};
            }}
        """

        self._btn = QPushButton("↺")
        self._btn.setFixedSize(30, 30)
        self._btn.setFont(make_font(13, 400))
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn.setStyleSheet(btn_style)
        self._btn.clicked.connect(self._on_action)
        row.addWidget(self._btn)

        row.addSpacing(4)

        self._delete_btn = QPushButton("🗑")
        self._delete_btn.setFixedSize(30, 30)
        self._delete_btn.setFont(make_font(13, 400))
        self._delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._delete_btn.setStyleSheet(del_hover)
        self._delete_btn.setToolTip("Remove subnet")
        self._delete_btn.clicked.connect(self._on_delete)
        row.addWidget(self._delete_btn)

        self.update(result)

    @property
    def status(self) -> str:
        return self._status

    def update(self, result: SubnetScanResult) -> None:  # type: ignore[override]
        self._status = result.status
        label, color = self._STATUS_CFG.get(result.status, ("—", T.TEXT_MUTED))
        self._status_lbl.setText(label)
        self._status_lbl.setStyleSheet(
            f"color: {color}; background: transparent;"
        )

        cidr_color = T.TEXT_PRIMARY if result.status in ("queued", "scanning") else T.TEXT_MUTED
        local_tag = (
            f' <span style="font-size:10px; color:{T.TEXT_MUTED}; '
            f'background:#F3F4F6; border-radius:3px; padding:1px 5px;">local</span>'
            if result.local else ""
        )
        self._cidr_lbl.setText(
            f'<span style="color:{cidr_color};">{result.subnet}</span>{local_tag}'
        )

        if result.status == "queued":
            self._miners_lbl.setText("—")
            self._fw_lbl.setText("—")
            self._btn.setText("—")
            self._btn.setEnabled(False)
        elif result.status == "scanning":
            pct = (
                int(result.scanned_hosts / result.total_hosts * 100)
                if result.total_hosts else 0
            )
            self._miners_lbl.setText(f"{pct}%")
            self._fw_lbl.setText("scanning…")
            self._btn.setText("⊗")
            self._btn.setEnabled(True)
            self._btn.setToolTip("Cancel scan")
        elif result.status == "cancelled":
            self._miners_lbl.setText("—")
            self._fw_lbl.setText("—")
            self._btn.setText("↺")
            self._btn.setEnabled(True)
            self._btn.setToolTip("Retry scan")
        else:  # complete
            self._miners_lbl.setText(str(result.miners_found))
            self._fw_lbl.setText(_fw_summary(result.firmware_breakdown))
            self._btn.setText("↺")
            self._btn.setEnabled(True)
            self._btn.setToolTip("Rescan subnet")

        self._time_lbl.setText(_time_ago(result.last_scanned))

    def _on_action(self) -> None:
        if self._engine is None:
            return
        if self._status == "scanning":
            self._engine.cancel_scan()
        else:
            self._engine.scan_manager.enqueue([self.subnet])

    def _on_delete(self) -> None:
        if self._engine is not None:
            self._engine.remove_subnet(self.subnet)


# ── Active scans card ─────────────────────────────────────────────────────────

class _ActiveScansCard(QWidget):
    def __init__(self, engine: Optional["ScanningEngine"], parent=None):
        super().__init__(parent)
        self._engine = engine
        self._rows: dict[str, _ScanRow] = {}
        self._dividers: dict[str, QFrame] = {}

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

        # ── Card header ───────────────────────────────────────────────────────
        hdr_w = QWidget()
        hdr_w.setStyleSheet("background: transparent;")
        hdr = QHBoxLayout(hdr_w)
        hdr.setContentsMargins(20, 16, 20, 12)
        hdr.addWidget(_lbl("Active Network Scans", 14, 700, T.TEXT_PRIMARY))
        hdr.addStretch()

        self._badge_count = QLabel("0 ACTIVE")
        self._badge_count.setFont(make_font(11, 700))
        self._badge_count.setStyleSheet(
            f"color: {T.ACCENT_GREEN}; background: transparent;"
        )
        badge_wrap = QWidget()
        badge_wrap.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        badge_wrap.setObjectName("badge")
        badge_wrap.setStyleSheet(
            "QWidget#badge { background: #DCFCE7; border-radius: 10px; }"
        )
        bw = QHBoxLayout(badge_wrap)
        bw.setContentsMargins(10, 4, 10, 4)
        bw.setSpacing(5)
        bw.addWidget(_lbl("●", 8, 400, T.ACCENT_GREEN))
        bw.addWidget(self._badge_count)
        hdr.addWidget(badge_wrap)
        self._outer.addWidget(hdr_w)
        self._outer.addWidget(_hdiv())

        # ── Column headers (widths match _ScanRow exactly) ────────────────────
        col_hdr_w = QWidget()
        col_hdr_w.setStyleSheet("background: transparent;")
        ch = QHBoxLayout(col_hdr_w)
        ch.setContentsMargins(20, 7, 20, 7)
        ch.setSpacing(0)

        def _ch_lbl(text: str, w: int = 0, stretch: int = 0,
                    right: bool = False) -> QLabel:
            l = _lbl(text, 11, 600, T.TEXT_MUTED)
            if w:
                l.setFixedWidth(w)
            if right:
                l.setAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
            return l

        ch.addWidget(_ch_lbl("STATUS",          _W_STATUS))
        ch.addWidget(_ch_lbl("SUBNET CIDR",      0, stretch=1))
        ch.addSpacing(_W_GAP)
        ch.addWidget(_ch_lbl("MINERS",           _W_MINERS, right=True))
        ch.addSpacing(_W_GAP)
        ch.addWidget(_ch_lbl("FIRMWARES FOUND",  _W_FW))
        ch.addSpacing(_W_GAP)
        ch.addWidget(_ch_lbl("LAST CHECKED",     _W_CHECKED, right=True))
        ch.addSpacing(_W_GAP)
        ch.addWidget(_ch_lbl("",                 _W_ACTIONS))  # two action buttons

        self._outer.addWidget(col_hdr_w)
        self._outer.addWidget(_hdiv())

        # ── Scrollable rows ───────────────────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setStyleSheet("background: transparent;")
        self._scroll.setMinimumHeight(320)

        self._rows_widget = QWidget()
        self._rows_widget.setStyleSheet("background: transparent;")
        self._rows_layout = QVBoxLayout(self._rows_widget)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(0)
        self._rows_layout.addStretch()

        self._scroll.setWidget(self._rows_widget)
        self._outer.addWidget(self._scroll, 1)

    def add_or_update_row(self, result: SubnetScanResult) -> None:
        if result.subnet in self._rows:
            self._rows[result.subnet].update(result)
        else:
            # Divider goes *before* the new row, skipped for the very first entry
            # so there is never a trailing separator after the last row.
            is_first = len(self._rows) == 0
            row = _ScanRow(result, self._engine)
            div = None if is_first else _hdiv()
            self._rows[result.subnet] = row
            self._dividers[result.subnet] = div
            idx = self._rows_layout.count() - 1   # before stretch
            if div is not None:
                self._rows_layout.insertWidget(idx, div)
                self._rows_layout.insertWidget(idx + 1, row)
            else:
                self._rows_layout.insertWidget(idx, row)
        self._refresh_badge()

    def remove_row(self, subnet: str) -> None:
        is_first = next(iter(self._rows), None) == subnet
        row = self._rows.pop(subnet, None)
        div = self._dividers.pop(subnet, None)
        if row:
            row.setVisible(False)
            self._rows_layout.removeWidget(row)
            row.deleteLater()
        if div:
            div.setVisible(False)
            self._rows_layout.removeWidget(div)
            div.deleteLater()
        # When the first row (the one without a leading divider) is removed, the
        # new first row now has an orphaned leading divider — remove it too.
        if is_first and self._rows:
            new_first = next(iter(self._rows))
            leading_div = self._dividers.get(new_first)
            if leading_div is not None:
                leading_div.setVisible(False)
                self._rows_layout.removeWidget(leading_div)
                leading_div.deleteLater()
                self._dividers[new_first] = None
        self._refresh_badge()

    def row_count(self) -> int:
        return len(self._rows)

    def get_row(self, subnet: str) -> "_ScanRow | None":
        return self._rows.get(subnet)

    def _refresh_badge(self) -> None:
        active = sum(1 for r in self._rows.values() if r.status == "scanning")
        self._badge_count.setText(f"{active} ACTIVE")


# ── Page ──────────────────────────────────────────────────────────────────────

class DiscoveryPage(QWidget):
    """Discovery page with live subnet scan queue."""

    next_clicked = pyqtSignal()

    def __init__(self, engine: Optional["ScanningEngine"] = None, parent=None):
        super().__init__(parent)
        self._engine = engine
        self._scan_completed = False
        self._total_miners = 0
        self._last_scan_ts: Optional[float] = None
        self._had_cancel = False
        self._failed_scan_count = 0  # warning shown only after 2 empty scans

        self.setStyleSheet(f"background: {T.BG_WINDOW};")

        self._content = QWidget()
        self._content.setStyleSheet(f"background: {T.BG_WINDOW};")
        layout = QVBoxLayout(self._content)
        layout.setContentsMargins(T.CONTENT_PADDING, T.CONTENT_PADDING,
                                  T.CONTENT_PADDING, T.CONTENT_PADDING)
        layout.setSpacing(20)

        # ── Heading ───────────────────────────────────────────────────────────
        heading = QLabel("Discover Miners")
        heading.setFont(make_font(*T.FONT_PAGE_HEADING))
        heading.setStyleSheet(f"color: {T.TEXT_PRIMARY};")
        layout.addWidget(heading)

        layout.addSpacing(8)

        desc = QLabel(
            "Every facility is wired differently. Tell the agent which subnets "
            "your miners are on so it knows where to look."
        )
        desc.setFont(make_font(*T.FONT_PAGE_DESC))
        desc.setStyleSheet(f"color: {T.TEXT_SECONDARY};")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        layout.addSpacing(20)

        status_row = QHBoxLayout()
        status_row.setSpacing(6)
        self._status_dot = _lbl("●", 10, 400, T.ACCENT_ORANGE)
        self._status_lbl = _lbl("Scanning local network…", 13, 400, T.TEXT_SECONDARY)
        status_row.addWidget(self._status_dot)
        status_row.addWidget(self._status_lbl)
        status_row.addStretch()
        layout.addLayout(status_row)

        # ── Warning card (above scan progress, hidden until 0-miner scan) ──────
        self._warning = _WarningCard()
        self._warning.setVisible(False)
        layout.addWidget(self._warning)

        # ── Combined progress + subnet entry card ─────────────────────────────
        self._progress_entry = _ProgressEntryCard(engine)
        self._progress_entry.subnets_queued.connect(self._on_user_subnets_queued)
        layout.addWidget(self._progress_entry)

        # ── Active scans table ────────────────────────────────────────────────
        self._scans_card = _ActiveScansCard(engine)
        layout.addWidget(self._scans_card, 1)

        self._page_scroll = QScrollArea()
        self._page_scroll.setWidgetResizable(True)
        self._page_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._page_scroll.setStyleSheet("background: transparent;")
        self._page_scroll.setWidget(self._content)
        self._page_outer = QVBoxLayout(self)
        self._page_outer.setContentsMargins(0, 0, 0, 0)
        self._page_outer.addWidget(self._page_scroll)

        # ── Populate from existing scan state ─────────────────────────────────
        if engine is not None:
            all_results = engine.scan_manager.get_all_results()
            for result in all_results:
                self._scans_card.add_or_update_row(result)
                if result.status == "complete":
                    self._scan_completed = True
                    if result.last_scanned and (
                        self._last_scan_ts is None
                        or result.last_scanned > self._last_scan_ts
                    ):
                        self._last_scan_ts = result.last_scanned

            # Restore warning state from existing scan results
            any_complete = any(r.status == "complete" for r in all_results)
            total_miners = engine.scan_manager.total_miners()
            self._warning.setVisible(any_complete and total_miners == 0)

            # Initialise the queue progress bar
            _total = len(all_results)
            _completed = sum(1 for r in all_results if r.status == "complete")
            if engine.scan_manager.is_scanning():
                self._set_status_scanning()
            else:
                self._progress_entry.set_idle(_completed, _total, self._last_scan_ts)
                self._set_status_idle()

            self._connect_signals(engine)

        # ── Timestamp refresh every 30s ───────────────────────────────────────
        self._ts_timer = QTimer(self)
        self._ts_timer.setInterval(30_000)
        self._ts_timer.timeout.connect(self._refresh_timestamps)
        self._ts_timer.start()

    # ── Signal wiring ────────────────────────────────────────────────────────

    def wire_engine(self, engine: "ScanningEngine") -> None:
        """Attach a freshly created engine after provisioning."""
        self._engine = engine
        self._progress_entry._engine = engine
        self._progress_entry.sync_firmware_toggles(engine)
        self._progress_entry._refresh_countdown()
        self._scans_card._engine = engine
        self._connect_signals(engine)

    def _connect_signals(self, engine: "ScanningEngine") -> None:
        engine.signals.scan_queued.connect(self._on_scan_queued)
        engine.signals.scan_started.connect(self._on_scan_started)
        engine.signals.scan_progress.connect(self._on_scan_progress)
        engine.signals.scan_complete.connect(self._on_scan_complete)
        engine.signals.scan_cancelled.connect(self._on_scan_cancelled)
        engine.signals.scan_queue_empty.connect(self._on_queue_empty)
        engine.signals.discovery_total_changed.connect(self._on_total_changed)
        engine.signals.subnet_removed.connect(self._scans_card.remove_row)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _refresh_queue_progress(self) -> None:
        """Recompute queue stats and update the top progress bar."""
        if self._engine is None:
            return
        results = self._engine.scan_manager.get_all_results()
        total = len(results)
        completed = sum(1 for r in results if r.status == "complete")
        scanning = next((r for r in results if r.status == "scanning"), None)
        if scanning:
            self._progress_entry.set_scanning(scanning.subnet, completed, total)
        else:
            self._progress_entry.set_idle(completed, total, self._last_scan_ts)

    def _on_scan_queued(self, subnet: str) -> None:
        is_local = False
        if self._engine is not None:
            for r in self._engine.scan_manager.get_all_results():
                if r.subnet == subnet:
                    is_local = r.local
                    break
        self._scans_card.add_or_update_row(
            SubnetScanResult(subnet=subnet, status="queued", local=is_local)
        )
        self._refresh_queue_progress()
        self._set_status_scanning()

    def _on_scan_started(self, subnet: str, total: int) -> None:
        self._scans_card.add_or_update_row(
            SubnetScanResult(subnet=subnet, status="scanning", total_hosts=total)
        )
        self._refresh_queue_progress()
        self._set_status_scanning()

    def _on_scan_progress(self, subnet: str, scanned: int, total: int) -> None:
        self._progress_entry.update_progress(subnet, scanned, total)
        self._scans_card.add_or_update_row(SubnetScanResult(
            subnet=subnet, status="scanning",
            total_hosts=total, scanned_hosts=scanned,
        ))

    def _on_scan_complete(self, subnet: str, miners_found: int,
                          firmware_breakdown: object) -> None:
        self._scan_completed = True
        now = time.time()
        self._last_scan_ts = now
        self._scans_card.add_or_update_row(SubnetScanResult(
            subnet=subnet, status="complete",
            miners_found=miners_found,
            firmware_breakdown=firmware_breakdown,  # type: ignore[arg-type]
            last_scanned=now,
        ))
        self._refresh_queue_progress()

    def _on_scan_cancelled(self, subnet: str) -> None:
        self._had_cancel = True
        self._scans_card.add_or_update_row(
            SubnetScanResult(subnet=subnet, status="cancelled")
        )
        if self._engine:
            results = self._engine.scan_manager.get_all_results()
            total = len(results)
            completed = sum(1 for r in results if r.status == "complete")
            self._progress_entry.set_cancelled(subnet, completed, total)
        else:
            self._progress_entry.set_cancelled(subnet)

    def _on_queue_empty(self) -> None:
        if self._engine:
            results = self._engine.scan_manager.get_all_results()
            total = len(results)
            completed = sum(1 for r in results if r.status == "complete")
            self._progress_entry.set_idle(completed, total, self._last_scan_ts)
        else:
            self._progress_entry.set_idle(0, 0, self._last_scan_ts)
        self._set_status_idle()
        if self._scan_completed and self._total_miners == 0 and not self._had_cancel:
            self._failed_scan_count += 1
        show = self._failed_scan_count >= 2
        self._warning.setVisible(show)
        self._had_cancel = False

    def _on_total_changed(self, total: int) -> None:
        self._total_miners = total
        if total > 0:
            self._failed_scan_count = 0
        self._warning.setVisible(False)
        if total > 0:
            n = self._scans_card.row_count()
            s = "s" if total != 1 else ""
            self._status_lbl.setText(
                f"{total} miner{s} found across {n} subnet{'s' if n != 1 else ''}"
            )
            self._status_dot.setStyleSheet(
                f"color: {T.ACCENT_GREEN}; background: transparent;"
            )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _set_status_scanning(self) -> None:
        self._status_dot.setStyleSheet(
            f"color: {T.ACCENT_ORANGE}; background: transparent;"
        )
        if self._total_miners == 0:
            self._status_lbl.setText("Scan in progress…")

    def _set_status_idle(self) -> None:
        if self._total_miners > 0:
            return   # keep the "N miners found" text
        color = T.TEXT_MUTED if not self._scan_completed else T.TEXT_SECONDARY
        self._status_dot.setStyleSheet(f"color: {color}; background: transparent;")
        self._status_lbl.setText(
            "Scan complete — no miners found"
            if self._scan_completed else "No active scan"
        )

    def _on_user_subnets_queued(self, cidrs: list) -> None:
        """Immediately add rows for user-submitted subnets without waiting for the drain timer."""
        for s in cidrs:
            if s not in self._scans_card._rows:
                self._scans_card.add_or_update_row(SubnetScanResult(subnet=s, status="queued"))
        self._set_status_scanning()
        self._refresh_queue_progress()

    def _refresh_timestamps(self) -> None:
        if self._engine is None:
            return
        for result in self._engine.scan_manager.get_all_results():
            row = self._scans_card.get_row(result.subnet)
            if row is not None:
                row.update(result)
