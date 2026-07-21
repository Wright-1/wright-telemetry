"""Native confirmation dialog — shared by every "are you sure?" prompt in the GUI.

Tries NSAlert first (uses the real app icon, fully respects dark mode),
falls back to osascript if PyObjC is unavailable, then to QMessageBox as a
last resort (and on non-macOS platforms).
"""

from __future__ import annotations

import subprocess
import sys

from PyQt6.QtWidgets import QMessageBox, QWidget


def confirm_dialog(
    parent: QWidget | None,
    title: str,
    message: str,
    confirm_label: str = "Continue",
    cancel_label: str = "Cancel",
) -> bool:
    """Show a native confirmation dialog. Returns True if the user confirms."""
    if sys.platform == "darwin":
        return _confirm_macos(title, message, confirm_label, cancel_label, parent)
    return _confirm_qt(parent, title, message, confirm_label, cancel_label)


def _confirm_macos(
    title: str,
    message: str,
    confirm_label: str,
    cancel_label: str,
    parent: QWidget | None,
) -> bool:
    # ── 1. NSAlert via PyObjC (packaged app: app icon, dark-mode aware) ──
    try:
        from AppKit import NSAlert  # type: ignore
        alert = NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(message)
        alert.setAlertStyle_(0)          # 0 = NSAlertStyleWarning
        alert.addButtonWithTitle_(confirm_label)
        alert.addButtonWithTitle_(cancel_label)
        # runModal returns 1000 for the first button added (confirm_label)
        return int(alert.runModal()) == 1000
    except Exception:
        pass

    # ── 2. osascript fallback (dev environment without PyObjC) ────────────
    script = (
        f'display dialog "{_escape(message)}" '
        f'with title "{_escape(title)}" '
        f'buttons {{"{_escape(cancel_label)}", "{_escape(confirm_label)}"}} '
        f'default button "{_escape(cancel_label)}" '
        f'with icon caution'
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
        )
        return confirm_label in result.stdout
    except Exception:
        pass

    # ── 3. Qt fallback (should never be reached on macOS) ─────────────────
    return _confirm_qt(parent, title, message, confirm_label, cancel_label)


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _confirm_qt(
    parent: QWidget | None,
    title: str,
    message: str,
    confirm_label: str,
    cancel_label: str,
) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(message)
    yes_btn = box.addButton(confirm_label, QMessageBox.ButtonRole.YesRole)
    box.addButton(cancel_label, QMessageBox.ButtonRole.NoRole)
    box.setDefaultButton(yes_btn)
    box.exec()
    return box.clickedButton() is yes_btn
