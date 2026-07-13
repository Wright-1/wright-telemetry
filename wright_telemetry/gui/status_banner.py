"""Shared status banner — spans the top of every page in the main window.

Currently drives fan-detection mode messaging (baseline capture, ready-to-
toggle, live dip count). Hidden otherwise. Add new adaptive messages here
as more cross-page states arise.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget

from wright_telemetry.gui.fonts import make_font
from wright_telemetry.gui import theme as T

_FAN_DETECTION_STATE_TEXT: dict[str, str] = {
    "establishing_baseline": "Establishing baseline for {miner_count} miner(s)…",
    "toggle_ready": "Ready — flip a fan switch to identify it. {dip_count} dip(s) detected so far.",
}

_ERROR_DISPLAY_MS = 6000

_BANNER_STYLE = f"""
    QWidget#status_banner {{
        background: {T.ACCENT_BLUE};
        border-bottom: 1px solid {T.ACCENT_BLUE_HOVER};
    }}
"""
_BANNER_ERROR_STYLE = f"""
    QWidget#status_banner {{
        background: {T.ACCENT_RED};
        border-bottom: 1px solid {T.ACCENT_RED};
    }}
"""


class StatusBanner(QWidget):
    """Full-width, single-line status strip shown above the sidebar/content/security row.

    Hidden by default; callers drive visibility + content via update_* methods.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("status_banner")
        self.setStyleSheet(_BANNER_STYLE)
        self.setFixedHeight(32)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(8)

        self._dot = QLabel("●")
        self._dot.setFont(make_font(9, 400))
        self._dot.setStyleSheet(f"color: {T.TEXT_ON_DARK}; background: transparent;")
        layout.addWidget(self._dot)

        self._label = QLabel("")
        self._label.setFont(make_font(12, 500))
        self._label.setStyleSheet(f"color: {T.TEXT_ON_DARK}; background: transparent;")
        layout.addWidget(self._label, 1)

        self.setVisible(False)
        self._error_token = 0  # invalidates any pending auto-hide from show_error()

    def update_fan_detection(self, payload: dict) -> None:
        """payload: {"active": bool, "state": str, "miner_count": int, "dip_count": int}"""
        self._error_token += 1  # a real state update cancels any pending error auto-hide
        self.setStyleSheet(_BANNER_STYLE)

        if not payload.get("active"):
            self.setVisible(False)
            return

        state = payload.get("state", "")
        detail = _FAN_DETECTION_STATE_TEXT.get(state, "").format(
            miner_count=payload.get("miner_count", 0),
            dip_count=payload.get("dip_count", 0),
        )
        self._label.setText(f"You are in Fan Detection Mode — {detail}" if detail else "You are in Fan Detection Mode")
        self.setVisible(True)

    def show_error(self, message: str) -> None:
        """Briefly show a failure message (e.g. fan detection couldn't start),
        then auto-hide — so a failed start is never silent."""
        self._error_token += 1
        token = self._error_token
        self.setStyleSheet(_BANNER_ERROR_STYLE)
        self._label.setText(message)
        self.setVisible(True)
        QTimer.singleShot(_ERROR_DISPLAY_MS, lambda: self._clear_error_if_current(token))

    def _clear_error_if_current(self, token: int) -> None:
        if token != self._error_token:
            return  # a newer state/error update already superseded this one
        self.setStyleSheet(_BANNER_STYLE)
        self.setVisible(False)
