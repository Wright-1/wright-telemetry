"""Right-side security profile panel (dark background)."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from wright_telemetry.gui.fonts import make_font
from wright_telemetry.gui import theme as T


class SecurityPanel(QWidget):
    """Dark panel showing encryption status and GitHub link."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedWidth(T.SECURITY_PANEL_W)
        self.setStyleSheet(f"background: {T.BG_SECURITY}; border-left: 1px solid #2A2D35;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(T.SECURITY_PADDING, 24, T.SECURITY_PADDING, 24)
        layout.setSpacing(16)

        # ── Header ────────────────────────────────────────────────────────────
        header_row = QHBoxLayout()
        lock_icon = QLabel("🔒")
        lock_icon.setStyleSheet("font-size: 14px; border: none;")
        header_row.addWidget(lock_icon)

        title = QLabel("SECURITY PROFILE")
        title.setFont(make_font(*T.FONT_SECTION_HEADING))
        title.setStyleSheet(
            f"color: {T.TEXT_ON_DARK}; letter-spacing: 2px; border: none;"
        )
        header_row.addWidget(title)
        header_row.addStretch()
        layout.addLayout(header_row)

        # ── Description ───────────────────────────────────────────────────────
        desc = QLabel(
            "Data never leaves your network without\npermission. AES-256 encrypted."
        )
        desc.setFont(make_font(*T.FONT_BODY_SMALL))
        desc.setStyleSheet(f"color: {T.TEXT_ON_DARK_MUTED};")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # ── Encryption badge ──────────────────────────────────────────────────
        badge = QWidget()
        badge.setStyleSheet(
            f"background: #2A2D35; border-radius: 6px; border: 1px solid #3A3D45;"
        )
        badge_layout = QHBoxLayout(badge)
        badge_layout.setContentsMargins(12, 8, 12, 8)

        badge_text = QLabel("Encrypted Stream")
        badge_text.setFont(make_font(*T.FONT_BODY_SMALL))
        badge_text.setStyleSheet(f"color: {T.TEXT_ON_DARK}; border: none;")
        badge_layout.addWidget(badge_text, 1)

        check = QLabel("✓")
        check.setStyleSheet(f"color: {T.ACCENT_GREEN}; font-size: 16px; font-weight: bold; border: none;")
        badge_layout.addWidget(check)

        layout.addWidget(badge)

        layout.addSpacing(8)

        # ── GitHub section ────────────────────────────────────────────────────
        # Placeholder image area
        gh_placeholder = QWidget()
        gh_placeholder.setFixedHeight(80)
        gh_placeholder.setStyleSheet(
            "background: #2A2D35; border-radius: 6px; border: 1px solid #3A3D45;"
        )
        ph_layout = QVBoxLayout(gh_placeholder)
        ph_icon = QLabel("▷")
        ph_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ph_icon.setStyleSheet(f"color: {T.TEXT_ON_DARK_MUTED}; font-size: 24px; border: none;")
        ph_layout.addWidget(ph_icon)
        layout.addWidget(gh_placeholder)

        gh_title = QLabel("GitHub Repository")
        gh_title.setFont(make_font(13, 600))
        gh_title.setStyleSheet(f"color: {T.TEXT_ON_DARK};")
        layout.addWidget(gh_title)

        gh_desc = QLabel("Open source. Auditable. See the code\nyou are installing.")
        gh_desc.setFont(make_font(*T.FONT_BODY_SMALL))
        gh_desc.setStyleSheet(f"color: {T.TEXT_ON_DARK_MUTED};")
        gh_desc.setWordWrap(True)
        layout.addWidget(gh_desc)

        layout.addSpacing(4)

        view_btn = QPushButton("👁  View Code")
        view_btn.setFont(make_font(*T.FONT_BUTTON))
        view_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        view_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {T.TEXT_ON_DARK};
                border: 1px solid #3A3D45;
                border-radius: 6px;
                padding: 8px 16px;
            }}
            QPushButton:hover {{
                background: #2A2D35;
            }}
        """)
        layout.addWidget(view_btn)

        layout.addStretch(1)
