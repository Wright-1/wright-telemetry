"""Discovery page — subnet entry, scan progress, active scan table."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from wright_telemetry.gui.fonts import make_font
from wright_telemetry.gui import theme as T
from wright_telemetry.gui.widgets import PrimaryButton


# ── Helpers ───────────────────────────────────────────────────────────────────

def _card(parent=None) -> QWidget:
    """White rounded card with a border."""
    w = QWidget(parent)
    w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    w.setObjectName("card")
    w.setStyleSheet(f"""
        QWidget#card {{
            background: {T.BG_CARD};
            border: 1px solid {T.BORDER_DEFAULT};
            border-radius: 8px;
        }}
    """)
    return w


def _label(text: str, size: int, weight: int, color: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setFont(make_font(size, weight))
    lbl.setStyleSheet(f"color: {color}; background: transparent;")
    return lbl


def _hdivider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    line.setStyleSheet(f"background: {T.BORDER_DEFAULT}; border: none;")
    return line


# ── Sub-widgets ───────────────────────────────────────────────────────────────

class _AlertCard(QWidget):
    """Red-bordered 'No miners detected' warning card."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("alert")
        self.setStyleSheet(f"""
            QWidget#alert {{
                background: #FFF8F8;
                border: 1px solid #FECACA;
                border-left: 3px solid {T.ACCENT_RED};
                border-radius: 8px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        # Title row
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        icon = _label("⚠", 16, 600, T.ACCENT_RED)
        icon.setFixedWidth(22)
        title_row.addWidget(icon)
        title_row.addWidget(_label("No miners detected", 14, 600, T.TEXT_PRIMARY))
        title_row.addStretch()
        layout.addLayout(title_row)

        # Description
        desc = _label(
            "The automated scan has not found any compatible telemetry sources. "
            "This usually indicates a networking or configuration issue.",
            12, 400, T.TEXT_SECONDARY,
        )
        desc.setWordWrap(True)
        desc.setContentsMargins(32, 0, 0, 0)
        layout.addWidget(desc)

        # 2×2 checklist
        grid = QGridLayout()
        grid.setContentsMargins(32, 4, 0, 0)
        grid.setHorizontalSpacing(32)
        grid.setVerticalSpacing(6)
        tips = [
            "Check local network connectivity.",
            "Ensure miners are on the same subnet as this host.",
            "Verify VPN/Firewall is not blocking Port 4028.",
            "Confirm miners have API Access enabled.",
        ]
        for i, tip in enumerate(tips):
            row, col = divmod(i, 2)
            tip_row = QHBoxLayout()
            tip_row.setSpacing(6)
            chk = _label("◎", 12, 400, T.ACCENT_ORANGE)
            chk.setFixedWidth(16)
            tip_row.addWidget(chk)
            tip_row.addWidget(_label(tip, 12, 400, T.TEXT_SECONDARY))
            tip_row.addStretch()
            cell = QWidget()
            cell.setStyleSheet("background: transparent;")
            cell.setLayout(tip_row)
            grid.addWidget(cell, row, col)
        layout.addLayout(grid)


class _ProgressCard(QWidget):
    """Global scan progress bar card."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("prog_card")
        self.setStyleSheet(f"""
            QWidget#prog_card {{
                background: {T.BG_CARD};
                border: 1px solid {T.BORDER_DEFAULT};
                border-radius: 8px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        # Header row
        hdr = QHBoxLayout()
        hdr.addWidget(_label("GLOBAL SCAN PROGRESS", 11, 700, T.TEXT_MUTED))
        hdr.addStretch()
        hdr.addWidget(_label("75% Complete", 12, 700, T.TEXT_PRIMARY))
        layout.addLayout(hdr)

        # Progress bar
        bar = QProgressBar()
        bar.setValue(75)
        bar.setTextVisible(False)
        bar.setFixedHeight(10)
        bar.setStyleSheet(f"""
            QProgressBar {{
                background: {T.BORDER_DEFAULT};
                border-radius: 5px;
                border: none;
            }}
            QProgressBar::chunk {{
                background: {T.TEXT_PRIMARY};
                border-radius: 5px;
            }}
        """)
        layout.addWidget(bar)

        # Footer row
        ftr = QHBoxLayout()
        ftr.addWidget(_label("Scanning: 192.168.10.x…", 12, 400, T.TEXT_SECONDARY))
        ftr.addStretch()
        ftr.addWidget(_label("Elapsed: 00:04:12", 12, 400, T.TEXT_MUTED))
        layout.addLayout(ftr)


class _SubnetEntryCard(QWidget):
    """Left panel — manual CIDR input + port sweep checkbox + add button."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("subnet_card")
        self.setStyleSheet(f"""
            QWidget#subnet_card {{
                background: {T.BG_CARD};
                border: 1px solid {T.BORDER_DEFAULT};
                border-radius: 8px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        layout.addWidget(_label("Manual Subnet Entry", 16, 700, T.TEXT_PRIMARY))

        layout.addWidget(_label("CIDR RANGE (CSV)", 11, 600, T.TEXT_MUTED))

        self.cidr_input = QTextEdit()
        self.cidr_input.setPlaceholderText("192.168.1.0/24, 10.0.5.0/24")
        self.cidr_input.setFixedHeight(80)
        self.cidr_input.setFont(make_font(12, 400))
        self.cidr_input.setStyleSheet(f"""
            QTextEdit {{
                background: {T.BG_WINDOW};
                border: 1px solid {T.BORDER_DEFAULT};
                border-radius: 6px;
                padding: 8px;
                color: {T.TEXT_PRIMARY};
            }}
        """)
        layout.addWidget(self.cidr_input)

        sweep = QCheckBox("Enable Port Sweep (Deep Scan)")
        sweep.setFont(make_font(12, 400))
        sweep.setStyleSheet(f"color: {T.TEXT_SECONDARY}; background: transparent;")
        layout.addWidget(sweep)

        add_btn = QPushButton("ADD TO QUEUE")
        add_btn.setFont(make_font(12, 700))
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.setStyleSheet(f"""
            QPushButton {{
                background: {T.TEXT_PRIMARY};
                color: #FFFFFF;
                border: none;
                border-radius: 6px;
                padding: 11px;
                letter-spacing: 0.5px;
            }}
            QPushButton:hover {{
                background: #2A2D35;
            }}
        """)
        layout.addWidget(add_btn)

        layout.addStretch()

        # Guidance info box
        guidance = QWidget()
        guidance.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        guidance.setObjectName("guidance")
        guidance.setStyleSheet(f"""
            QWidget#guidance {{
                background: {T.BG_SIDEBAR};
                border: 1px solid {T.BORDER_DEFAULT};
                border-radius: 6px;
            }}
        """)
        g_layout = QVBoxLayout(guidance)
        g_layout.setContentsMargins(14, 12, 14, 12)
        g_layout.setSpacing(4)

        g_title_row = QHBoxLayout()
        g_title_row.setSpacing(6)
        g_icon = _label("ⓘ", 13, 400, T.TEXT_MUTED)
        g_icon.setFixedWidth(18)
        g_title_row.addWidget(g_icon)
        g_title_row.addWidget(_label("Subnet Guidance", 12, 600, T.TEXT_PRIMARY))
        g_title_row.addStretch()
        g_layout.addLayout(g_title_row)

        g_desc = _label(
            "A subnet is a logical subdivision of an IP network. "
            "Check your controller (Unifi, Sophos, Cisco) for details.",
            12, 400, T.TEXT_SECONDARY,
        )
        g_desc.setWordWrap(True)
        g_layout.addWidget(g_desc)

        layout.addWidget(guidance)


class _ScanRow(QWidget):
    """One row in the Active Network Scans table."""

    STATUS_STYLES = {
        "scanning": ("↻ Scanning", T.ACCENT_GREEN),
        "queued":   ("⊙ Queued",   T.ACCENT_ORANGE),
        "finished": ("✓ Finished", T.TEXT_MUTED),
    }
    ACTION_ICONS = {
        "scanning": "⊗",
        "queued":   "🗑",
        "finished": "↺",
    }

    def __init__(self, status: str, cidr: str, miners: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("background: transparent;")

        row = QHBoxLayout(self)
        row.setContentsMargins(20, 10, 20, 10)
        row.setSpacing(0)

        label_text, color = self.STATUS_STYLES.get(status, ("—", T.TEXT_MUTED))
        action_icon = self.ACTION_ICONS.get(status, "—")

        # STATUS (40% of row)
        status_lbl = _label(label_text, 12, 500, color)
        status_lbl.setFixedWidth(120)
        row.addWidget(status_lbl)

        # SUBNET CIDR (flexible)
        cidr_lbl = _label(cidr, 12, 400, T.TEXT_PRIMARY if status != "finished" else T.TEXT_MUTED)
        row.addWidget(cidr_lbl, 1)

        # MINERS
        miners_lbl = _label(miners, 12, 400, T.TEXT_SECONDARY)
        miners_lbl.setFixedWidth(70)
        miners_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(miners_lbl)

        # ACTIONS
        action_btn = QPushButton(action_icon)
        action_btn.setFixedSize(32, 32)
        action_btn.setFont(make_font(14, 400))
        action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        action_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {T.TEXT_MUTED};
                border: none;
                border-radius: 4px;
            }}
            QPushButton:hover {{
                background: {T.BG_SIDEBAR};
                color: {T.TEXT_PRIMARY};
            }}
        """)
        row.addWidget(action_btn)


