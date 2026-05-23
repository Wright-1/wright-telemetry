"""Portal page — shown during onboarding in place of Overview.

The sole job of this page is to get the user to create their Wright One
account. Once the engine reports a successful agent-info fetch, MainWindow
replaces this page with the real Overview.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from wright_telemetry.gui.fonts import make_font
from wright_telemetry.gui import theme as T
from wright_telemetry.gui.widgets import PrimaryButton


class PortalPage(QWidget):
    """Step 3 of onboarding: direct the user to create a portal account."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background: {T.BG_WINDOW};")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(T.CONTENT_PADDING, T.CONTENT_PADDING,
                                 T.CONTENT_PADDING, T.CONTENT_PADDING)
        outer.setSpacing(0)
        outer.addStretch(1)

        # ── Centre column ─────────────────────────────────────────────────────
        col = QVBoxLayout()
        col.setSpacing(0)

        # Heading
        heading = QLabel("Last step: create your account")
        heading.setFont(make_font(*T.FONT_PAGE_HEADING))
        heading.setStyleSheet(f"color: {T.TEXT_PRIMARY};")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(heading)
        col.addSpacing(10)

        # Description
        desc = QLabel(
            "Your agent is running and collecting data.\n"
            "Sign up at the Wright One portal to see your dashboard, "
            "analytics, and proactive fan alerts."
        )
        desc.setFont(make_font(*T.FONT_PAGE_DESC))
        desc.setStyleSheet(f"color: {T.TEXT_SECONDARY};")
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setWordWrap(True)
        col.addWidget(desc)
        col.addSpacing(28)

        # CTA button — centred
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._open_btn = PrimaryButton("Open Wright One Portal  ↗")
        self._open_btn.setFixedHeight(44)
        self._open_btn.setMinimumWidth(220)
        self._open_btn.clicked.connect(self._on_open)
        btn_row.addWidget(self._open_btn)
        btn_row.addStretch()
        col.addLayout(btn_row)
        col.addSpacing(16)

        # Sub-link
        already = QLabel(
            "Already have an account? Visit "
            f"<a style='color:{T.ACCENT_BLUE};' "
            f"href='https://portal.wrightfan.com'>portal.wrightfan.com</a>"
        )
        already.setFont(make_font(*T.FONT_BODY_SMALL))
        already.setStyleSheet(f"color: {T.TEXT_MUTED};")
        already.setAlignment(Qt.AlignmentFlag.AlignCenter)
        already.setTextFormat(Qt.TextFormat.RichText)
        already.setOpenExternalLinks(True)
        col.addWidget(already)

        # Centred horizontally
        h = QHBoxLayout()
        h.addStretch()
        col_w = QWidget()
        col_w.setMaximumWidth(480)
        col_w.setLayout(col)
        h.addWidget(col_w)
        h.addStretch()
        outer.addLayout(h)

        outer.addStretch(1)

    def _on_open(self) -> None:
        QDesktopServices.openUrl(QUrl("https://portal.wrightfan.com"))
