"""Logs page — real-time in-app log viewer.

Architecture
────────────
``GuiLogHandler`` subclasses ``logging.Handler`` and owns a ``_LogBridge``
QObject.  Background threads (scheduler, WebSocket, collectors) call
``handler.emit()`` as normal; the bridge's ``pyqtSignal(object)`` queues
each ``LogRecord`` to the Qt main thread automatically via Qt's cross-thread
queued-connection mechanism — no manual locking required.

On the main thread, each record is:
  1. Appended to a ``collections.deque(maxlen=2000)`` for in-memory replay.
  2. Formatted as an HTML line and appended to a ``QTextEdit`` (if it passes
     the active level filter and search text).

``document().setMaximumBlockCount`` bounds rendered memory independently of
the deque, so the view prunes itself without manual management.

Level filter buttons and the search box rebuild the view from the deque on
change, so no records are lost when the filter is adjusted.
"""

from __future__ import annotations

import html as _html
import logging
import shutil
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from wright_telemetry.gui.fonts import make_font
from wright_telemetry.gui import theme as T

# ── Constants ─────────────────────────────────────────────────────────────────

_MAX_RECORDS = 2_000   # in-memory ring buffer size
_MAX_BLOCKS  = 2_000   # QTextEdit rendered block limit (auto-prunes oldest)

# Namespace prefix — only records from this logger tree reach the GUI.
# Keeps out noise from requests, urllib3, websockets, etc.
_LOG_NAMESPACE = "wright_telemetry"

# ── Level colours (light theme) ───────────────────────────────────────────────

_LEVEL_COLORS: dict[str, str] = {
    "DEBUG":    T.TEXT_MUTED,     # subdued grey  — low priority
    "INFO":     T.ACCENT_BLUE,    # blue          — informational
    "WARNING":  T.ACCENT_ORANGE,  # orange        — caution
    "ERROR":    T.ACCENT_RED,     # red           — error
    "CRITICAL": T.ACCENT_RED,     # red bold      — fatal
}

# Mapping used by the level-filter pills
_LEVEL_NUMS: dict[Optional[str], int] = {
    None:       logging.DEBUG,
    "DEBUG":    logging.DEBUG,
    "INFO":     logging.INFO,
    "WARNING":  logging.WARNING,
    "ERROR":    logging.ERROR,
}


# ── Thread-safe bridge ────────────────────────────────────────────────────────

class _LogBridge(QObject):
    """Minimal QObject wrapper so ``pyqtSignal`` can queue across threads.

    Emitting from a non-main thread automatically creates a queued connection
    to any slot connected from the main thread — the record lands safely on
    the main thread without locks.
    """
    record_ready = pyqtSignal(object)  # object = logging.LogRecord


class GuiLogHandler(logging.Handler):
    """Logging handler that posts ``LogRecord`` objects to a Qt signal.

    Attach to the root logger once; any subsequent ``configure_logging()``
    calls clear and re-add handlers, so callers should re-attach if needed.
    """

    def __init__(self) -> None:
        super().__init__()
        self.bridge = _LogBridge()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.bridge.record_ready.emit(record)
        except Exception:
            self.handleError(record)


# ── Namespace filter ──────────────────────────────────────────────────────────

class _NamespaceFilter(logging.Filter):
    """Allow only records from ``prefix`` and its children."""

    def __init__(self, prefix: str) -> None:
        super().__init__()
        self._prefix = prefix
        self._prefix_dot = prefix + "."

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        return (
            record.name == self._prefix
            or record.name.startswith(self._prefix_dot)
        )


# ── Level filter pill button ──────────────────────────────────────────────────

class _LevelPill(QPushButton):
    """Small toggle button for a log level (or "ALL")."""

    def __init__(self, level: Optional[str], parent=None) -> None:
        super().__init__("ALL" if level is None else level, parent)
        self.level: Optional[str] = level
        self._active = False
        self.setFixedHeight(26)
        self.setFont(make_font(11, 600))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh()

    def setActive(self, active: bool) -> None:
        self._active = active
        self._refresh()

    def _refresh(self) -> None:
        accent = _LEVEL_COLORS.get(self.level or "", T.ACCENT_BLUE)
        if self.level is None:
            accent = T.ACCENT_BLUE
        if self._active:
            self.setStyleSheet(f"""
                QPushButton {{
                    background: {accent};
                    color: white;
                    border: 1px solid {accent};
                    border-radius: 5px;
                    padding: 0 10px;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: {accent};
                    border: 1px solid {T.BORDER_DEFAULT};
                    border-radius: 5px;
                    padding: 0 10px;
                }}
                QPushButton:hover {{
                    background: {T.BG_CARD_HOVER};
                }}
            """)


