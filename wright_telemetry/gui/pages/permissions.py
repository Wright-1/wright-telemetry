"""Page widgets for each wizard step.

Each page is a QWidget that can be swapped into the main content area.
All pages are static for now — no backend connections.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from wright_telemetry.consent import DEFAULT_CONSENT, METRICS
from wright_telemetry.gui.fonts import make_font
from wright_telemetry.gui import theme as T
from wright_telemetry.gui.widgets import PermissionRow, PrimaryButton, SecondaryButton

if TYPE_CHECKING:
    from wright_telemetry.gui.engine import ScanningEngine


class PermissionsPage(QWidget):
    """Step 1: data-sharing permissions with toggles."""

    next_clicked = pyqtSignal()

    def __init__(self, engine: "ScanningEngine | None" = None, parent: QWidget | None = None):
        super().__init__(parent)
        self._engine = engine

        # Load saved consent so existing preferences survive restarts.
        # Falls back to DEFAULT_CONSENT (all off) for first-run.
        from wright_telemetry.config import load_config
        saved_consent: dict[str, bool] = (
            load_config() or {}
        ).get("consent", DEFAULT_CONSENT)

        # Debounce: wait 300ms after the last toggle before saving
        self._debounce = QTimer()
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self._flush_consent)
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
        for key, info in METRICS.items():
            # Build detail text: full description + the API endpoint it calls.
            detail = info["description"] + f"\n\nAPI call: {info['endpoint']}"
            # First sentence of description becomes the subtitle.
            subtitle = info["description"].split("\n")[0].rstrip(".")
            color = T.CATEGORY_COLORS.get(key, T.ACCENT_BLUE)
            row = PermissionRow(
                key=key,
                icon=T.CATEGORY_ICONS.get(key, "●"),
                title=info["label"],
                subtitle=subtitle,
                detail=detail,
                category_color=color,
                checked=saved_consent.get(key, False),
            )
            scroll_layout.addWidget(row)
            row.toggle.toggled.connect(self._on_toggle_changed)
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

    def _on_toggle_changed(self, _checked: bool) -> None:
        """Restart the debounce timer on every toggle flip."""
        self._debounce.start()  # restarts automatically if already running

    def _flush_consent(self) -> None:
        """Save current consent state and signal the scheduler."""
        if self._engine is not None:
            self._engine.update_consent(self.get_consent())

    def _on_next(self) -> None:
        """Flush any pending debounce immediately, then navigate."""
        self._debounce.stop()
        self._flush_consent()
        self.next_clicked.emit()


