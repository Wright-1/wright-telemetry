"""Page widgets for each wizard step.

Each page is a QWidget that can be swapped into the main content area.
All pages are static for now — no backend connections.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from wright_telemetry.gui.fonts import make_font
from wright_telemetry.gui import theme as T
from wright_telemetry.gui.widgets import PermissionRow, PrimaryButton, SecondaryButton

if TYPE_CHECKING:
    from wright_telemetry.gui.engine import ScanningEngine

# Permission data — mirrors consent.py METRICS
PERMISSIONS = [
    {
        "key": "cooling",
        "icon": T.CATEGORY_ICONS["cooling"],
        "title": "Temperature & Fan RPM",
        "subtitle": "Reads sensors to predict lifespan.",
        "detail": (
            "Detailed access required for monitoring temperature & fan RPM. "
            "This permission allows the local agent to query specific endpoints "
            "on your mining hardware."
        ),
    },
    {
        "key": "hashrate",
        "icon": T.CATEGORY_ICONS["hashrate"],
        "title": "Hashrate & Power Stats",
        "subtitle": "Monitors efficiency and savings.",
        "detail": (
            "Reads your miner's hashrate, pool stats, and power consumption. "
            "Wright uses this to show how fans are saving you money by keeping "
            "your miner running at peak efficiency."
        ),
    },
    {
        "key": "uptime",
        "icon": T.CATEGORY_ICONS["uptime"],
        "title": "Uptime & Firmware Info",
        "subtitle": "Tracks reliability metrics.",
        "detail": (
            "Reads how long your miner has been running and its firmware version. "
            "Wright uses this to show how modular design increases uptime "
            "compared to stock fans."
        ),
    },
    {
        "key": "hashboards",
        "icon": T.CATEGORY_ICONS["hashboards"],
        "title": "Per-Hashboard Chip Temps",
        "subtitle": "Detailed hardware diagnostics.",
        "detail": (
            "Reads temperature and status for each hashboard in your miner. "
            "Wright uses this for granular degradation detection, spotting "
            "hot-spots before they cause downtime."
        ),
    },
    {
        "key": "errors",
        "icon": T.CATEGORY_ICONS["errors"],
        "title": "Miner Errors",
        "subtitle": "Automatically files support reports.",
        "detail": (
            "Reads the error log from your miner (timestamps, error codes, "
            "affected components). Wright uses this to notify you of fan "
            "failures and automatically file support reports on your behalf."
        ),
    },
    {
        "key": "auto_update",
        "icon": T.CATEGORY_ICONS["auto_update"],
        "title": "Automatic Updates",
        "subtitle": "Keeps the agent secure.",
        "detail": (
            "Allows Wright One to automatically download and apply new versions "
            "of this agent in the background. Checks run hourly and require no "
            "action on your part."
        ),
    },
    {
        "key": "remote_config",
        "icon": T.CATEGORY_ICONS["remote_config"],
        "title": "Remote Configuration",
        "subtitle": "Adjust settings remotely from your dashboard.",
        "detail": (
            "Allows Wright One support and your customer portal to view and "
            "update this agent's configuration remotely. Passwords are never "
            "transmitted; they are always masked before leaving your machine."
        ),
    },
]


class PermissionsPage(QWidget):
    """Step 1: data-sharing permissions with toggles."""

    next_clicked = pyqtSignal()

    def __init__(self, engine: "ScanningEngine | None" = None, parent: QWidget | None = None):
        super().__init__(parent)
        self._engine = engine
        self.setStyleSheet(f"background: {T.BG_WINDOW};")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(T.CONTENT_PADDING, T.CONTENT_PADDING, T.CONTENT_PADDING, 0)
        outer.setSpacing(0)

        # ── Heading ───────────────────────────────────────────────────────────
        title = QLabel("Welcome to Wright Telemetry")
        title.setFont(make_font(*T.FONT_PAGE_HEADING))
        title.setStyleSheet(f"color: {T.TEXT_PRIMARY};")
        outer.addWidget(title)

        outer.addSpacing(8)

        desc = QLabel(
            "Runs on your local network, monitors miners every 30 seconds, "
            "and streams performance data to your Wright Fan dashboard for "
            "real-time visibility and predictive alerts."
        )
        desc.setFont(make_font(*T.FONT_PAGE_DESC))
        desc.setStyleSheet(f"color: {T.TEXT_SECONDARY};")
        desc.setWordWrap(True)
        outer.addWidget(desc)

        outer.addSpacing(20)

        # ── Scrollable permission list ────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent;")

        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(8)

        self.rows: list[PermissionRow] = []
        for perm in PERMISSIONS:
            color = T.CATEGORY_COLORS.get(perm["key"], T.ACCENT_BLUE)
            row = PermissionRow(
                key=perm["key"],
                icon=perm["icon"],
                title=perm["title"],
                subtitle=perm["subtitle"],
                detail=perm["detail"],
                category_color=color,
                checked=True,
            )
            scroll_layout.addWidget(row)
            self.rows.append(row)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        outer.addWidget(scroll, 1)

        # ── Bottom bar ────────────────────────────────────────────────────────
        outer.addSpacing(12)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 12, 0, 16)

        help_lbl = QLabel("ⓘ  Need help? <a style='color: " + T.ACCENT_BLUE + ";' href='#'>Security Guide.</a>")
        help_lbl.setFont(make_font(*T.FONT_BODY_SMALL))
        help_lbl.setStyleSheet(f"color: {T.TEXT_MUTED};")
        help_lbl.setTextFormat(Qt.TextFormat.RichText)
        bottom.addWidget(help_lbl)

        bottom.addStretch()

        next_btn = PrimaryButton("Next: Discover Miners  →")
        next_btn.clicked.connect(self._on_next)
        bottom.addWidget(next_btn)

        outer.addLayout(bottom)

    def get_consent(self) -> dict[str, bool]:
        """Return current toggle states as a consent dict."""
        return {row.key: row.toggle.isChecked() for row in self.rows}

    def _on_next(self) -> None:
        """Save consent to disk, signal the engine to reload, then navigate."""
        if self._engine is not None:
            self._engine.update_consent(self.get_consent())
        self.next_clicked.emit()


