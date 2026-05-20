"""Overview page — agent status summary (placeholder)."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from wright_telemetry.gui.fonts import make_font
from wright_telemetry.gui import theme as T


class OverviewPage(QWidget):
    """Step 3: agent overview with link to web portal (placeholder)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setStyleSheet(f"background: {T.BG_WINDOW};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(T.CONTENT_PADDING, T.CONTENT_PADDING,
                                  T.CONTENT_PADDING, T.CONTENT_PADDING)

        title = QLabel("Agent Overview")
        title.setFont(make_font(*T.FONT_PAGE_HEADING))
        title.setStyleSheet(f"color: {T.TEXT_PRIMARY};")
        layout.addWidget(title)

        layout.addSpacing(8)

        desc = QLabel("Your agent is configured. Visit the web portal for detailed monitoring.")
        desc.setFont(make_font(*T.FONT_PAGE_DESC))
        desc.setStyleSheet(f"color: {T.TEXT_SECONDARY};")
        layout.addWidget(desc)

        layout.addStretch()

        placeholder = QLabel("Overview UI will be built here.")
        placeholder.setFont(make_font(14, 400))
        placeholder.setStyleSheet(f"color: {T.TEXT_MUTED};")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(placeholder)

        layout.addStretch()
