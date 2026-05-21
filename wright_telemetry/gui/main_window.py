"""Main application window — assembles sidebar, content pages, and security panel."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from wright_telemetry.gui.fonts import make_font
from wright_telemetry.gui import theme as T
from wright_telemetry.gui.pages.access_key import AccessKeyPage
from wright_telemetry.gui.pages.permissions import PermissionsPage
from wright_telemetry.gui.pages.discovery import DiscoveryPage
from wright_telemetry.gui.pages.overview import OverviewPage
from wright_telemetry.gui.security_panel import SecurityPanel
from wright_telemetry.gui.sidebar import Sidebar

if TYPE_CHECKING:
    from wright_telemetry.gui.engine import ScanningEngine


class MainWindow(QWidget):
    """Top-level window: title bar label + three-column body.

    Pass ``needs_provisioning=True`` to show the access-key page first.
    The engine is not started until provisioning completes (or immediately
    if credentials already exist).
    """

    PAGE_KEYS = ["permissions", "discovery", "overview"]

    def __init__(
        self,
        version: str = "0.7.3",
        engine: "ScanningEngine | None" = None,
        needs_provisioning: bool = False,
    ):
        super().__init__()
        self._engine = engine
        self._needs_provisioning = needs_provisioning
        self.setWindowTitle("Wright Telemetry Collector — Local Agent")
        self.resize(T.WINDOW_W, T.WINDOW_H)
        self.setMinimumSize(T.WINDOW_MIN_W, T.WINDOW_MIN_H)
        self.setStyleSheet(f"background: {T.BG_WINDOW};")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Window title bar ──────────────────────────────────────────────────
        title_bar = QWidget()
        title_bar.setFixedHeight(36)
        title_bar.setStyleSheet(
            f"background: {T.BG_SIDEBAR}; border-bottom: 1px solid {T.BORDER_DEFAULT};"
        )
        tb_layout = QHBoxLayout(title_bar)
        tb_layout.setContentsMargins(16, 0, 16, 0)
        tb_label = QLabel("WRIGHT TELEMETRY COLLECTOR - LOCAL AGENT")
        tb_label.setFont(make_font(11, 600))
        tb_label.setStyleSheet(f"color: {T.TEXT_PRIMARY}; letter-spacing: 0.5px;")
        tb_layout.addWidget(tb_label)
        tb_layout.addStretch()
        root.addWidget(title_bar)

        # ── Three-column body ─────────────────────────────────────────────────
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        # Sidebar
        self.sidebar = Sidebar(version=version)
        self.sidebar.page_selected.connect(self._switch_page)
        body.addWidget(self.sidebar)

        # Stacked content area
        self.stack = QStackedWidget()
        self.pages: dict[str, QWidget] = {
            "permissions": PermissionsPage(engine=engine),
            "discovery": DiscoveryPage(engine=engine),
            "overview": OverviewPage(engine=engine),
        }
        for key in self.PAGE_KEYS:
            self.stack.addWidget(self.pages[key])

        # Access-key provisioning page (shown before the normal pages)
        self._access_key_page = AccessKeyPage()
        self.stack.addWidget(self._access_key_page)
        self._access_key_page.provisioned.connect(self._on_provisioned)

        body.addWidget(self.stack, 1)

        # Security panel
        self.security = SecurityPanel()
        body.addWidget(self.security)

        root.addLayout(body, 1)

        # ── Engine signal connections ────────────────────────────────────────
        if engine is not None:
            engine.signals.ws_status_changed.connect(self.sidebar.set_ws_status)

        # Wire permissions → discovery navigation
        self.pages["permissions"].next_clicked.connect(
            lambda: self._switch_page("discovery")
        )

        # Show access-key page first if credentials are missing
        if needs_provisioning:
            self.stack.setCurrentWidget(self._access_key_page)
            self.sidebar.setVisible(False)
            self.security.setVisible(False)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._engine is not None:
            self._engine.stop()
        event.accept()

    def _on_provisioned(self) -> None:
        """Called when the access-key page successfully redeems credentials.

        Re-reads the freshly saved config, builds and starts the engine,
        then transitions to the normal permissions page.
        """
        from wright_telemetry.config import load_config
        from wright_telemetry.gui.engine import ScanningEngine

        cfg = load_config() or {}
        self._engine = ScanningEngine(cfg)

        # Wire up engine signals now that we have one
        self._engine.signals.ws_status_changed.connect(self.sidebar.set_ws_status)

        # Give the engine reference to the existing pages that need it
        self.pages["permissions"]._engine = self._engine
        self.pages["discovery"]._engine = self._engine
        self.pages["overview"]._engine = self._engine

        self._engine.start()

        # Reveal the sidebar and switch to the permissions page
        self.sidebar.setVisible(True)
        self.security.setVisible(True)
        self._switch_page("permissions")

    def _switch_page(self, key: str) -> None:
        if key in self.pages:
            self.stack.setCurrentWidget(self.pages[key])
            self.sidebar.set_active(key)
            self.security.setVisible(key == "permissions")
