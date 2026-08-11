"""Settings page — view the provisioned access key and reset local config.

Shows the ``access_key`` the user typed on the provisioning screen (masked
by default, revealable/copyable) plus the facility ID / email for context.
Note this is distinct from ``wright_api_key`` — the access key is what the
user enters when there is no config file, and the portal's redeem call
returns the actual API key from that.  Configs written before this field
existed won't have it, so it displays as "null" until the user clears
config and re-enters an access key.

The "Clear Configuration" action deletes ``~/.wright-telemetry/config.json``
entirely and emits ``config_cleared`` so ``MainWindow`` can send the user
back to the access-key provisioning screen.  Clearing works even when the
config file is missing or has no access key — it's just a delete-if-exists.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from wright_telemetry.gui.fonts import make_font
from wright_telemetry.gui import theme as T
from wright_telemetry.gui.widgets import SecondaryButton


class SettingsPage(QWidget):
    """Access token viewer + config reset."""

    # Emitted after the config file has been deleted so MainWindow can
    # navigate back to the provisioning screen.
    config_cleared = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background: {T.BG_WINDOW};")

        root = QVBoxLayout(self)
        root.setContentsMargins(
            T.CONTENT_PADDING, T.CONTENT_PADDING,
            T.CONTENT_PADDING, T.CONTENT_PADDING,
        )
        root.setSpacing(20)

        # Heading
        title_lbl = QLabel("Settings")
        title_lbl.setFont(make_font(*T.FONT_PAGE_HEADING))
        title_lbl.setStyleSheet(f"color: {T.TEXT_PRIMARY}; background: transparent;")
        root.addWidget(title_lbl)

        desc_lbl = QLabel("Manage this agent's connection to your Wright One facility.")
        desc_lbl.setFont(make_font(*T.FONT_PAGE_DESC))
        desc_lbl.setStyleSheet(f"color: {T.TEXT_SECONDARY}; background: transparent;")
        root.addWidget(desc_lbl)

        root.addWidget(self._build_account_card())
        root.addWidget(self._build_danger_card())
        root.addStretch(1)

        self._refresh()

    # ── Cards ─────────────────────────────────────────────────────────────────

    def _build_account_card(self) -> QWidget:
        card = self._make_card()
        layout = card.layout()

        heading = QLabel("Account")
        heading.setFont(make_font(*T.FONT_SECTION_HEADING))
        heading.setStyleSheet(
            f"color: {T.TEXT_MUTED}; background: transparent; border: none; letter-spacing: 0.5px;"
        )
        layout.addWidget(heading)
        layout.addSpacing(12)

        self._facility_lbl = self._make_info_row(layout, "Facility ID")
        self._email_lbl = self._make_info_row(layout, "Email")

        # Access key row (label + plain-text field + copy button)
        token_label = QLabel("Access Key")
        token_label.setFont(make_font(12, 600))
        token_label.setStyleSheet(f"color: {T.TEXT_PRIMARY}; background: transparent; border: none;")
        layout.addWidget(token_label)
        layout.addSpacing(6)

        token_row = QHBoxLayout()
        token_row.setSpacing(8)

        self._token_field = QLineEdit()
        self._token_field.setReadOnly(True)
        self._token_field.setFont(make_font(13, 400))
        self._token_field.setFixedHeight(38)
        self._token_field.setStyleSheet(f"""
            QLineEdit {{
                background: {T.BG_WINDOW};
                color: {T.TEXT_PRIMARY};
                border: 1px solid {T.BORDER_DEFAULT};
                border-radius: 6px;
                padding: 0 12px;
            }}
        """)
        token_row.addWidget(self._token_field, 1)

        self._copy_btn = self._make_ghost_btn("Copy")
        self._copy_btn.clicked.connect(self._on_copy)
        token_row.addWidget(self._copy_btn)

        layout.addLayout(token_row)
        return card

    def _build_danger_card(self) -> QWidget:
        card = self._make_card()
        layout = card.layout()

        heading = QLabel("Danger Zone")
        heading.setFont(make_font(*T.FONT_SECTION_HEADING))
        heading.setStyleSheet(
            f"color: {T.ACCENT_RED}; background: transparent; border: none; letter-spacing: 0.5px;"
        )
        layout.addWidget(heading)
        layout.addSpacing(12)

        row = QHBoxLayout()
        row.setSpacing(16)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        clear_title = QLabel("Clear Configuration")
        clear_title.setFont(make_font(*T.FONT_PERM_TITLE))
        clear_title.setStyleSheet(f"color: {T.TEXT_PRIMARY}; background: transparent; border: none;")
        text_col.addWidget(clear_title)
        clear_desc = QLabel(
            "Deletes the local config file, including your access key, subnets, "
            "and permissions. You'll need to re-enter an access key and set "
            "these up again to reconnect this agent."
        )
        clear_desc.setFont(make_font(*T.FONT_PERM_DESC))
        clear_desc.setStyleSheet(f"color: {T.TEXT_SECONDARY}; background: transparent; border: none;")
        clear_desc.setWordWrap(True)
        text_col.addWidget(clear_desc)
        row.addLayout(text_col, 1)

        self._clear_btn = QPushButton("Clear Configuration")
        self._clear_btn.setFont(make_font(*T.FONT_BUTTON))
        self._clear_btn.setFixedHeight(38)
        self._clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_btn.setStyleSheet(f"""
            QPushButton {{
                background: {T.BG_CARD};
                color: {T.ACCENT_RED};
                border: 1px solid {T.ACCENT_RED};
                border-radius: {T.BUTTON_RADIUS}px;
                padding: 0 18px;
            }}
            QPushButton:hover {{
                background: {T.ACCENT_RED};
                color: {T.TEXT_ON_DARK};
            }}
        """)
        self._clear_btn.clicked.connect(self._on_clear_clicked)
        row.addWidget(self._clear_btn)

        layout.addLayout(row)
        return card

    # ── Factory helpers ───────────────────────────────────────────────────────

    _CARD_SEQ = 0

    def _make_card(self) -> QWidget:
        # Give each card a unique object name and scope the border/background
        # rule to that ID selector — a bare "QWidget { ... }" rule cascades to
        # every descendant QWidget (including QLabels), which is what was
        # drawing a box around every row.
        SettingsPage._CARD_SEQ += 1
        obj_name = f"settings_card_{SettingsPage._CARD_SEQ}"

        card = QWidget()
        card.setObjectName(obj_name)
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setStyleSheet(f"""
            QWidget#{obj_name} {{
                background: {T.BG_CARD};
                border: 1px solid {T.BORDER_DEFAULT};
                border-radius: 8px;
            }}
        """)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(0)
        return card

    def _make_info_row(self, layout: QVBoxLayout, label: str) -> QLabel:
        row = QHBoxLayout()
        row.setSpacing(8)
        name_lbl = QLabel(label)
        name_lbl.setFont(make_font(12, 600))
        name_lbl.setFixedWidth(90)
        name_lbl.setStyleSheet(f"color: {T.TEXT_SECONDARY}; background: transparent; border: none;")
        row.addWidget(name_lbl)

        value_lbl = QLabel("—")
        value_lbl.setFont(make_font(12, 400))
        value_lbl.setStyleSheet(f"color: {T.TEXT_PRIMARY}; background: transparent; border: none;")
        row.addWidget(value_lbl, 1)

        layout.addLayout(row)
        layout.addSpacing(10)
        return value_lbl

    @staticmethod
    def _make_ghost_btn(label: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setFont(make_font(12, 500))
        btn.setFixedHeight(38)
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
            }}
        """)
        return btn

    # ── Data loading ──────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        """Reload config from disk and repopulate the fields.

        ``access_key`` is the value the user typed on the provisioning
        screen — distinct from ``wright_api_key``, which is what the portal
        returns from redeeming it.  Configs written before ``access_key``
        was persisted won't have it, so it correctly shows as "null" until
        the user clears config and re-enters an access key.
        """
        from wright_telemetry.config import load_config

        cfg = load_config() or {}
        self._token = str(cfg.get("access_key") or "")
        self._facility_lbl.setText(str(cfg.get("facility_id", "") or "—"))
        self._email_lbl.setText(str(cfg.get("email", "") or "—"))

        has_token = bool(self._token)
        self._token_field.setText(self._token if has_token else "null")
        self._copy_btn.setEnabled(has_token)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._refresh()

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_copy(self) -> None:
        QApplication.clipboard().setText(self._token)
        self._copy_btn.setText("Copied!")
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(1200, lambda: self._copy_btn.setText("Copy"))

    def _on_clear_clicked(self) -> None:
        from wright_telemetry.gui.native_dialog import confirm_dialog

        confirmed = confirm_dialog(
            self,
            "Clear Configuration",
            "You are removing your config file. This disconnects the agent "
            "and deletes your access key, discovered subnets, and permission "
            "settings — you'll need to enter your access key and set up "
            "subnets and permissions again.",
            confirm_label="Clear Configuration",
            cancel_label="Cancel",
        )
        if not confirmed:
            return

        from wright_telemetry.config import CONFIG_FILE
        try:
            if CONFIG_FILE.exists():
                CONFIG_FILE.unlink()
        except OSError as exc:
            QMessageBox.critical(
                self,
                "Could not clear configuration",
                f"Failed to delete the config file:\n{exc}",
            )
            return

        self._refresh()
        self.config_cleared.emit()
