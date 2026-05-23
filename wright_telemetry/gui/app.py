"""Application entry point for the GUI."""

from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from wright_telemetry import __version__
from wright_telemetry.config import ensure_config_file, is_config_complete
from wright_telemetry.gui.engine import ScanningEngine
from wright_telemetry.gui.main_window import MainWindow
from wright_telemetry.logging_setup import configure_logging


def run_gui() -> None:
    """Launch the GUI and start the background scanning engine."""
    app = QApplication(sys.argv)
    app.setApplicationName("Wright Telemetry")
    app.setStyle("Fusion")

    # Shared bootstrap: mirrors what the CLI does in __main__.py.
    # Ensures ~/.wright-telemetry/config.json exists before anything else
    # runs, then loads it (empty dict on first launch).
    cfg = ensure_config_file()
    configure_logging(facility_id=cfg.get("facility_id", "unknown"))

    # A config is considered ready when the two credentials the engine
    # needs are present.  is_config_complete() uses the same rules as
    # the CLI so the two paths stay in sync.
    complete, missing = is_config_complete(cfg)
    if not complete:
        print(f"[WRIGHT] Config incomplete — missing: {', '.join(missing)}")

    needs_provisioning = not complete

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
