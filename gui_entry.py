"""
PyInstaller GUI entry point.

This file is used as the entry point for the macOS .app bundle.
It launches the GUI directly — no CLI flags needed.
"""

from wright_telemetry.gui.app import run_gui

if __name__ == "__main__":
    run_gui()
