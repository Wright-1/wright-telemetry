"""Overview page — agent status, system health, miner topology, portal CTA."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from wright_telemetry.gui.fonts import make_font
from wright_telemetry.gui import theme as T


# ── Helpers ───────────────────────────────────────────────────────────────────

def _lbl(text: str, size: int, weight: int, color: str,
         wrap: bool = False) -> QLabel:
    l = QLabel(text)
    l.setFont(make_font(size, weight))
    l.setStyleSheet(f"color: {color}; background: transparent;")
    if wrap:
        l.setWordWrap(True)
    return l


def _card(name: str) -> QWidget:
    w = QWidget()
    w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    w.setObjectName(name)
    w.setStyleSheet(f"""
        QWidget#{name} {{
            background: {T.BG_CARD};
            border: 1px solid {T.BORDER_DEFAULT};
            border-radius: 8px;
        }}
    """)
    return w


def _hdiv() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setFixedHeight(1)
    f.setStyleSheet(f"background: {T.BORDER_DEFAULT}; border: none;")
    return f


def _vdiv() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.VLine)
    f.setFixedWidth(1)
    f.setStyleSheet(f"background: {T.BORDER_DEFAULT}; border: none;")
    return f


# ── Update banner ─────────────────────────────────────────────────────────────

class _UpdateBanner(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("update_banner")
        self.setStyleSheet("""
            QWidget#update_banner {
                background: #FFF8F5;
                border: 1px solid #FECAA0;
                border-radius: 8px;
            }
        """)
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 12, 16, 12)
        row.setSpacing(10)

        icon = _lbl("⚠", 14, 600, T.ACCENT_ORANGE)
        icon.setFixedWidth(20)
        row.addWidget(icon)

        msg = _lbl(
            "Agent version is out of date. Please update to the latest version "
            "(v0.8.1) to ensure stability.",
            13, 400, T.TEXT_PRIMARY,
        )
        msg.setWordWrap(True)
        row.addWidget(msg, 1)

        btn = QPushButton("Update Now")
        btn.setFont(make_font(12, 600))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {T.TEXT_PRIMARY};
                color: #FFFFFF;
                border: none;
                border-radius: 6px;
                padding: 8px 18px;
            }}
            QPushButton:hover {{ background: #2A2D35; }}
        """)
        row.addWidget(btn)


# ── Agent Details card ────────────────────────────────────────────────────────

