"""Main application window -- assembles sidebar, content pages, and security panel."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from wright_telemetry.gui.fonts import make_font
from wright_telemetry.gui import theme as T
from wright_telemetry.gui.pages.access_key import AccessKeyPage
from wright_telemetry.gui.pages.permissions import PermissionsPage
from wright_telemetry.gui.pages.discovery import DiscoveryPage
from wright_telemetry.gui.pages.overview import OverviewPage
from wright_telemetry.gui.pages.portal import PortalPage
from wright_telemetry.gui.security_panel import SecurityPanel
from wright_telemetry.gui.sidebar import Sidebar

if TYPE_CHECKING:
    from wright_telemetry.gui.engine import ScanningEngine


class MainWindow(QWidget):
    """Top-level window: title bar + three-column body + onboarding footer.

    Onboarding state is tracked in memory:
      step 1 (permissions): user clicks Next on the permissions page
      step 2 (discovery):   user clicks Next on the discovery page
      step 3 (account):     engine receives a successful agent_info fetch

    While onboarding is incomplete the sidebar Overview item is hidden and
    the stack shows PortalPage instead.  A footer progress bar spans the full
    window width and disappears once step 3 completes.
    """

    PAGE_KEYS = ["permissions", "discovery", "overview"]

    def __init__(
        self,
        version: str = "0.7.3",
        engine: "ScanningEngine | None" = None,
        needs_provisioning: bool = False,
    ):
        super().__init__()
        self._engine = engine
        self._needs_provisioning = needs_provisioning

        # Onboarding state (all in memory, never persisted)
        self._ob_permissions = False
        self._ob_discovery   = False
        self._ob_account     = False

        self.setWindowTitle("Wright Telemetry Collector -- Local Agent")
        self.resize(T.WINDOW_W, T.WINDOW_H)
        self.setMinimumSize(T.WINDOW_MIN_W, T.WINDOW_MIN_H)
        self.setStyleSheet(f"background: {T.BG_WINDOW};")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Title bar
        title_bar = QWidget()
        title_bar.setFixedHeight(36)
        title_bar.setStyleSheet(
            f"background: {T.BG_SIDEBAR}; border-bottom: 1px solid {T.BORDER_DEFAULT};"
        )
        tb_layout = QHBoxLayout(title_bar)
        tb_layout.setContentsMargins(16, 0, 16, 0)
        tb_label = QLabel("WRIGHT TELEMETRY COLLECTOR - LOCAL AGENT")
        tb_label.setFont(make_font(11, 600))
        tb_label.setStyleSheet(f"color: {T.TEXT_PRIMARY}; letter-spacing: 0.5px;")
        tb_layout.addWidget(tb_label)
        tb_layout.addStretch()
        root.addWidget(title_bar)

        # Three-column body
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self.sidebar = Sidebar(version=version)
        self.sidebar.page_selected.connect(self._switch_page)
        body.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        self.pages: dict[str, QWidget] = {
            "permissions": PermissionsPage(engine=engine),
            "discovery":   DiscoveryPage(engine=engine),
            "overview":    OverviewPage(engine=engine),
            "portal":      PortalPage(),
        }
        for key in self.PAGE_KEYS:
            self.stack.addWidget(self.pages[key])
        self.stack.addWidget(self.pages["portal"])

        self._access_key_page = AccessKeyPage()
        self.stack.addWidget(self._access_key_page)
        self._access_key_page.provisioned.connect(self._on_provisioned)

        body.addWidget(self.stack, 1)

        self.security = SecurityPanel()
        body.addWidget(self.security)

        root.addLayout(body, 1)

        # Onboarding footer (full window width, hidden once account step done)
        self._footer = self._build_footer()
        root.addWidget(self._footer)

        # Engine signals
        if engine is not None:
            self._connect_engine_signals(engine)
        else:
            self.sidebar.set_portal_connected(False)

        # Page navigation wiring
        self.pages["permissions"].next_clicked.connect(self._on_permissions_next)
        self.pages["discovery"].next_clicked.connect(self._on_discovery_next)

        # Initial state
        if needs_provisioning:
            self.stack.setCurrentWidget(self._access_key_page)
            self.sidebar.setVisible(False)
            self.security.setVisible(False)
            self._footer.setVisible(False)
        else:
            self._update_onboarding_ui()

    # -------------------------------------------------------------------------
    # Footer construction
    # -------------------------------------------------------------------------

    def _build_footer(self) -> QWidget:
        footer = QWidget()
        footer.setObjectName("ob_footer")
        footer.setStyleSheet(f"""
            QWidget#ob_footer {{
                background: {T.BG_WINDOW};
                border-top: 1px solid {T.BORDER_DEFAULT};
            }}
        """)
        footer.setFixedHeight(56)

        layout = QHBoxLayout(footer)
        layout.setContentsMargins(24, 0, 24, 0)
        layout.setSpacing(0)

        # Step indicators (left)
        self._step_dots:   list[QLabel] = []
        self._step_labels: list[QLabel] = []
        step_names = ["PERMISSIONS", "DISCOVER MINERS", "CREATE ACCOUNT"]

        steps_row = QHBoxLayout()
        steps_row.setSpacing(0)
        steps_row.setContentsMargins(0, 0, 0, 0)

        for i, name in enumerate(step_names):
            dot = QLabel("●")
            dot.setFont(make_font(8, 400))
            dot.setStyleSheet(f"color: {T.TEXT_MUTED}; background: transparent;")
            self._step_dots.append(dot)

            lbl = QLabel(name)
            lbl.setFont(make_font(11, 600))
            lbl.setStyleSheet(f"color: {T.TEXT_MUTED}; background: transparent; letter-spacing: 0.4px;")
            self._step_labels.append(lbl)

            steps_row.addWidget(dot)
            steps_row.addSpacing(6)
            steps_row.addWidget(lbl)

            if i < len(step_names) - 1:
                spacer = QLabel("  ·  ")
                spacer.setFont(make_font(11, 400))
                spacer.setStyleSheet(f"color: {T.BORDER_DEFAULT}; background: transparent;")
                steps_row.addWidget(spacer)

        layout.addLayout(steps_row)
        layout.addStretch()

        # Next button (right) -- text updates per step
        from wright_telemetry.gui.widgets import PrimaryButton
        self._footer_btn = PrimaryButton("Next: Discover Miners  →")
        self._footer_btn.setFixedHeight(38)
        self._footer_btn.clicked.connect(self._on_footer_next)
        layout.addWidget(self._footer_btn)

        return footer

    def _on_footer_next(self) -> None:
        """Advance the onboarding flow from the footer button."""
        if not self._ob_permissions:
            self._on_permissions_next()
        elif not self._ob_discovery:
            self._on_discovery_next()


    def _update_onboarding_ui(self) -> None:
        """Refresh footer step dots/labels, button text, sidebar nav."""
        states = [self._ob_permissions, self._ob_discovery, self._ob_account]

        active_step = next((i for i, s in enumerate(states) if not s), len(states))
        for i, (dot, lbl) in enumerate(zip(self._step_dots, self._step_labels)):
            if states[i]:
                dot.setStyleSheet(f"color: {T.ACCENT_BLUE}; background: transparent;")
                lbl.setStyleSheet(
                    f"color: {T.TEXT_SECONDARY}; font-weight: 600; "
                    f"background: transparent; letter-spacing: 0.4px;"
                )
            elif i == active_step:
                dot.setStyleSheet(f"color: {T.ACCENT_BLUE}; background: transparent;")
                lbl.setStyleSheet(
                    f"color: {T.ACCENT_BLUE}; font-weight: 700; "
                    f"background: transparent; letter-spacing: 0.4px;"
                )
            else:
                dot.setStyleSheet(f"color: {T.BORDER_DEFAULT}; background: transparent;")
                lbl.setStyleSheet(
                    f"color: {T.TEXT_MUTED}; font-weight: 600; "
                    f"background: transparent; letter-spacing: 0.4px;"
                )

        # Update button text to reflect the next step
        if not self._ob_permissions:
            self._footer_btn.setText("Next: Discover Miners  →")
        elif not self._ob_discovery:
            self._footer_btn.setText("Next: Create Account  →")
        else:
            self._footer_btn.setVisible(False)

        # Footer disappears once account is connected
        self._footer.setVisible(not self._ob_account)

        # Sidebar Overview visibility
        self.sidebar.set_portal_connected(self._ob_account)

    # -------------------------------------------------------------------------
    # Onboarding step handlers
    # -------------------------------------------------------------------------

    def _on_permissions_next(self) -> None:
        self._ob_permissions = True
        self._update_onboarding_ui()
        self._switch_page("discovery")

    def _on_discovery_next(self) -> None:
        self._ob_discovery = True
        self._update_onboarding_ui()
        # Show portal page in place of overview
        self.stack.setCurrentWidget(self.pages["portal"])
        self.sidebar.set_active("overview")   # keep nav consistent
        self.security.setVisible(False)

    def _on_account_connected(self) -> None:
        self._ob_account = True
        self._update_onboarding_ui()
        # Now switch to the real Overview
        self._switch_page("overview")

    # -------------------------------------------------------------------------
    # Engine wiring
    # -------------------------------------------------------------------------

    def _connect_engine_signals(self, engine: "ScanningEngine") -> None:
        engine.signals.ws_status_changed.connect(self.sidebar.set_ws_status)
        engine.signals.agent_info_loaded.connect(
            lambda _data: self._on_account_connected()
        )
        engine.signals.agent_info_error.connect(
            lambda _err: self.sidebar.set_portal_connected(False)
        )

    # -------------------------------------------------------------------------
    # Provisioning callback (access-key page)
    # -------------------------------------------------------------------------

    def _on_provisioned(self) -> None:
        from wright_telemetry.config import load_config
        from wright_telemetry.gui.engine import ScanningEngine

        cfg = load_config() or {}
        self._engine = ScanningEngine(cfg)
        self._connect_engine_signals(self._engine)

        self.pages["permissions"]._engine = self._engine
        self.pages["discovery"].wire_engine(self._engine)
        self.pages["overview"]._engine    = self._engine

        self._engine.start()

        self.sidebar.setVisible(True)
        self.security.setVisible(True)
        self._footer.setVisible(True)
        self._update_onboarding_ui()
        self._switch_page("permissions")

    # -------------------------------------------------------------------------
    # Navigation
    # -------------------------------------------------------------------------

    def _switch_page(self, key: str) -> None:
        # Advance onboarding state to match wherever the user navigates.
        # Navigating past a step implies that step is done.
        # Collect all state changes first, then call _update_onboarding_ui
        # at most once to avoid redundant redraws.
        state_changed = False
        if key in ("discovery", "overview"):
            if not self._ob_permissions:
                self._ob_permissions = True
                state_changed = True
        if key == "overview":
            if not self._ob_discovery:
                self._ob_discovery = True
                state_changed = True
        if state_changed:
            self._update_onboarding_ui()

        if key == "overview" and not self._ob_account:
            # Still onboarding -- show portal page instead of overview
            self.stack.setCurrentWidget(self.pages["portal"])
            self.sidebar.set_active("overview")
            self.security.setVisible(False)
            return
        if key in self.pages:
            self.stack.setCurrentWidget(self.pages[key])
            self.sidebar.set_active(key)
            self.security.setVisible(key == "permissions")

    # -------------------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._engine is not None:
            self._engine.stop()
        event.accept()
