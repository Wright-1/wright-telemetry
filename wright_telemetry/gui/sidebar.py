"""Left sidebar: header, nav items, version badge."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from wright_telemetry.gui.fonts import make_font
from wright_telemetry.gui import theme as T
from wright_telemetry.gui.widgets import NavItem


class Sidebar(QWidget):
    """Fixed-width sidebar with navigation and version info."""

    page_selected = pyqtSignal(str)

    NAV_ITEMS = [
        ("permissions", "⚙", "Permissions"),
        ("discovery", "◎", "Discovery"),
        ("overview", "⊞", "Overview"),
    ]

    def __init__(self, version: str = "0.7.3", parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedWidth(T.SIDEBAR_W)
        self.setStyleSheet(f"background: {T.BG_SIDEBAR}; border-right: 1px solid {T.BORDER_DEFAULT};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(T.SIDEBAR_PADDING, 20, T.SIDEBAR_PADDING, 16)
        layout.setSpacing(0)

        # ── Header ────────────────────────────────────────────────────────────
        header = QLabel("Setup")
        header.setFont(make_font(*T.FONT_NAV_HEADER))
        header.setStyleSheet(f"color: {T.TEXT_PRIMARY};")
        layout.addWidget(header)

        sub = QLabel("Configuration Wizard")
        sub.setFont(make_font(*T.FONT_NAV_SUB))
        sub.setStyleSheet(f"color: {T.TEXT_MUTED};")
        layout.addWidget(sub)

        layout.addSpacing(20)

        # ── Nav items ─────────────────────────────────────────────────────────
        self._items: dict[str, NavItem] = {}
        for key, icon, label in self.NAV_ITEMS:
            item = NavItem(key, icon, label, active=(key == "permissions"))
            item.clicked.connect(self._on_clicked)
            layout.addWidget(item)
            self._items[key] = item

        layout.addStretch(1)

        # ── Version badge ─────────────────────────────────────────────────────
        ver = QLabel(f"v{version}")
        ver.setFont(make_font(*T.FONT_VERSION))
        ver.setStyleSheet(f"color: {T.TEXT_MUTED};")
        layout.addWidget(ver)

        inst = QLabel("LOCAL INSTANCE")
        inst.setFont(make_font(10, 600))
        inst.setStyleSheet(f"color: {T.TEXT_MUTED}; letter-spacing: 1px;")
        layout.addWidget(inst)

    def set_active(self, key: str) -> None:
        for k, item in self._items.items():
            item.setActive(k == key)

    def _on_clicked(self, key: str) -> None:
        self.set_active(key)
        self.page_selected.emit(key)