class _ActiveScansCard(QWidget):
    """Right panel — table of active/queued/finished subnet scans."""

    ROWS = [
        ("scanning", "192.168.1.0/24",  "0/254"),
        ("queued",   "10.0.1.0/24",     "--"),
        ("queued",   "192.168.2.0/24",  "--"),
        ("finished", "172.16.0.0/24",   "0"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("scans_card")
        self.setStyleSheet(f"""
            QWidget#scans_card {{
                background: {T.BG_CARD};
                border: 1px solid {T.BORDER_DEFAULT};
                border-radius: 8px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Card header ───────────────────────────────────────────────────────
        hdr = QWidget()
        hdr.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        hdr.setObjectName("scans_hdr")
        hdr.setStyleSheet("QWidget#scans_hdr { background: transparent; }")
        hdr_layout = QHBoxLayout(hdr)
        hdr_layout.setContentsMargins(20, 16, 20, 12)

        hdr_layout.addWidget(_label("Active Network Scans", 15, 700, T.TEXT_PRIMARY))
        hdr_layout.addStretch()

        badge = QWidget()
        badge.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        badge.setObjectName("badge")
        badge.setStyleSheet("""
            QWidget#badge {
                background: #DCFCE7;
                border-radius: 10px;
            }
        """)
        b_layout = QHBoxLayout(badge)
        b_layout.setContentsMargins(10, 4, 10, 4)
        b_layout.setSpacing(5)
        dot = _label("●", 8, 400, T.ACCENT_GREEN)
        b_layout.addWidget(dot)
        b_layout.addWidget(_label("2 ACTIVE", 11, 700, T.ACCENT_GREEN))
        hdr_layout.addWidget(badge)

        layout.addWidget(hdr)
        layout.addWidget(_hdivider())

        # ── Column headers ────────────────────────────────────────────────────
        col_hdr = QWidget()
        col_hdr.setStyleSheet("background: transparent;")
        ch_layout = QHBoxLayout(col_hdr)
        ch_layout.setContentsMargins(20, 8, 20, 8)
        ch_layout.setSpacing(0)

        for text, width, stretch in [
            ("STATUS",      120, 0),
            ("SUBNET CIDR", 0,   1),
            ("MINERS",      70,  0),
            ("ACTIONS",     32,  0),
        ]:
            lbl = _label(text, 11, 600, T.TEXT_MUTED)
            if width:
                lbl.setFixedWidth(width)
            if text == "MINERS":
                lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            ch_layout.addWidget(lbl, stretch)

        layout.addWidget(col_hdr)
        layout.addWidget(_hdivider())

        # ── Rows ──────────────────────────────────────────────────────────────
        for status, cidr, miners in self.ROWS:
            layout.addWidget(_ScanRow(status, cidr, miners))
            layout.addWidget(_hdivider())

        layout.addStretch()

        # ── Footer button ─────────────────────────────────────────────────────
        logs_btn = QPushButton("VIEW SYSTEM LOGS")
        logs_btn.setFont(make_font(11, 600))
        logs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        logs_btn.setFixedHeight(40)
        logs_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {T.TEXT_SECONDARY};
                border: none;
                border-top: 1px solid {T.BORDER_DEFAULT};
                border-bottom-left-radius: 8px;
                border-bottom-right-radius: 8px;
                letter-spacing: 0.5px;
            }}
            QPushButton:hover {{
                background: {T.BG_SIDEBAR};
            }}
        """)
        layout.addWidget(logs_btn)


# ── Page ──────────────────────────────────────────────────────────────────────

class DiscoveryPage(QWidget):
    """Step 2: subnet discovery."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background: {T.BG_WINDOW};")

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent;")

        content = QWidget()
        content.setStyleSheet(f"background: {T.BG_WINDOW};")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(T.CONTENT_PADDING, T.CONTENT_PADDING, T.CONTENT_PADDING, T.CONTENT_PADDING)
        layout.setSpacing(16)

        # ── Heading ───────────────────────────────────────────────────────────
        layout.addWidget(_label("Discover Miners", 22, 700, T.TEXT_PRIMARY))

        status_row = QHBoxLayout()
        status_row.setSpacing(6)
        dot = _label("●", 10, 400, T.ACCENT_ORANGE)
        status_row.addWidget(dot)
        status_row.addWidget(_label(
            "0 miners discovered across 0 subnets. Network scan active.",
            13, 400, T.TEXT_SECONDARY,
        ))
        status_row.addStretch()
        layout.addLayout(status_row)

        # ── Alert card ────────────────────────────────────────────────────────
        layout.addWidget(_AlertCard())

        # ── Progress card ─────────────────────────────────────────────────────
        layout.addWidget(_ProgressCard())

        # ── Two-column layout ─────────────────────────────────────────────────
        two_col = QHBoxLayout()
        two_col.setSpacing(16)

        left = _SubnetEntryCard()
        left.setFixedWidth(310)
        two_col.addWidget(left)

        two_col.addWidget(_ActiveScansCard(), 1)

        layout.addLayout(two_col)
        layout.addStretch()

        scroll.setWidget(content)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
