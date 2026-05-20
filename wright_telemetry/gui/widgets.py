"""Reusable widgets: toggle switch, nav item, permission row, buttons."""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
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
    THUMB_R = 8

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
        p.drawEllipse(
            int(thumb_x - self.THUMB_R), int(thumb_y - self.THUMB_R),
            self.THUMB_R * 2, self.THUMB_R * 2,
        )
        p.end()


# ── Chevron indicator ─────────────────────────────────────────────────────────


class ChevronLabel(QLabel):
    """Small chevron pointing right (collapsed) or down (expanded)."""

    def __init__(self, expanded: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self._expanded = expanded
        self.setFixedSize(20, 20)
        self._refresh()

    def setExpanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self._refresh()

    def _refresh(self) -> None:
        self.setText("▾" if self._expanded else "›")
        self.setStyleSheet(f"color: {T.TEXT_MUTED}; font-size: 14px;")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)


# ── Permission Row ────────────────────────────────────────────────────────────


class PermissionRow(QWidget):
    """Colored left-border card: icon + title + subtitle + toggle + chevron.

    The left border is rendered by letting the outer container's background
    color (the category color) show through a 3 px gap on the left side of
    the white inner content widget.
    """

    def __init__(
        self,
        key: str,
        icon: str,
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
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # Outer shell — category color acts as the left border
        self.setObjectName(f"prow_{key}")
        self.setStyleSheet(f"""
            QWidget#prow_{key} {{
                background: {category_color};
                border-radius: 6px;
            }}
        """)

        outer_layout = QHBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # 3 px transparent strip — the outer background shows through here
        strip = QWidget()
        strip.setFixedWidth(T.PERM_ROW_BORDER_W)
        strip.setStyleSheet("background: transparent;")
        outer_layout.addWidget(strip)

        # White content area (right ¾ of the card)
        content = QWidget()
        content.setObjectName(f"prow_c_{key}")
        content.setStyleSheet(f"""
            QWidget#prow_c_{key} {{
                background: {T.BG_CARD};
                border-top: 1px solid {T.BORDER_DEFAULT};
                border-right: 1px solid {T.BORDER_DEFAULT};
                border-bottom: 1px solid {T.BORDER_DEFAULT};
                border-left: none;
                border-top-right-radius: 6px;
                border-bottom-right-radius: 6px;
            }}
        """)
        outer_layout.addWidget(content, 1)

        inner = QVBoxLayout(content)
        inner.setContentsMargins(14, 11, 14, 11)
        inner.setSpacing(0)

        # ── Header row ────────────────────────────────────────────────────────
        header = QHBoxLayout()
        header.setSpacing(12)

        icon_lbl = QLabel(icon)
        icon_lbl.setFixedSize(22, 22)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setStyleSheet(
            f"color: {category_color}; font-size: 15px; border: none;"
        )
        header.addWidget(icon_lbl)

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

        # Toggle — intercept click so it doesn't also trigger row expand
        self.toggle = ToggleSwitch(checked=checked)
        self.toggle.mousePressEvent = self._on_toggle_click
        header.addWidget(self.toggle)

        self.chevron = ChevronLabel(expanded=False)
        header.addWidget(self.chevron)

        inner.addLayout(header)

        # ── Expandable detail ─────────────────────────────────────────────────
        self.detail_label = QLabel(detail)
        self.detail_label.setFont(make_font(*T.FONT_PERM_DESC))
        self.detail_label.setStyleSheet(
            f"color: {T.TEXT_SECONDARY}; border: none; padding: 8px 0 0 34px;"
        )
        self.detail_label.setWordWrap(True)
        self.detail_label.setVisible(False)
        inner.addWidget(self.detail_label)

    def _on_toggle_click(self, ev) -> None:
        """Flip toggle state without propagating the click to expand the row."""
        self.toggle._checked = not self.toggle._checked
        self.toggle.toggled.emit(self.toggle._checked)
        self.toggle.update()

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
            self.setStyleSheet(
                f"background: {T.NAV_ACTIVE_BG}; "
                f"border-left: 3px solid {T.ACCENT_BLUE}; "
                f"border-top-right-radius: 4px; "
                f"border-bottom-right-radius: 4px;"
            )
            self.icon_lbl.setStyleSheet(
                f"color: {T.TEXT_PRIMARY}; font-size: 13px; "
                f"border: none; background: transparent;"
            )
            self.text_lbl.setStyleSheet(
                f"color: {T.TEXT_PRIMARY}; font-weight: 600; "
                f"border: none; background: transparent;"
            )
        else:
            self.setStyleSheet(
                "background: transparent; "
                "border-left: 3px solid transparent; "
                "border-radius: 4px;"
            )
            self.icon_lbl.setStyleSheet(
                f"color: {T.TEXT_SECONDARY}; font-size: 13px; "
                f"border: none; background: transparent;"
            )
            self.text_lbl.setStyleSheet(
                f"color: {T.TEXT_SECONDARY}; "
                f"border: none; background: transparent;"
            )

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