# ── Logs page ─────────────────────────────────────────────────────────────────

class LogsPage(QWidget):
    """Real-time log viewer page.

    Attaches a ``GuiLogHandler`` to the root ``logging.Logger`` on
    construction and removes it on ``closeEvent``.  Records are rendered
    as colour-coded HTML lines in a read-only ``QTextEdit``.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background: {T.BG_WINDOW};")

        # ── Internal state ────────────────────────────────────────────────────
        self._records: deque[logging.LogRecord] = deque(maxlen=_MAX_RECORDS)
        self._min_level = logging.DEBUG
        self._search_txt = ""
        self._auto_scroll = True

        # Debounce search input so we don't rebuild on every keystroke
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self._rebuild)

        # ── Logging handler ───────────────────────────────────────────────────
        self._handler = GuiLogHandler()
        self._handler.setLevel(logging.DEBUG)
        self._handler.addFilter(_NamespaceFilter(_LOG_NAMESPACE))
        # QueuedConnection is implicit for cross-thread signals, but we make
        # it explicit for clarity and to ensure main-thread delivery.
        self._handler.bridge.record_ready.connect(
            self._on_record, Qt.ConnectionType.QueuedConnection
        )
        logging.getLogger().addHandler(self._handler)

        # ── Layout ────────────────────────────────────────────────────────────
        root = QVBoxLayout(self)
        root.setContentsMargins(
            T.CONTENT_PADDING, T.CONTENT_PADDING,
            T.CONTENT_PADDING, T.CONTENT_PADDING,
        )
        root.setSpacing(16)

        # Page heading row
        heading_row = QHBoxLayout()
        heading_row.setSpacing(0)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_lbl = QLabel("Logs")
        title_lbl.setFont(make_font(*T.FONT_PAGE_HEADING))
        title_lbl.setStyleSheet(f"color: {T.TEXT_PRIMARY}; background: transparent;")
        desc_lbl = QLabel("Real-time collector activity from all background services")
        desc_lbl.setFont(make_font(*T.FONT_PAGE_DESC))
        desc_lbl.setStyleSheet(f"color: {T.TEXT_SECONDARY}; background: transparent;")
        title_col.addWidget(title_lbl)
        title_col.addWidget(desc_lbl)
        heading_row.addLayout(title_col)
        heading_row.addStretch()

        self._clear_btn = self._make_ghost_btn("Clear")
        self._clear_btn.clicked.connect(self._on_clear)
        self._download_btn = self._make_ghost_btn("⬇  Download Log")
        self._download_btn.clicked.connect(self._on_download)

        heading_row.addWidget(self._clear_btn)
        heading_row.addSpacing(8)
        heading_row.addWidget(self._download_btn)
        root.addLayout(heading_row)

        # Toolbar card: level pills + search + auto-scroll toggle
        toolbar = QWidget()
        toolbar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        toolbar.setObjectName("log_toolbar")
        toolbar.setStyleSheet(f"""
            QWidget#log_toolbar {{
                background: {T.BG_CARD};
                border: 1px solid {T.BORDER_DEFAULT};
                border-radius: 8px;
            }}
        """)
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(12, 8, 12, 8)
        tb_layout.setSpacing(6)

        # Level filter pills
        self._pills: dict[Optional[str], _LevelPill] = {}
        for lvl in [None, "DEBUG", "INFO", "WARNING", "ERROR"]:
            pill = _LevelPill(lvl)
            pill.clicked.connect(lambda _, l=lvl: self._set_level(l))
            tb_layout.addWidget(pill)
            self._pills[lvl] = pill
        self._pills[None].setActive(True)

        # Vertical separator
        tb_layout.addSpacing(4)
        vsep = QFrame()
        vsep.setFrameShape(QFrame.Shape.VLine)
        vsep.setFixedWidth(1)
        vsep.setFixedHeight(20)
        vsep.setStyleSheet(f"background: {T.BORDER_DEFAULT}; border: none;")
        tb_layout.addWidget(vsep)
        tb_layout.addSpacing(4)

        # Search
        search_icon = QLabel("⌕")
        search_icon.setFont(make_font(14, 400))
        search_icon.setStyleSheet(
            f"color: {T.TEXT_MUTED}; background: transparent; border: none;"
        )
        tb_layout.addWidget(search_icon)

        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Filter messages…")
        self._search_box.setFont(make_font(12, 400))
        self._search_box.setFixedHeight(26)
        self._search_box.setStyleSheet(f"""
            QLineEdit {{
                background: {T.BG_WINDOW};
                color: {T.TEXT_PRIMARY};
                border: 1px solid {T.BORDER_DEFAULT};
                border-radius: 4px;
                padding: 0 8px;
            }}
            QLineEdit:focus {{
                border-color: {T.ACCENT_BLUE};
                outline: none;
            }}
        """)
        self._search_box.textChanged.connect(self._on_search_changed)
        tb_layout.addWidget(self._search_box, 1)

        tb_layout.addSpacing(4)

        # Auto-scroll toggle
        self._autoscroll_btn = QPushButton("↓ Auto-scroll")
        self._autoscroll_btn.setFont(make_font(11, 500))
        self._autoscroll_btn.setFixedHeight(26)
        self._autoscroll_btn.setCheckable(True)
        self._autoscroll_btn.setChecked(True)
        self._autoscroll_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._autoscroll_btn.toggled.connect(self._on_autoscroll_toggled)
        self._refresh_autoscroll_style(True)
        tb_layout.addWidget(self._autoscroll_btn)

        root.addWidget(toolbar)

        # Log area
        self._log_area = QTextEdit()
        self._log_area.setReadOnly(True)
        self._log_area.document().setMaximumBlockCount(_MAX_BLOCKS)
        # Remove default paragraph margins so lines sit flush against each other
        self._log_area.document().setDefaultStyleSheet(
            "body { margin: 0; padding: 0; } "
            "p { margin: 0; padding: 1px 0; line-height: 1.35; }"
        )
        font = make_font(11, 400)
        font.setFamily("Menlo")
        font.setStyleHint(font.StyleHint.Monospace)
        self._log_area.setFont(font)
        self._log_area.setStyleSheet(f"""
            QTextEdit {{
                background: {T.BG_CARD};
                color: {T.TEXT_PRIMARY};
                border: 1px solid {T.BORDER_DEFAULT};
                border-radius: 8px;
                padding: 10px 14px;
                selection-background-color: {T.ACCENT_BLUE};
                selection-color: white;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 8px;
                margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: {T.BORDER_DEFAULT};
                border-radius: 4px;
                min-height: 24px;
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0;
            }}
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
        """)
        root.addWidget(self._log_area, 1)

        # Status bar
        status_bar = QHBoxLayout()
        status_bar.setContentsMargins(2, 0, 2, 0)
        self._status_lbl = QLabel("No entries yet")
        self._status_lbl.setFont(make_font(11, 400))
        self._status_lbl.setStyleSheet(
            f"color: {T.TEXT_MUTED}; background: transparent;"
        )
        status_bar.addWidget(self._status_lbl)
        status_bar.addStretch()
        self._updated_lbl = QLabel("")
        self._updated_lbl.setFont(make_font(11, 400))
        self._updated_lbl.setStyleSheet(
            f"color: {T.TEXT_MUTED}; background: transparent;"
        )
        status_bar.addWidget(self._updated_lbl)
        root.addLayout(status_bar)

    # ── Factory helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _make_ghost_btn(label: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setFont(make_font(12, 500))
        btn.setFixedHeight(32)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {T.BG_CARD};
                color: {T.TEXT_SECONDARY};
                border: 1px solid {T.BORDER_DEFAULT};
                border-radius: 6px;
                padding: 0 14px;
            }}
            QPushButton:hover {{
                background: {T.BG_CARD_HOVER};
                color: {T.TEXT_PRIMARY};
                border-color: {T.TEXT_MUTED};
            }}
        """)
        return btn

    # ── HTML formatting ───────────────────────────────────────────────────────

    def _format_html(self, record: logging.LogRecord) -> str:
        """Return a single-line HTML string for one ``LogRecord``."""
        ts    = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        level = record.levelname
        color = _LEVEL_COLORS.get(level, T.TEXT_PRIMARY)

        # Keep the last two dotted components of the logger name, max 28 chars
        parts = record.name.split(".")
        name  = ".".join(parts[-2:]) if len(parts) > 1 else record.name
        if len(name) > 28:
            name = name[:26] + "…"

        try:
            msg = _html.escape(record.getMessage())
        except Exception:
            msg = ""

        # CRITICAL gets a subtle red background badge
        if level == "CRITICAL":
            level_html = (
                f'<span style="color:white;font-weight:700;background:{T.ACCENT_RED};"'
                f'>&nbsp;{_html.escape(level)}&nbsp;</span>'
            )
        else:
            level_html = (
                f'<span style="color:{color};font-weight:600;">{_html.escape(level)}</span>'
            )

        sep = f'<span style="color:{T.BORDER_DEFAULT};">&nbsp;·&nbsp;</span>'

        return (
            f'<span style="color:{T.TEXT_MUTED};">{ts}</span>'
            f'{sep}'
            f'{level_html}'
            f'{sep}'
            f'<span style="color:{T.TEXT_SECONDARY};">{_html.escape(name)}</span>'
            f'{sep}'
            f'<span style="color:{T.TEXT_PRIMARY};">{msg}</span>'
        )

    # ── Filter helpers ────────────────────────────────────────────────────────

    def _passes_filter(self, record: logging.LogRecord) -> bool:
        if record.levelno < self._min_level:
            return False
        if self._search_txt:
            haystack = record.getMessage().lower() + " " + record.name.lower()
            if self._search_txt not in haystack:
                return False
        return True

    def _rebuild(self) -> None:
        """Clear the view and re-render all stored records that pass the filter."""
        self._log_area.clear()
        shown = 0
        for rec in self._records:
            if self._passes_filter(rec):
                self._log_area.append(self._format_html(rec))
                shown += 1
        if self._auto_scroll:
            sb = self._log_area.verticalScrollBar()
            sb.setValue(sb.maximum())
        self._update_status(shown)

    # ── Status bar ────────────────────────────────────────────────────────────

    def _update_status(self, shown: Optional[int] = None) -> None:
        total = len(self._records)
        if total == 0:
            self._status_lbl.setText("No entries yet")
            return
        if shown is None or shown == total:
            self._status_lbl.setText(
                f"{total} entr{'y' if total == 1 else 'ies'}"
            )
        else:
            self._status_lbl.setText(
                f"{shown} of {total} entr{'y' if total == 1 else 'ies'} shown"
            )

    # ── Auto-scroll toggle style ──────────────────────────────────────────────

    def _refresh_autoscroll_style(self, on: bool) -> None:
        if on:
            self._autoscroll_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {T.ACCENT_BLUE};
                    color: white;
                    border: 1px solid {T.ACCENT_BLUE};
                    border-radius: 4px;
                    padding: 0 10px;
                }}
            """)
        else:
            self._autoscroll_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: {T.TEXT_MUTED};
                    border: 1px solid {T.BORDER_DEFAULT};
                    border-radius: 4px;
                    padding: 0 10px;
                }}
                QPushButton:hover {{
                    background: {T.BG_CARD_HOVER};
                }}
            """)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_record(self, record: logging.LogRecord) -> None:
        """Receive a new ``LogRecord`` on the main thread and update the view."""
        self._records.append(record)
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        self._updated_lbl.setText(f"Updated {ts}")

        if self._passes_filter(record):
            self._log_area.append(self._format_html(record))
            if self._auto_scroll:
                sb = self._log_area.verticalScrollBar()
                sb.setValue(sb.maximum())

        self._update_status()

    def _set_level(self, level: Optional[str]) -> None:
        for k, pill in self._pills.items():
            pill.setActive(k == level)
        self._min_level = _LEVEL_NUMS.get(level, logging.DEBUG)
        self._rebuild()

    def _on_search_changed(self, text: str) -> None:
        self._search_txt = text.lower().strip()
        self._search_timer.start()

    def _on_autoscroll_toggled(self, checked: bool) -> None:
        self._auto_scroll = checked
        self._refresh_autoscroll_style(checked)
        if checked:
            sb = self._log_area.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _on_clear(self) -> None:
        self._records.clear()
        self._log_area.clear()
        self._status_lbl.setText("No entries yet")
        self._updated_lbl.setText("")

    def _on_download(self) -> None:
        """Save the on-disk ``collector.log`` to a user-chosen location."""
        from wright_telemetry.config import CONFIG_DIR
        src = CONFIG_DIR / "collector.log"

        if not src.exists():
            QMessageBox.warning(
                self,
                "Log file not found",
                f"No log file found at:\n{src}\n\nThe collector may not have started yet.",
            )
            return

        # Suggest a timestamped filename so downloads don't overwrite each other
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"wright-collector-{ts}.log"

        dest, _ = QFileDialog.getSaveFileName(
            self,
            "Save log file",
            str(Path.home() / "Downloads" / default_name),
            "Log files (*.log);;Text files (*.txt);;All files (*)",
        )
        if not dest:
            return  # user cancelled

        try:
            shutil.copy2(str(src), dest)
        except OSError as exc:
            QMessageBox.critical(
                self,
                "Download failed",
                f"Could not save the log file:\n{exc}",
            )

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:  # noqa: N802
        logging.getLogger().removeHandler(self._handler)
        super().closeEvent(event)
