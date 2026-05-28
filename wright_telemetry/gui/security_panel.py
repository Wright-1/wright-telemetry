"""Right-side security panel: dark security-profile card + GitHub card + Discord card."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from wright_telemetry.gui.fonts import make_font
from wright_telemetry.gui import theme as T


def _dark_card(parent: QWidget | None = None) -> QWidget:
    """Return the dark 'Security Profile' card widget."""
    card = QWidget(parent)
    card.setStyleSheet(f"""
        QWidget {{
            background: {T.BG_SECURITY};
            border-radius: 8px;
        }}
    """)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(10)

    # Header row: lock icon + SECURITY PROFILE label
    header_row = QHBoxLayout()
    header_row.setSpacing(8)

    lock = QLabel("🔒")
    lock.setFixedSize(18, 18)
    lock.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lock.setStyleSheet("font-size: 12px; border: none; background: transparent;")
    header_row.addWidget(lock)

    title = QLabel("SECURITY PROFILE")
    title.setFont(make_font(*T.FONT_SECTION_HEADING))
    title.setStyleSheet(
        f"color: {T.TEXT_ON_DARK}; letter-spacing: 2px; "
        f"border: none; background: transparent;"
    )
    header_row.addWidget(title)
    header_row.addStretch()
    layout.addLayout(header_row)

    # Description
    desc = QLabel("AES-256 encrypted. Nothing leaves your network without your permission.")
    desc.setFont(make_font(*T.FONT_BODY_SMALL))
    desc.setStyleSheet(
        f"color: {T.TEXT_ON_DARK}; border: none; background: transparent;"
    )
    desc.setWordWrap(True)
    layout.addWidget(desc)

    # Encrypted Stream badge
    badge = QWidget()
    badge.setStyleSheet("""
        QWidget {
            background: #2A2D35;
            border-radius: 5px;
            border: 1px solid #3A3D45;
        }
    """)
    badge_layout = QHBoxLayout(badge)
    badge_layout.setContentsMargins(12, 7, 12, 7)
    badge_layout.setSpacing(0)

    badge_text = QLabel("Encrypted Stream")
    badge_text.setFont(make_font(12, 500))
    badge_text.setStyleSheet(
        f"color: {T.TEXT_ON_DARK}; border: none; background: transparent;"
    )
    badge_layout.addWidget(badge_text, 1)

    check = QLabel("✓")
    check.setFont(make_font(14, 700))
    check.setStyleSheet(
        f"color: {T.ACCENT_GREEN}; border: none; background: transparent;"
    )
    badge_layout.addWidget(check)

    layout.addWidget(badge)

    return card


def _github_card(parent: QWidget | None = None) -> QWidget:
    """Return the white GitHub repository card."""
    card = QWidget(parent)
    card.setStyleSheet(f"""
        QWidget {{
            background: {T.BG_CARD};
            border-radius: 8px;
            border: 1px solid {T.BORDER_DEFAULT};
        }}
    """)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(8)

    # Placeholder image area (terminal icon)
    img_area = QWidget()
    img_area.setFixedHeight(72)
    img_area.setStyleSheet(f"""
        QWidget {{
            background: {T.BG_SIDEBAR};
            border-radius: 6px;
            border: 1px solid {T.BORDER_DEFAULT};
        }}
    """)
    img_layout = QVBoxLayout(img_area)
    ph_icon = QLabel(">_")
    ph_icon.setFont(make_font(18, 600))
    ph_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
    ph_icon.setStyleSheet(
        f"color: {T.TEXT_MUTED}; border: none; background: transparent;"
    )
    img_layout.addWidget(ph_icon)
    layout.addWidget(img_area)

    # Title
    gh_title = QLabel("GitHub Repository")
    gh_title.setFont(make_font(13, 600))
    gh_title.setStyleSheet(
        f"color: {T.TEXT_PRIMARY}; border: none; background: transparent;"
    )
    layout.addWidget(gh_title)

    # Description
    gh_desc = QLabel("Open source. Auditable. See the code you are installing.")
    gh_desc.setFont(make_font(*T.FONT_BODY_SMALL))
    gh_desc.setStyleSheet(
        f"color: {T.TEXT_SECONDARY}; border: none; background: transparent;"
    )
    gh_desc.setWordWrap(True)
    layout.addWidget(gh_desc)

    layout.addSpacing(2)

    # View Code button
    view_btn = QPushButton("⛓  View Code")
    view_btn.setFont(make_font(*T.FONT_BUTTON))
    view_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    view_btn.setStyleSheet(f"""
        QPushButton {{
            background: transparent;
            color: {T.TEXT_PRIMARY};
            border: 1px solid {T.BORDER_DEFAULT};
            border-radius: 6px;
            padding: 7px 14px;
        }}
        QPushButton:hover {{
            background: {T.BG_SIDEBAR};
        }}
    """)
    view_btn.clicked.connect(
        lambda: QDesktopServices.openUrl(QUrl("https://github.com/Wright-1/wright-telemetry"))
    )
    layout.addWidget(view_btn)

    return card


def _discord_card(parent: QWidget | None = None) -> QWidget:
    """Return the Discord community card."""
    card = QWidget(parent)
    card.setStyleSheet(f"""
        QWidget {{
            background: {T.BG_CARD};
            border-radius: 8px;
            border: 1px solid {T.BORDER_DEFAULT};
        }}
    """)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(8)

    # Icon area
    img_area = QWidget()
    img_area.setFixedHeight(72)
    img_area.setStyleSheet(f"""
        QWidget {{
            background: #5865F2;
            border-radius: 6px;
            border: none;
        }}
    """)
    img_layout = QVBoxLayout(img_area)
    ph_icon = QLabel("#")
    ph_icon.setFont(make_font(24, 700))
    ph_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
    ph_icon.setStyleSheet(
        "color: white; border: none; background: transparent;"
    )
    img_layout.addWidget(ph_icon)
    layout.addWidget(img_area)

    # Title
    title = QLabel("Discord Community")
    title.setFont(make_font(13, 600))
    title.setStyleSheet(
        f"color: {T.TEXT_PRIMARY}; border: none; background: transparent;"
    )
    layout.addWidget(title)

    # Description
    desc = QLabel("Get help, share feedback, and talk to the Wright One team.")
    desc.setFont(make_font(*T.FONT_BODY_SMALL))
    desc.setStyleSheet(
        f"color: {T.TEXT_SECONDARY}; border: none; background: transparent;"
    )
    desc.setWordWrap(True)
    layout.addWidget(desc)

    layout.addSpacing(2)

    # Join button
    join_btn = QPushButton("Join Discord")
    join_btn.setFont(make_font(*T.FONT_BUTTON))
    join_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    join_btn.setStyleSheet(f"""
        QPushButton {{
            background: transparent;
            color: {T.TEXT_PRIMARY};
            border: 1px solid {T.BORDER_DEFAULT};
            border-radius: 6px;
            padding: 7px 14px;
        }}
        QPushButton:hover {{
            background: {T.BG_SIDEBAR};
        }}
    """)
    join_btn.clicked.connect(
        lambda: QDesktopServices.openUrl(QUrl("https://discord.gg/wrightone"))
    )
    layout.addWidget(join_btn)

    return card


class SecurityPanel(QWidget):
    """Fixed-width right panel: light background, dark security card on top,
    white GitHub card below."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedWidth(T.SECURITY_PANEL_W)
        self.setStyleSheet(
            f"background: {T.BG_WINDOW}; "
            f"border-left: 1px solid {T.BORDER_DEFAULT};"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(T.SECURITY_PADDING, 20, T.SECURITY_PADDING, 20)
        layout.setSpacing(12)

        layout.addWidget(_dark_card())
        layout.addWidget(_github_card())
        layout.addWidget(_discord_card())
        layout.addStretch(1)
