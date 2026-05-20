"""Left sidebar: header, nav items, version badge."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
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
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Scope to #sidebar so the rule doesn't cascade to children
        self.setObjectName("sidebar")
        self.setStyleSheet(f"""
            QWidget#sidebar {{
                background: {T.BG_SIDEBAR};
            }}
        """)

        # Right border as a separate 1px widget to avoid cascade
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        inner_widget = QWidget()
        inner_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        inner_layout = QVBoxLayout(inner_widget)
        inner_layout.setContentsMargins(0, 16, 0, 14)
        inner_layout.setSpacing(0)

        # ── Header ────────────────────────────────────────────────────────────
        setup_lbl = QLabel("Setup")
        setup_lbl.setFont(make_font(*T.FONT_NAV_HEADER))
        setup_lbl.setStyleSheet(f"color: {T.TEXT_PRIMARY}; background: transparent;")
        setup_lbl.setContentsMargins(T.SIDEBAR_PADDING, 0, T.SIDEBAR_PADDING, 0)
        inner_layout.addWidget(setup_lbl)

        sub_lbl = QLabel("Configuration Wizard")
        sub_lbl.setFont(make_font(*T.FONT_NAV_SUB))
        sub_lbl.setStyleSheet(f"color: {T.TEXT_MUTED}; background: transparent;")
        sub_lbl.setContentsMargins(T.SIDEBAR_PADDING, 0, T.SIDEBAR_PADDING, 0)
        inner_layout.addWidget(sub_lbl)

        inner_layout.addSpacing(10)

        # ── Nav items ─────────────────────────────────────────────────────────
        self._items: dict[str, NavItem] = {}
        for key, icon, label in self.NAV_ITEMS:
            item = NavItem(key, icon, label, active=(key == "permissions"))
            item.clicked.connect(self._on_clicked)
            inner_layout.addWidget(item)
            self._items[key] = item

        inner_layout.addStretch(1)

        # ── Separator line ────────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {T.BORDER_DEFAULT}; border: none;")
        inner_layout.addWidget(sep)

        inner_layout.addSpacing(10)

        # ── Version badge ─────────────────────────────────────────────────────
        ver_row = QHBoxLayout()
        ver_row.setContentsMargins(T.SIDEBAR_PADDING, 0, T.SIDEBAR_PADDING, 0)
        ver_row.setSpacing(6)

        info_icon = QLabel("ⓘ")
        info_icon.setFont(make_font(13, 400))
        info_icon.setStyleSheet(f"color: {T.TEXT_MUTED}; background: transparent;")
        info_icon.setFixedWidth(16)
        ver_row.addWidget(info_icon)

        ver_col = QVBoxLayout()
        ver_col.setSpacing(0)

        ver_lbl = QLabel(f"v{version}")
        ver_lbl.setFont(make_font(*T.FONT_VERSION))
        ver_lbl.setStyleSheet(f"color: {T.TEXT_MUTED}; background: transparent;")
        ver_col.addWidget(ver_lbl)

        inst_lbl = QLabel("LOCAL INSTANCE")
        inst_lbl.setFont(make_font(10, 600))
        inst_lbl.setStyleSheet(f"color: {T.TEXT_MUTED}; background: transparent; letter-spacing: 0.5px;")
        ver_col.addWidget(inst_lbl)

        ver_row.addLayout(ver_col)
        ver_row.addStretch()
        inner_layout.addLayout(ver_row)

        outer.addWidget(inner_widget, 1)

        # Right border as a plain 1px frame
        border = QFrame()
        border.setFixedWidth(1)
        border.setStyleSheet(f"background: {T.BORDER_DEFAULT}; border: none;")
        outer.addWidget(border)

    def set_active(self, key: str) -> None:
        for k, item in self._items.items():
            item.setActive(k == key)

    def _on_clicked(self, key: str) -> None:
        self.set_active(key)
        self.page_selected.emit(key)
