"""Main application window — assembles sidebar, content pages, and security panel."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from wright_telemetry.gui.fonts import make_font
from wright_telemetry.gui import theme as T
from wright_telemetry.gui.pages.permissions import PermissionsPage
from wright_telemetry.gui.pages.discovery import DiscoveryPage
from wright_telemetry.gui.pages.overview import OverviewPage
from wright_telemetry.gui.security_panel import SecurityPanel
from wright_telemetry.gui.sidebar import Sidebar


class MainWindow(QWidget):
    """Top-level window: title bar label + three-column body."""

    PAGE_KEYS = ["permissions", "discovery", "overview"]

    def __init__(self, version: str = "0.7.3"):
        super().__init__()
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
            "permissions": PermissionsPage(),
            "discovery": DiscoveryPage(),
            "overview": OverviewPage(),
        }
        for key in self.PAGE_KEYS:
            self.stack.addWidget(self.pages[key])
        body.addWidget(self.stack, 1)

        # Security panel
        self.security = SecurityPanel()
        body.addWidget(self.security)

        root.addLayout(body, 1)

    def _switch_page(self, key: str) -> None:
        if key in self.pages:
            self.stack.setCurrentWidget(self.pages[key])
            self.sidebar.set_active(key)
