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
    engine = ScanningEngine(cfg)

    window = MainWindow(version=__version__, engine=engine)
    window.show()

    # Start after show() so the QTimer fires on the running event loop
    engine.start()

    sys.exit(app.exec())


if __name__ == "__main__":
    run_gui()
