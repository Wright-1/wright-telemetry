"""Application entry point for the GUI."""

from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from wright_telemetry import __version__
from wright_telemetry.gui.main_window import MainWindow


def run_gui() -> None:
    """Launch the setup wizard GUI."""
    app = QApplication(sys.argv)
    app.setApplicationName("Wright Telemetry")
    app.setStyle("Fusion")

    window = MainWindow(version=__version__)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    run_gui()
