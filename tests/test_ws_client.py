"""Tests for AgentController's has_scan_manager flag.

This flag is what lets the scheduler distinguish GUI mode (a ScanManager keeps
the shared discovery store updated) from headless/TUI mode (the same
AgentController exists for the WebSocket bridge, but no ScanManager is
running, so the scheduler must run its own subnet scans).
"""

from __future__ import annotations

from wright_telemetry.gui.scan_manager import ScanManager
from wright_telemetry.ws_client import AgentController


def test_agent_controller_defaults_to_no_scan_manager():
    controller = AgentController()
    assert controller.has_scan_manager is False


def test_scan_manager_marks_controller_as_gui_mode():
    controller = AgentController()
    assert controller.has_scan_manager is False

    ScanManager(controller, firmware_types=["braiins"])

    assert controller.has_scan_manager is True