class _AgentDetailsCard(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        card = _card("agent_card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(14)

        # Header row
        hdr = QHBoxLayout()
        icon = _lbl("ⓘ", 14, 400, T.TEXT_SECONDARY)
        icon.setFixedWidth(20)
        hdr.addWidget(icon)
        hdr.addWidget(_lbl("Agent Details", 14, 700, T.TEXT_PRIMARY))
        hdr.addStretch()

        badge = QWidget()
        badge.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        badge.setObjectName("id_badge")
        badge.setStyleSheet(f"""
            QWidget#id_badge {{
                background: {T.BG_SIDEBAR};
                border: 1px solid {T.BORDER_DEFAULT};
                border-radius: 12px;
            }}
        """)
        b_row = QHBoxLayout(badge)
        b_row.setContentsMargins(10, 4, 10, 4)
        b_row.addWidget(_lbl("Instance ID: WTC-9921-X", 11, 500, T.TEXT_SECONDARY))
        hdr.addWidget(badge)
        layout.addLayout(hdr)

        layout.addWidget(_hdiv())

        # Three info columns
        cols = QHBoxLayout()
        cols.setSpacing(0)

        for label, value, stretch in [
            ("AGENT VERSION",  "v0.7.3",                  False),
            ("CUSTOMER NAME",  "John Doe",                 False),
            ("FACILITY / RACK","North Data Center – Rack 4", True),
        ]:
            col = QVBoxLayout()
            col.setSpacing(4)
            col.addWidget(_lbl(label, 10, 600, T.TEXT_MUTED))
            val = _lbl(value, 18, 700, T.TEXT_PRIMARY, wrap=True)
            col.addWidget(val)
            cell = QWidget()
            cell.setStyleSheet("background: transparent;")
            cell.setLayout(col)
            cols.addWidget(cell, 1 if stretch else 0)
            if label != "FACILITY / RACK":
                cols.addWidget(_vdiv())
                cols.addSpacing(20)

        layout.addLayout(cols)


# ── Operational Status card ───────────────────────────────────────────────────

class _OperationalStatusCard(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        card = _card("ops_card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(12)

        # Header
        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        hdr.addWidget(_lbl("⏱", 14, 400, T.ACCENT_GREEN))
        hdr.addWidget(_lbl("Operational Status", 14, 700, T.TEXT_PRIMARY))
        hdr.addStretch()
        layout.addLayout(hdr)

        layout.addWidget(_hdiv())

        layout.addWidget(_lbl("SYSTEM UPTIME", 10, 600, T.TEXT_MUTED))
        layout.addWidget(_lbl("14d 06h 12m", 28, 700, T.TEXT_PRIMARY))

        # Active badge
        status_row = QHBoxLayout()
        status_row.setSpacing(5)
        dot = _lbl("●", 10, 400, T.ACCENT_GREEN)
        status_row.addWidget(dot)
        status_row.addWidget(_lbl("Collector Engine Active", 12, 500, T.ACCENT_GREEN))
        status_row.addStretch()
        layout.addLayout(status_row)

        layout.addStretch()


# ── System Health card ────────────────────────────────────────────────────────

class _HealthItem(QWidget):
    def __init__(self, icon: str, icon_color: str, border_color: str,
                 title: str, desc: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("health_item")
        self.setStyleSheet(f"""
            QWidget#health_item {{
                background: {T.BG_WINDOW};
                border: 1px solid {T.BORDER_DEFAULT};
                border-left: 3px solid {border_color};
                border-radius: 6px;
            }}
        """)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(10)

        ic = _lbl(icon, 14, 600, icon_color)
        ic.setFixedWidth(20)
        row.addWidget(ic)

        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(_lbl(title, 12, 600, T.TEXT_PRIMARY))
        text.addWidget(_lbl(desc, 11, 400, T.TEXT_SECONDARY, wrap=True))
        row.addLayout(text, 1)


class _SystemHealthCard(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        card = _card("health_card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(10)

        # Header
        hdr = QHBoxLayout()
        hdr.addWidget(_lbl("System Health", 14, 700, T.TEXT_PRIMARY))
        hdr.addStretch()

        warn_badge = QWidget()
        warn_badge.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        warn_badge.setObjectName("warn_badge")
        warn_badge.setStyleSheet("""
            QWidget#warn_badge {
                background: #FFF3E0;
                border-radius: 10px;
            }
        """)
        wb = QHBoxLayout(warn_badge)
        wb.setContentsMargins(10, 4, 10, 4)
        wb.addWidget(_lbl("1 Active Warning", 11, 600, T.ACCENT_ORANGE))
        hdr.addWidget(warn_badge)
        layout.addLayout(hdr)

        layout.addWidget(_hdiv())

        layout.addWidget(_HealthItem(
            "⚠", T.ACCENT_ORANGE, T.ACCENT_ORANGE,
            "API Connection Latency",
            "High latency detected in us-east-1 relay node (142ms).",
        ))
        layout.addWidget(_HealthItem(
            "✓", T.ACCENT_GREEN, T.ACCENT_GREEN,
            "Local Buffer Integrity",
            "Journal logs are rotating successfully.",
        ))
        layout.addStretch()


# ── Miner Topology card ───────────────────────────────────────────────────────

class _FirmwareBar(QWidget):
    """Segmented bar showing firmware distribution."""

    SEGMENTS = [
        ("24", T.TEXT_PRIMARY,  "#111318"),   # Braiins — darkest
        ("12", "#555C68",       "#555C68"),   # Vnish — mid gray
        ("20", "#C8CDD5",       "#C8CDD5"),   # LuxOS — light gray
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)

        for i, (count, text_color, bg) in enumerate(self.SEGMENTS):
            seg = QWidget()
            seg.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            seg.setObjectName(f"seg{i}")

            radius = ""
            if i == 0:
                radius = "border-top-left-radius: 5px; border-bottom-left-radius: 5px;"
            elif i == len(self.SEGMENTS) - 1:
                radius = "border-top-right-radius: 5px; border-bottom-right-radius: 5px;"

            seg.setStyleSheet(f"""
                QWidget#seg{i} {{
                    background: {bg};
                    {radius}
                }}
            """)
            seg.setFixedHeight(28)

            lbl = QLabel(count)
            lbl.setFont(make_font(12, 600))
            lbl.setStyleSheet(f"color: {'white' if i == 0 else T.BG_CARD}; background: transparent;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

            seg_layout = QHBoxLayout(seg)
            seg_layout.setContentsMargins(0, 0, 0, 0)
            seg_layout.addWidget(lbl)

            row.addWidget(seg, int(count))


class _MinerTopologyCard(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        card = _card("topo_card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(12)

        # Header
        hdr = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_col.addWidget(_lbl("Miner Topology", 14, 700, T.TEXT_PRIMARY))
        title_col.addWidget(_lbl("Network-wide hardware distribution", 11, 400, T.TEXT_MUTED))
        hdr.addLayout(title_col)
        hdr.addStretch()

        count_col = QVBoxLayout()
        count_col.setAlignment(Qt.AlignmentFlag.AlignRight)
        count_col.addWidget(_lbl("56", 28, 700, T.TEXT_PRIMARY))
        total_lbl = _lbl("TOTAL MINERS", 10, 600, T.TEXT_MUTED)
        total_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        count_col.addWidget(total_lbl)
        hdr.addLayout(count_col)
        layout.addLayout(hdr)

        layout.addWidget(_hdiv())

        # Firmware distribution bar
        fw_hdr = QHBoxLayout()
        fw_hdr.addWidget(_lbl("Firmware Distribution", 12, 600, T.TEXT_PRIMARY))
        fw_hdr.addStretch()
        fw_hdr.addWidget(_lbl("Braiins OS / Vnish / LuxOS", 11, 400, T.TEXT_MUTED))
        layout.addLayout(fw_hdr)

        layout.addWidget(_FirmwareBar())

        # Legend
        legend = QHBoxLayout()
        legend.setSpacing(0)
        for dot_color, label, count in [
            ("#111318", "Braiins OS", "24 nodes"),
            ("#555C68", "Vnish",      "12 nodes"),
            ("#C8CDD5", "LuxOS",      "20 nodes"),
        ]:
            item = QHBoxLayout()
            item.setSpacing(6)
            dot = _lbl("●", 10, 400, dot_color)
            dot.setFixedWidth(14)
            item.addWidget(dot)
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addWidget(_lbl(label, 12, 600, T.TEXT_PRIMARY))
            col.addWidget(_lbl(count, 11, 400, T.TEXT_MUTED))
            item.addLayout(col)
            cell = QWidget()
            cell.setStyleSheet("background: transparent;")
            cell.setLayout(item)
            legend.addWidget(cell, 1)
        layout.addLayout(legend)


# ── Portal CTA banner ─────────────────────────────────────────────────────────

class _PortalBanner(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("portal_banner")
        self.setStyleSheet(f"""
            QWidget#portal_banner {{
                background: #E8E9EB;
                border-radius: 8px;
            }}
        """)
        self.setFixedHeight(200)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = _lbl("Further Analytics Online", 20, 700, T.TEXT_PRIMARY)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        layout.addSpacing(6)

        sub = _lbl("Real-time visualization of your fleet.", 13, 400, T.TEXT_SECONDARY)
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(sub)

        layout.addSpacing(10)

        link = _lbl("portal.wrightfan.com  ↗", 13, 500, T.TEXT_PRIMARY)
        link.setAlignment(Qt.AlignmentFlag.AlignCenter)
        link.setCursor(Qt.CursorShape.PointingHandCursor)
        link.setStyleSheet(
            f"color: {T.TEXT_PRIMARY}; background: transparent; "
            "text-decoration: underline;"
        )
        layout.addWidget(link)


# ── Page ──────────────────────────────────────────────────────────────────────

class OverviewPage(QWidget):
    """Step 3: agent overview."""

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
        layout.setContentsMargins(T.CONTENT_PADDING, T.CONTENT_PADDING,
                                  T.CONTENT_PADDING, T.CONTENT_PADDING)
        layout.setSpacing(16)

        # Update banner
        layout.addWidget(_UpdateBanner())

        # Row 1: Agent Details + Operational Status
        row1 = QHBoxLayout()
        row1.setSpacing(16)
        row1.addWidget(_AgentDetailsCard(), 3)
        row1.addWidget(_OperationalStatusCard(), 2)
        layout.addLayout(row1)

        # Row 2: System Health + Miner Topology
        row2 = QHBoxLayout()
        row2.setSpacing(16)
        row2.addWidget(_SystemHealthCard(), 2)
        row2.addWidget(_MinerTopologyCard(), 3)
        layout.addLayout(row2)

        # Portal CTA
        layout.addWidget(_PortalBanner())

        scroll.setWidget(content)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
