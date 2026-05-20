"""Reusable widgets: toggle switch, nav item, permission row, buttons."""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from wright_telemetry.gui.fonts import make_font
from wright_telemetry.gui import theme as T


# ── Toggle Switch ─────────────────────────────────────────────────────────────


class ToggleSwitch(QWidget):
    """A custom on/off toggle matching the design spec."""

    toggled = pyqtSignal(bool)

    TRACK_W = 40
    TRACK_H = 22
    THUMB_R = 8  # radius

    def __init__(self, checked: bool = True, parent: QWidget | None = None):
        super().__init__(parent)
        self._checked = checked
        self.setFixedSize(self.TRACK_W, self.TRACK_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, on: bool) -> None:
        self._checked = on
        self.update()

    def mousePressEvent(self, ev):  # noqa: N802
        self._checked = not self._checked
        self.toggled.emit(self._checked)
        self.update()

    def paintEvent(self, ev):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        track_color = QColor(T.ACCENT_BLUE) if self._checked else QColor("#D1D5DB")
        path = QPainterPath()
        r = self.TRACK_H / 2
        path.addRoundedRect(0, 0, self.TRACK_W, self.TRACK_H, r, r)
        p.fillPath(path, track_color)

        thumb_x = self.TRACK_W - self.THUMB_R - 5 if self._checked else self.THUMB_R + 5
        thumb_y = self.TRACK_H / 2
        p.setBrush(QColor("#FFFFFF"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(int(thumb_x - self.THUMB_R), int(thumb_y - self.THUMB_R),
                       self.THUMB_R * 2, self.THUMB_R * 2)
        p.end()


# ── Chevron indicator ─────────────────────────────────────────────────────────


class ChevronLabel(QLabel):
    """A small chevron that can point down or right."""

    def __init__(self, expanded: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self._expanded = expanded
        self.setFixedSize(20, 20)
        self._update_text()

    def setExpanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self._update_text()

    def _update_text(self) -> None:
        arrow = "▾" if self._expanded else "›"
        self.setText(arrow)
        self.setStyleSheet(f"color: {T.TEXT_MUTED}; font-size: 14px;")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)


# ── Permission Row ────────────────────────────────────────────────────────────


class PermissionRow(QWidget):
    """A single permission item: colored left border, icon area, title,
    subtitle, toggle, chevron, and expandable detail text."""

    def __init__(
        self,
        key: str,
        title: str,
        subtitle: str,
        detail: str,
        category_color: str,
        checked: bool = True,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.key = key
        self._expanded = False

        self.setStyleSheet(
            f"""
            PermissionRow {{
                background: {T.BG_CARD};
                border: 1px solid {T.BORDER_DEFAULT};
                border-left: {T.PERM_ROW_BORDER_W}px solid {category_color};
                border-radius: 6px;
            }}
            """
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(0)

        # ── Header row ────────────────────────────────────────────────────────
        header = QHBoxLayout()
        header.setSpacing(12)

        # Icon placeholder (colored dot for now)
        icon = QLabel("●")
        icon.setFixedSize(24, 24)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(f"color: {category_color}; font-size: 14px; border: none;")
        header.addWidget(icon)

        # Title + subtitle column
        text_col = QVBoxLayout()
        text_col.setSpacing(2)

        title_lbl = QLabel(title)
        title_lbl.setFont(make_font(*T.FONT_PERM_TITLE))
        title_lbl.setStyleSheet(f"color: {T.TEXT_PRIMARY}; border: none;")
        text_col.addWidget(title_lbl)

        sub_lbl = QLabel(subtitle)
        sub_lbl.setFont(make_font(*T.FONT_PERM_DESC))
        sub_lbl.setStyleSheet(f"color: {T.TEXT_SECONDARY}; border: none;")
        text_col.addWidget(sub_lbl)

        header.addLayout(text_col, 1)

        # Toggle
        self.toggle = ToggleSwitch(checked=checked)
        header.addWidget(self.toggle)

        # Chevron
        self.chevron = ChevronLabel(expanded=False)
        self.chevron.setCursor(Qt.CursorShape.PointingHandCursor)
        header.addWidget(self.chevron)

        outer.addLayout(header)

        # ── Detail text (hidden by default) ───────────────────────────────────
        self.detail_label = QLabel(detail)
        self.detail_label.setFont(make_font(*T.FONT_PERM_DESC))
        self.detail_label.setStyleSheet(
            f"color: {T.TEXT_SECONDARY}; border: none; padding: 8px 0 0 36px;"
        )
        self.detail_label.setWordWrap(True)
        self.detail_label.setVisible(False)
        outer.addWidget(self.detail_label)

    def mousePressEvent(self, ev):  # noqa: N802
        self._expanded = not self._expanded
        self.chevron.setExpanded(self._expanded)
        self.detail_label.setVisible(self._expanded)

    def sizeHint(self) -> QSize:
        return super().sizeHint()


# ── Navigation Item ───────────────────────────────────────────────────────────


class NavItem(QWidget):
    """Sidebar navigation row: icon + label."""

    clicked = pyqtSignal(str)

    def __init__(self, key: str, icon_char: str, label: str, active: bool = False,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.key = key
        self._active = active
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(T.NAV_ROW_H)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(10)

        self.icon_lbl = QLabel(icon_char)
        self.icon_lbl.setFixedWidth(20)
        self.icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.icon_lbl)

        self.text_lbl = QLabel(label)
        self.text_lbl.setFont(make_font(*T.FONT_NAV_ITEM))
        layout.addWidget(self.text_lbl, 1)

        self._apply_style()

    def setActive(self, active: bool) -> None:
        self._active = active
        self._apply_style()

    def _apply_style(self) -> None:
        if self._active:
            bg = T.NAV_ACTIVE_BG
            fg = T.ACCENT_BLUE
            border = f"border-left: 3px solid {T.ACCENT_BLUE};"
        else:
            bg = "transparent"
            fg = T.TEXT_SECONDARY
            border = "border-left: 3px solid transparent;"
        self.setStyleSheet(f"background: {bg}; {border} border-radius: 4px;")
        self.icon_lbl.setStyleSheet(f"color: {fg}; font-size: 14px; border: none;")
        self.text_lbl.setStyleSheet(f"color: {fg}; border: none;")

    def mousePressEvent(self, ev):  # noqa: N802
        self.clicked.emit(self.key)


# ── Buttons ───────────────────────────────────────────────────────────────────


class PrimaryButton(QPushButton):
    """Blue filled button."""

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setFont(make_font(*T.FONT_BUTTON))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(f"""
            QPushButton {{
                background: {T.ACCENT_BLUE};
                color: {T.TEXT_ON_DARK};
                border: none;
                border-radius: {T.BUTTON_RADIUS}px;
                padding: 10px 24px;
            }}
            QPushButton:hover {{
                background: {T.ACCENT_BLUE_HOVER};
            }}
        """)


class SecondaryButton(QPushButton):
    """White outlined button."""

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setFont(make_font(*T.FONT_BUTTON))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(f"""
            QPushButton {{
                background: {T.BG_CARD};
                color: {T.TEXT_PRIMARY};
                border: 1px solid {T.BORDER_DEFAULT};
                border-radius: {T.BUTTON_RADIUS}px;
                padding: 10px 24px;
            }}
            QPushButton:hover {{
                background: {T.BG_CARD_HOVER};
            }}
        """)
