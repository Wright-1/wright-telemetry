"""Application entry point for the GUI."""

from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from wright_telemetry import __version__
from wright_telemetry.config import load_config
from wright_telemetry.gui.engine import ScanningEngine
from wright_telemetry.gui.main_window import MainWindow
from wright_telemetry.logging_setup import configure_logging


def run_gui() -> None:
    """Launch the GUI and start the background scanning engine."""
    app = QApplication(sys.argv)
    app.setApplicationName("Wright Telemetry")
    app.setStyle("Fusion")

    cfg = load_config() or {}
    configure_logging(facility_id=cfg.get("facility_id", "unknown"))

    # Determine whether we need to provision credentials first.
    # If api_key or facility_id are missing the engine cannot connect,
    # so we show the access-key page before starting anything.
    needs_provisioning = not (
        cfg.get("wright_api_key", "").strip()
        and cfg.get("facility_id", "").strip()
    )

    if needs_provisioning:
        # Engine is created later by MainWindow._on_provisioned()
        engine = None
    else:
        engine = ScanningEngine(cfg)

    window = MainWindow(
        version=__version__,
        engine=engine,
        needs_provisioning=needs_provisioning,
    )
    window.show()

    # Only start the engine if we already have credentials
    if engine is not None:
        engine.start()

    sys.exit(app.exec())


if __name__ == "__main__":
    run_gui()
