"""Page widgets for each wizard step.

Each page is a QWidget that can be swapped into the main content area.
All pages are static for now — no backend connections.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from wright_telemetry.consent import DEFAULT_CONSENT, METRICS
from wright_telemetry.gui.fonts import make_font
from wright_telemetry.gui import theme as T
from wright_telemetry.gui.widgets import PermissionRow

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

        outer.addSpacing(16)

        # ── Legal banner ──────────────────────────────────────────────────────
        legal_banner = QWidget()
        legal_banner.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        legal_banner.setObjectName("legal_banner")
        legal_banner.setStyleSheet(f"""
            QWidget#legal_banner {{
                background: {T.BG_CARD};
                border: 1px solid {T.BORDER_DEFAULT};
                border-radius: 8px;
            }}
        """)
        legal_layout = QHBoxLayout(legal_banner)
        legal_layout.setContentsMargins(14, 10, 14, 10)
        legal_layout.setSpacing(0)

        legal_text = QLabel(
            "By enabling data collection you agree to the "
            f'<a href="https://placeholder.wrightone.io/privacy" '
            f'style="color: {T.ACCENT_BLUE}; text-decoration: none;">Privacy Policy</a>'
            " and "
            f'<a href="https://placeholder.wrightone.io/terms" '
            f'style="color: {T.ACCENT_BLUE}; text-decoration: none;">Terms of Service</a>.'
            " Data never leaves your local network without your consent."
        )
        legal_text.setFont(make_font(12, 400))
        legal_text.setStyleSheet(f"color: {T.TEXT_SECONDARY}; background: transparent;")
        legal_text.setWordWrap(True)
        legal_text.setOpenExternalLinks(False)
        legal_text.linkActivated.connect(
            lambda url: QDesktopServices.openUrl(QUrl(url))
        )
        legal_layout.addWidget(legal_text, 1)
        outer.addWidget(legal_banner)

        outer.addSpacing(14)

        # ── Scrollable permission list ────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent;")

        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(0)

        # All rows sit inside one shared card
        card = QWidget()
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setObjectName("perm_card")
        card.setStyleSheet(f"""
            QWidget#perm_card {{
                background: {T.BG_CARD};
                border: 1px solid {T.BORDER_DEFAULT};
                border-radius: 8px;
            }}
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        # Concise one-line subtitles — the full description is in the expanded detail.
        _SUBTITLES: dict[str, str] = {
            "cooling":       "Temperature sensors and fan speeds from your miner",
            "hashrate":      "Hashrate, pool stats, and power consumption",
            "uptime":        "Miner uptime and current firmware version",
            "hashboards":    "Temperature and status for each hashboard",
            "errors":        "Error log: timestamps, error codes, and affected components",
            "auto_update":   "Auto-download and apply new agent versions in the background",
            "remote_config": "View and update agent config remotely from your dashboard",
        }

        self.rows: list[PermissionRow] = []
        items = list(METRICS.items())
        for i, (key, info) in enumerate(items):
            endpoint = info["endpoint"]
            detail = info["description"] + f"\n\nAPI call: {endpoint}"
            subtitle = _SUBTITLES.get(key, info["description"].split("\n")[0].rstrip("."))
            color = T.CATEGORY_COLORS.get(key, T.ACCENT_BLUE)
            row = PermissionRow(
                key=key,
                icon=T.CATEGORY_ICONS.get(key, "*"),
                title=info["label"],
                subtitle=subtitle,
                detail=detail,
                category_color=color,
                checked=saved_consent.get(key, True),
            )
            card_layout.addWidget(row)
            row.toggle.toggled.connect(self._on_toggle_changed)
            self.rows.append(row)

            # Separator between rows (not after the last one)
            if i < len(items) - 1:
                sep = QFrame()
                sep.setFrameShape(QFrame.Shape.HLine)
                sep.setFixedHeight(1)
                sep.setStyleSheet(
                    f"background: {T.BORDER_SUBTLE}; border: none; "
                    f"margin-left: 64px;"
                )
                card_layout.addWidget(sep)

        scroll_layout.addWidget(card)
        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        outer.addWidget(scroll, 1)

        # Flush the initial (all-on) consent state immediately so the engine
        # and config file reflect the correct defaults from the very first run,
        # without waiting for the user to change a toggle or click Next.
        QTimer.singleShot(0, self._flush_consent)


    def get_consent(self) -> dict[str, bool]:
        """Return current toggle states as a consent dict."""
        return {row.key: row.toggle.isChecked() for row in self.rows}

    def _on_toggle_changed(self, _checked: bool) -> None:
        """Restart the debounce timer on every toggle flip."""
        self._debounce.start()  # restarts automatically if already running

    def _flush_consent(self) -> None:
        """Save current consent state and signal the scheduler.

        If the engine is already wired, delegates to it so the scheduler
        is hot-reloaded.  Otherwise writes directly to disk so that the
        WebSocket client and scheduler always see the correct defaults
        even before the engine comes up (e.g. first-run provisioning flow).
        """
        consent = self.get_consent()
        if self._engine is not None:
            self._engine.update_consent(consent)
        else:
            # Engine not attached yet — persist directly so other components
            # (ws_client, scheduler) read the correct defaults from disk.
            from wright_telemetry.config import load_config, save_config
            cfg = load_config() or {}
            cfg["consent"] = consent
            save_config(cfg)

    def on_next(self) -> None:
        """Called by the wizard when its Next button is pressed.

        Flushes any pending debounce immediately so consent is always
        persisted before the page transitions, then emits next_clicked.
        """
        self._debounce.stop()
        self._flush_consent()
        self.next_clicked.emit()


