"""Access Key provisioning page.

Shown on first launch (or whenever wright_api_key / facility_id are missing
from the saved config).  The user types their access key, we call
POST /api/v2/provision/redeem, and on success we write the returned
api_key + facility_id into the config and emit ``provisioned``.

The engine is NOT started until after provisioning completes — MainWindow
launches it when it receives the ``provisioned`` signal.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from wright_telemetry.gui.fonts import make_font
from wright_telemetry.gui import theme as T
from wright_telemetry.gui.widgets import PrimaryButton


_ERROR_MAP = {
    "Invalid access key":    "That access key wasn’t found. Double-check it and try again.",
    "Access key not found":  "That access key wasn’t found. Double-check it and try again.",
    "invalid access key":    "That access key wasn’t found. Double-check it and try again.",
    "Failed to redeem provision token": "Server error — please try again in a moment.",
}


def _friendly_error(raw: str) -> str:
    """Map a raw API error string to a user-facing message."""
    if not raw:
        return "Something went wrong — please try again."
    return _ERROR_MAP.get(raw, raw)


class AccessKeyPage(QWidget):
    """Step 0: enter access key to provision api_key + facility_id."""

    # Emitted once credentials have been saved to config and the engine
    # has been reloaded.  Listeners should navigate to the next page.
    provisioned = pyqtSignal()

    # Internal signal used to marshal the background-thread redeem result
    # back onto the Qt main thread safely.  Carries a plain dict payload.
    _redeem_done = pyqtSignal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background: {T.BG_WINDOW};")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(T.CONTENT_PADDING, T.CONTENT_PADDING, T.CONTENT_PADDING, T.CONTENT_PADDING)
        outer.setSpacing(0)
        outer.addStretch(1)

        # ── Centre card ───────────────────────────────────────────────────────
        card = QWidget()
        card.setFixedWidth(440)
        card.setStyleSheet(
            f"background: {T.BG_CARD};"
            f"border: 1px solid {T.BORDER_DEFAULT};"
            f"border-radius: 12px;"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(32, 32, 32, 32)
        card_layout.setSpacing(0)

        # Icon / logo area
        icon_lbl = QLabel("⬡")
        icon_lbl.setFont(make_font(36, 400))
        icon_lbl.setStyleSheet(f"color: {T.ACCENT_BLUE}; border: none;")
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(icon_lbl)
        card_layout.addSpacing(16)

        # Heading
        heading = QLabel("Activate Wright Telemetry")
        heading.setFont(make_font(*T.FONT_PAGE_HEADING))
        heading.setStyleSheet(f"color: {T.TEXT_PRIMARY}; border: none;")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(heading)
        card_layout.addSpacing(8)

        # Sub-heading
        sub = QLabel(
            "Enter the access key from your Wright One customer portal\n"
            "to connect this agent to your facility."
        )
        sub.setFont(make_font(*T.FONT_PAGE_DESC))
        sub.setStyleSheet(f"color: {T.TEXT_SECONDARY}; border: none;")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setWordWrap(True)
        card_layout.addWidget(sub)
        card_layout.addSpacing(28)

        # ── Access key input ─────────────────────────────────────────────────
        input_label = QLabel("Access Key")
        input_label.setFont(make_font(12, 600))
        input_label.setStyleSheet(f"color: {T.TEXT_PRIMARY}; border: none;")
        card_layout.addWidget(input_label)
        card_layout.addSpacing(6)

        self._key_input = QLineEdit()
        self._key_input.setPlaceholderText("XXXX-XXXX-XXXX-XXXX")
        self._key_input.setFont(make_font(15, 400))
        self._key_input.setFixedHeight(44)
        self._key_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: {T.BG_WINDOW};
                color: {T.TEXT_PRIMARY};
                border: 1px solid {T.BORDER_DEFAULT};
                border-radius: 8px;
                padding: 0 16px;
                letter-spacing: 2px;
            }}
            QLineEdit:focus {{
                border: 1px solid {T.ACCENT_BLUE};
            }}
        """)
        self._key_input.returnPressed.connect(self._on_activate)
        self._redeem_done.connect(self._handle_result)
        card_layout.addWidget(self._key_input)
        card_layout.addSpacing(8)

        # Status / error label
        self._status_lbl = QLabel("")
        self._status_lbl.setFont(make_font(12, 400))
        self._status_lbl.setStyleSheet(f"color: {T.TEXT_MUTED}; border: none;")
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setVisible(False)
        card_layout.addWidget(self._status_lbl)
        card_layout.addSpacing(20)

        # Activate button
        self._activate_btn = PrimaryButton("Activate")
        self._activate_btn.setFixedHeight(44)
        self._activate_btn.clicked.connect(self._on_activate)
        card_layout.addWidget(self._activate_btn)
        card_layout.addSpacing(16)

        # Help link
        help_lbl = QLabel(
            "Need a key? Visit the "
            f"<a style='color: {T.ACCENT_BLUE};' href='https://app.wrightfan.com'>Wright One portal</a>."
        )
        help_lbl.setFont(make_font(*T.FONT_BODY_SMALL))
        help_lbl.setStyleSheet(f"color: {T.TEXT_MUTED}; border: none;")
        help_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        help_lbl.setTextFormat(Qt.TextFormat.RichText)
        help_lbl.setOpenExternalLinks(True)
        card_layout.addWidget(help_lbl)

        # Centre the card horizontally
        h_center = QHBoxLayout()
        h_center.addStretch()
        h_center.addWidget(card)
        h_center.addStretch()
        outer.addLayout(h_center)
        outer.addStretch(1)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _set_status(self, message: str, color: str = T.TEXT_MUTED) -> None:
        self._status_lbl.setText(message)
        self._status_lbl.setStyleSheet(f"color: {color}; border: none;")
        self._status_lbl.setVisible(bool(message))

    def _set_loading(self, loading: bool) -> None:
        self._activate_btn.setEnabled(not loading)
        self._key_input.setEnabled(not loading)
        if loading:
            self._activate_btn.setText("Activating…")
        else:
            self._activate_btn.setText("Activate")

    def _on_activate(self) -> None:
        raw_key = self._key_input.text().strip()
        if not raw_key:
            self._set_status("Please enter your access key.", T.ACCENT_ORANGE)
            return

        self._set_loading(True)
        self._set_status("Connecting to Wright One…")

        from wright_telemetry.portal_client import redeem_access_key

        def _on_result(result: dict) -> None:
            # Called from the background redeem thread.  Emit a signal so Qt
            # delivers _handle_result on the main thread — QTimer.singleShot
            # without a receiver context is unreliable from non-Qt threads.
            self._redeem_done.emit({"result": result})

        redeem_access_key(access_key=raw_key, callback=_on_result)

    def _handle_result(self, payload: dict) -> None:
        result: dict = payload["result"]
        from wright_telemetry.config import load_config, save_config

        if result.get("success"):
            # Persist credentials — the engine will be started fresh after this
            from wright_telemetry.config import _DEFAULT_POLL_INTERVAL, _DEFAULT_COLLECTOR_TYPES
            from wright_telemetry.settings import API_URL
            cfg = load_config() or {}
            cfg["wright_api_key"] = result["apiKey"]
            cfg["facility_id"]    = result["facilityId"]
            cfg["email"]          = result.get("email", "")
            cfg.setdefault("wright_api_url", API_URL)
            cfg.setdefault("poll_interval_seconds", _DEFAULT_POLL_INTERVAL)
            cfg.setdefault("collector_types", list(_DEFAULT_COLLECTOR_TYPES))
            save_config(cfg)
            from wright_telemetry.config import CONFIG_FILE
            print(f"[WRIGHT] Config written → {CONFIG_FILE}")

            self._set_status("✓ Activated successfully!", T.ACCENT_GREEN)
            # Brief pause so the user sees the success state
            QTimer.singleShot(600, self.provisioned.emit)
        else:
            self._set_loading(False)
            raw_error = result.get("error", "")
            error = _friendly_error(raw_error)
            self._set_status(error, T.ACCENT_RED)
