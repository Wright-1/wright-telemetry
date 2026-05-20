"""Left sidebar: header, nav items, version badge."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from wright_telemetry.gui.fonts import make_font
from wright_telemetry.gui import theme as T
from wright_telemetry.gui.widgets import NavItem


class Sidebar(QWidget):
    """Fixed-width sidebar with navigation and version info."""

    page_selected = pyqtSignal(str)

    NAV_ITEMS = [
        ("permissions", "⊛", "Permissions"),
        ("discovery",   "◎", "Discovery"),
        ("overview",    "⊞", "Overview"),
    ]

    def __init__(self, version: str = "0.7.3", parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedWidth(T.SIDEBAR_W)
        self.setStyleSheet(
            f"background: {T.BG_SIDEBAR}; "
            f"border-right: 1px solid {T.BORDER_DEFAULT};"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 16, 0, 14)
        layout.setSpacing(0)

        # ── Header ────────────────────────────────────────────────────────────
        header_block = QWidget()
        header_block.setStyleSheet("background: transparent;")
        hb_layout = QVBoxLayout(header_block)
        hb_layout.setContentsMargins(T.SIDEBAR_PADDING, 0, T.SIDEBAR_PADDING, 0)
        hb_layout.setSpacing(1)

        setup_lbl = QLabel("Setup")
        setup_lbl.setFont(make_font(*T.FONT_NAV_HEADER))
        setup_lbl.setStyleSheet(f"color: {T.TEXT_PRIMARY};")
        hb_layout.addWidget(setup_lbl)

        sub_lbl = QLabel("Configuration Wizard")
        sub_lbl.setFont(make_font(*T.FONT_NAV_SUB))
        sub_lbl.setStyleSheet(f"color: {T.TEXT_MUTED};")
        hb_layout.addWidget(sub_lbl)

        layout.addWidget(header_block)
        layout.addSpacing(10)

        # ── Nav items ─────────────────────────────────────────────────────────
        self._items: dict[str, NavItem] = {}
        for key, icon, label in self.NAV_ITEMS:
            item = NavItem(key, icon, label, active=(key == "permissions"))
            item.clicked.connect(self._on_clicked)
            layout.addWidget(item)
            self._items[key] = item

        layout.addStretch(1)

        # ── Version badge ─────────────────────────────────────────────────────
        ver_block = QWidget()
        ver_block.setStyleSheet(
            f"background: transparent; "
            f"border-top: 1px solid {T.BORDER_DEFAULT};"
        )
        vb_layout = QHBoxLayout(ver_block)
        vb_layout.setContentsMargins(T.SIDEBAR_PADDING, 10, T.SIDEBAR_PADDING, 0)
        vb_layout.setSpacing(6)

        info_icon = QLabel("ⓘ")
        info_icon.setFont(make_font(13, 400))
        info_icon.setStyleSheet(f"color: {T.TEXT_MUTED};")
        info_icon.setFixedWidth(16)
        vb_layout.addWidget(info_icon)

        ver_text = QVBoxLayout()
        ver_text.setSpacing(0)

        ver_lbl = QLabel(f"v{version}")
        ver_lbl.setFont(make_font(*T.FONT_VERSION))
        ver_lbl.setStyleSheet(f"color: {T.TEXT_MUTED};")
        ver_text.addWidget(ver_lbl)

        inst_lbl = QLabel("LOCAL INSTANCE")
        inst_lbl.setFont(make_font(10, 600))
        inst_lbl.setStyleSheet(f"color: {T.TEXT_MUTED}; letter-spacing: 0.5px;")
        ver_text.addWidget(inst_lbl)

        vb_layout.addLayout(ver_text)
        vb_layout.addStretch()
        layout.addWidget(ver_block)

    def set_active(self, key: str) -> None:
        for k, item in self._items.items():
            item.setActive(k == key)

    def _on_clicked(self, key: str) -> None:
        self.set_active(key)
        self.page_selected.emit(key)
