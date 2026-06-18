"""Overview page — live agent status, facility/customer details, miner count."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt, QTimer
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

if TYPE_CHECKING:
    from wright_telemetry.gui.engine import ScanningEngine

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

        # Header
        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        hdr.addWidget(_lbl("ⓘ", 14, 400, T.TEXT_SECONDARY))
        hdr.addWidget(_lbl("Agent Details", 14, 700, T.TEXT_PRIMARY))
        hdr.addStretch()
        layout.addLayout(hdr)
        layout.addWidget(_hdiv())

        # Three info columns
        cols = QHBoxLayout()
        cols.setSpacing(0)

        # Agent version — static
        ver_col = QVBoxLayout()
        ver_col.setSpacing(4)
        ver_col.addWidget(_lbl("AGENT VERSION", 10, 600, T.TEXT_MUTED))
        try:
            from wright_telemetry import __version__ as _ver
        except ImportError:
            _ver = "—"
        ver_col.addWidget(_lbl(_ver, 18, 700, T.TEXT_PRIMARY))
        ver_w = QWidget()
        ver_w.setStyleSheet("background: transparent;")
        ver_w.setLayout(ver_col)
        cols.addWidget(ver_w)
        cols.addWidget(_vdiv())
        cols.addSpacing(20)

        # Customer name — live
        cust_col = QVBoxLayout()
        cust_col.setSpacing(4)
        cust_col.addWidget(_lbl("CUSTOMER NAME", 10, 600, T.TEXT_MUTED))
        self._customer_lbl = _lbl("Loading…", 18, 700, T.TEXT_MUTED)
        cust_col.addWidget(self._customer_lbl)
        cust_w = QWidget()
        cust_w.setStyleSheet("background: transparent;")
        cust_w.setLayout(cust_col)
        cols.addWidget(cust_w)
        cols.addWidget(_vdiv())
        cols.addSpacing(20)

        # Facility — live
        fac_col = QVBoxLayout()
        fac_col.setSpacing(4)
        fac_col.addWidget(_lbl("FACILITY", 10, 600, T.TEXT_MUTED))
        self._facility_lbl = _lbl("Loading…", 18, 700, T.TEXT_MUTED, wrap=True)
        fac_col.addWidget(self._facility_lbl)
        fac_w = QWidget()
        fac_w.setStyleSheet("background: transparent;")
        fac_w.setLayout(fac_col)
        cols.addWidget(fac_w, 1)

        layout.addLayout(cols)

    def set_agent_info(self, data: dict) -> None:
        customer_name = data.get("customer_name") or "—"
        facility_name = data.get("facility_name") or "—"
        facility_code = data.get("facility_code") or ""

        self._customer_lbl.setText(customer_name)
        self._customer_lbl.setStyleSheet(
            f"color: {T.TEXT_PRIMARY}; background: transparent;"
        )
        display = f"{facility_name}"
        self._facility_lbl.setText(display)
        self._facility_lbl.setStyleSheet(
            f"color: {T.TEXT_PRIMARY}; background: transparent;"
        )

    def set_error(self, _err: str) -> None:
        for lbl in (self._customer_lbl, self._facility_lbl):
            lbl.setText("—")
            lbl.setStyleSheet(f"color: {T.TEXT_MUTED}; background: transparent;")


# ── Operational Status card ───────────────────────────────────────────────────

class _OperationalStatusCard(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._start_ts = time.time()

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
        self._uptime_lbl = _lbl("0d 00h 00m", 28, 700, T.TEXT_PRIMARY)
        layout.addWidget(self._uptime_lbl)

        # Engine / miner status row
        status_row = QHBoxLayout()
        status_row.setSpacing(5)
        self._status_dot = _lbl("●", 10, 400, T.ACCENT_GREEN)
        self._status_lbl = _lbl("Collector Engine Active", 12, 500, T.ACCENT_GREEN)
        status_row.addWidget(self._status_dot)
        status_row.addWidget(self._status_lbl)
        status_row.addStretch()
        layout.addLayout(status_row)

        layout.addSpacing(8)

        # Miner count
        layout.addWidget(_lbl("MINERS REPORTING", 10, 600, T.TEXT_MUTED))
        self._miner_count_lbl = _lbl("—", 22, 700, T.TEXT_PRIMARY)
        layout.addWidget(self._miner_count_lbl)

        layout.addStretch()

        # Refresh uptime every 60s
        self._uptime_timer = QTimer(self)
        self._uptime_timer.setInterval(60_000)
        self._uptime_timer.timeout.connect(self._refresh_uptime)
        self._uptime_timer.start()
        self._refresh_uptime()

    def _refresh_uptime(self) -> None:
        elapsed = int(time.time() - self._start_ts)
        d, rem = divmod(elapsed, 86400)
        h, rem = divmod(rem, 3600)
        m = rem // 60
        self._uptime_lbl.setText(f"{d}d {h:02d}h {m:02d}m")

    def set_miner_count(self, count: int) -> None:
        self._miner_count_lbl.setText(str(count))
        if count > 0:
            self._status_dot.setStyleSheet(
                f"color: {T.ACCENT_GREEN}; background: transparent;"
            )
            self._status_lbl.setText("Collector Engine Active")
            self._status_lbl.setStyleSheet(
                f"color: {T.ACCENT_GREEN}; background: transparent;"
            )
        else:
            self._status_dot.setStyleSheet(
                f"color: {T.TEXT_MUTED}; background: transparent;"
            )
            self._status_lbl.setText("No miners reporting")
            self._status_lbl.setStyleSheet(
                f"color: {T.TEXT_MUTED}; background: transparent;"
            )


# ── Miner Topology card ───────────────────────────────────────────────────────

_FW_COLORS = {
    "braiins": "#111318",
    "vnish":   "#555C68",
    "luxos":   "#C8CDD5",
    "bitmain": "#8B95A3",
    "sealminer": "#2D5A8E",
}
_FW_LABELS = {
    "braiins": "Braiins OS",
    "vnish":   "Vnish",
    "luxos":   "LuxOS",
    "bitmain": "Bitmain",
    "sealminer": "Sealminer",
}


class _FirmwareBar(QWidget):
    """Segmented bar showing live firmware distribution."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(2)
        self._segments: list[QWidget] = []
        self._render({})

    def update_distribution(self, breakdown: dict[str, int]) -> None:
        # Clear old segments
        while self._row.count():
            item = self._row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._segments.clear()
        self._render(breakdown)

    def _render(self, breakdown: dict[str, int]) -> None:
        items = [(k, v) for k, v in breakdown.items() if v > 0]
        if not items:
            # Placeholder empty bar
            placeholder = QWidget()
            placeholder.setStyleSheet(
                f"background: {T.BORDER_DEFAULT}; border-radius: 5px;"
            )
            placeholder.setFixedHeight(28)
            self._row.addWidget(placeholder, 1)
            return

        total = sum(v for _, v in items)
        for i, (key, count) in enumerate(items):
            bg = _FW_COLORS.get(key, "#9CA3AF")
            text_color = "white" if key in ("braiins", "vnish", "sealminer") else T.TEXT_PRIMARY

            radius = ""
            if i == 0:
                radius = "border-top-left-radius:5px; border-bottom-left-radius:5px;"
            if i == len(items) - 1:
                radius += "border-top-right-radius:5px; border-bottom-right-radius:5px;"

            name = f"seg_{key}"
            seg = QWidget()
            seg.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            seg.setObjectName(name)
            seg.setStyleSheet(f"QWidget#{name}{{background:{bg};{radius}}}")
            seg.setFixedHeight(28)

            lbl = QLabel(str(count))
            lbl.setFont(make_font(12, 600))
            lbl.setStyleSheet(f"color:{text_color}; background:transparent;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

            seg_layout = QHBoxLayout(seg)
            seg_layout.setContentsMargins(0, 0, 0, 0)
            seg_layout.addWidget(lbl)

            self._row.addWidget(seg, count)


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
        self._total_lbl = _lbl("—", 28, 700, T.TEXT_PRIMARY)
        self._total_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        count_col.addWidget(self._total_lbl)
        total_label = _lbl("TOTAL MINERS", 10, 600, T.TEXT_MUTED)
        total_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        count_col.addWidget(total_label)
        hdr.addLayout(count_col)
        layout.addLayout(hdr)

        layout.addWidget(_hdiv())

        fw_hdr = QHBoxLayout()
        fw_hdr.addWidget(_lbl("Firmware Distribution", 12, 600, T.TEXT_PRIMARY))
        fw_hdr.addStretch()
        self._fw_legend_hdr = _lbl("—", 11, 400, T.TEXT_MUTED)
        fw_hdr.addWidget(self._fw_legend_hdr)
        layout.addLayout(fw_hdr)

        self._fw_bar = _FirmwareBar()
        layout.addWidget(self._fw_bar)

        # Legend
        self._legend_layout = QHBoxLayout()
        self._legend_layout.setSpacing(0)
        self._legend_widgets: list[QWidget] = []
        layout.addLayout(self._legend_layout)

    def update_distribution(self, breakdown: dict[str, int]) -> None:
        total = sum(breakdown.values())
        self._total_lbl.setText(str(total) if total else "—")

        active = {k: v for k, v in breakdown.items() if v > 0}
        self._fw_legend_hdr.setText(
            "  /  ".join(_FW_LABELS.get(k, k) for k in active) if active else "—"
        )

        self._fw_bar.update_distribution(breakdown)

        # Rebuild legend
        for w in self._legend_widgets:
            self._legend_layout.removeWidget(w)
            w.deleteLater()
        self._legend_widgets.clear()

        for key, count in active.items():
            dot_color = _FW_COLORS.get(key, "#9CA3AF")
            item = QHBoxLayout()
            item.setSpacing(6)
            dot = _lbl("●", 10, 400, dot_color)
            dot.setFixedWidth(14)
            item.addWidget(dot)
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addWidget(_lbl(_FW_LABELS.get(key, key), 12, 600, T.TEXT_PRIMARY))
            col.addWidget(_lbl(f"{count} node{'s' if count != 1 else ''}", 11, 400, T.TEXT_MUTED))
            item.addLayout(col)
            cell = QWidget()
            cell.setStyleSheet("background: transparent;")
            cell.setLayout(item)
            self._legend_layout.addWidget(cell, 1)
            self._legend_widgets.append(cell)


# ── Portal CTA banner ─────────────────────────────────────────────────────────

class _PortalBanner(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("portal_banner")
        self.setStyleSheet("QWidget#portal_banner { background: #E8E9EB; border-radius: 8px; }")
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

        link = QLabel(
            f'<a href="https://portal.wrightfan.com" '
            f'style="color:{T.TEXT_PRIMARY}; text-decoration:underline;">'
            f'portal.wrightfan.com  ↗</a>'
        )
        link.setFont(make_font(13, 500))
        link.setStyleSheet("background: transparent;")
        link.setAlignment(Qt.AlignmentFlag.AlignCenter)
        link.setTextFormat(Qt.TextFormat.RichText)
        link.setOpenExternalLinks(True)
        layout.addWidget(link)


# ── Page ──────────────────────────────────────────────────────────────────────

class OverviewPage(QWidget):
    """Overview: live facility/customer details, miner count, firmware topology."""

    def __init__(self, engine: Optional["ScanningEngine"] = None, parent=None):
        super().__init__(parent)
        self._engine = engine
        self._fw_breakdown: dict[str, int] = {}
        self._subnet_breakdowns: dict[str, dict[str, int]] = {}  # per-subnet, prevents double-counting on rescan

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

        # Row 1: Agent Details (full width)
        self._agent_card = _AgentDetailsCard()
        layout.addWidget(self._agent_card)

        # Row 2: Operational Status + Miner Topology
        row2 = QHBoxLayout()
        row2.setSpacing(16)
        self._ops_card  = _OperationalStatusCard()
        self._topo_card = _MinerTopologyCard()
        row2.addWidget(self._ops_card,  2)
        row2.addWidget(self._topo_card, 3)
        layout.addLayout(row2)

        # Portal CTA
        layout.addWidget(_PortalBanner())

        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        # ── Connect engine signals ───────────────────────────────────────────
        if engine is not None:
            engine.signals.agent_info_loaded.connect(self._on_agent_info)
            engine.signals.agent_info_error.connect(self._on_agent_info_error)
            engine.signals.miner_count_changed.connect(self._on_miner_count)
            engine.signals.scan_complete.connect(self._on_scan_complete)
            engine.signals.poll_cycle_complete.connect(self._on_poll_cycle)

            # Populate from scan_manager state on open
            self._refresh_topology_from_engine()

    # ── Signal slots ─────────────────────────────────────────────────────────

    def _on_agent_info(self, data: dict) -> None:
        self._agent_card.set_agent_info(data)

    def _on_agent_info_error(self, err: str) -> None:
        self._agent_card.set_error(err)

    def _on_miner_count(self, count: int) -> None:
        self._ops_card.set_miner_count(count)

    def _on_scan_complete(self, subnet: str, miners_found: int, breakdown: object) -> None:
        bd: dict[str, int] = breakdown if isinstance(breakdown, dict) else {}
        # Replace this subnet's entry, then recompute aggregate from scratch
        # so that a rescan never double-counts the old values.
        self._subnet_breakdowns[subnet] = bd
        self._fw_breakdown = {}
        for sub_bd in self._subnet_breakdowns.values():
            for key, val in sub_bd.items():
                self._fw_breakdown[key] = self._fw_breakdown.get(key, 0) + val
        self._topo_card.update_distribution(self._fw_breakdown)

    def _on_poll_cycle(self) -> None:
        # miner_count_changed fires within each poll cycle and drives _on_miner_count;
        # nothing additional is needed here.
        pass

    def _refresh_topology_from_engine(self) -> None:
        if self._engine is None:
            return
        self._subnet_breakdowns = {}
        for result in self._engine.scan_manager.get_all_results():
            if result.status == "complete":
                self._subnet_breakdowns[result.subnet] = dict(result.firmware_breakdown)
        self._fw_breakdown = {}
        for sub_bd in self._subnet_breakdowns.values():
            for k, v in sub_bd.items():
                self._fw_breakdown[k] = self._fw_breakdown.get(k, 0) + v
        self._topo_card.update_distribution(self._fw_breakdown)
        # Miner count will be populated by miner_count_changed on the first poll cycle.
