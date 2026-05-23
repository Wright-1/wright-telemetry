# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for wright-telemetry.

Produces two artefacts:
  1. wright-telemetry          — single-file CLI binary (console=True)
  2. Wright Telemetry.app      — macOS double-clickable GUI bundle (windowed)

Run with:
    pyinstaller wright-telemetry.spec

Or use the helper script which also wraps everything into a DMG:
    ./build_mac.sh
"""

import os
import sys
import importlib
from pathlib import Path
import pyfiglet

block_cipher = None

# ── shared data / hidden imports ───────────────────────────────────────────

_pyfiglet_root = Path(importlib.import_module("pyfiglet").__file__).parent

_shared_datas = [
    (str(_pyfiglet_root / "fonts"), "pyfiglet/fonts"),
]

_shared_hiddenimports = [
    "wright_telemetry",
    "wright_telemetry.collectors",
    "wright_telemetry.collectors.braiins",
    "wright_telemetry.collectors.bitmain",
    "wright_telemetry.collectors.luxos",
    "wright_telemetry.collectors.vnish",
    "wright_telemetry.gui",
    "wright_telemetry.gui.app",
    "wright_telemetry.gui.engine",
    "wright_telemetry.gui.main_window",
    "wright_telemetry.gui.sidebar",
    "wright_telemetry.gui.theme",
    "wright_telemetry.gui.widgets",
    "wright_telemetry.gui.fonts",
    "wright_telemetry.gui.security_panel",
    "wright_telemetry.gui.scan_manager",
    "pyfiglet",
    "pyfiglet.fonts",
    "websockets",
    "websockets.legacy",
    "websockets.legacy.client",
    "websockets.legacy.server",
    "websockets.asyncio",
    "websockets.asyncio.client",
    "websockets.asyncio.server",
    "PyQt6",
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
]

_shared_excludes = [
    "tkinter",
    "matplotlib",
    "numpy",
    "scipy",
    "pandas",
    "PIL",
]

# ─────────────────────────────────────────────────────────────────────────────
# 1.  CLI binary  (single file, console)
# ─────────────────────────────────────────────────────────────────────────────

cli_a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=_shared_datas,
    hiddenimports=_shared_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_shared_excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

cli_pyz = PYZ(cli_a.pure, cli_a.zipped_data, cipher=block_cipher)

cli_exe = EXE(
    cli_pyz,
    cli_a.scripts,
    cli_a.binaries,
    cli_a.zipfiles,
    cli_a.datas,
    name="wright-telemetry",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    console=True,           # keep terminal open for CLI usage
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# ─────────────────────────────────────────────────────────────────────────────
# 2.  macOS .app bundle  (windowed, GUI)
# ─────────────────────────────────────────────────────────────────────────────

gui_a = Analysis(
    ["gui_entry.py"],
    pathex=[],
    binaries=[],
    datas=_shared_datas,
    hiddenimports=_shared_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_shared_excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

gui_pyz = PYZ(gui_a.pure, gui_a.zipped_data, cipher=block_cipher)

# The inner Unix executable that lives inside the .app bundle
gui_exe = EXE(
    gui_pyz,
    gui_a.scripts,
    [],                     # binaries / zipfiles / datas go into COLLECT
    exclude_binaries=True,
    name="wright-telemetry-gui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,              # skip UPX for the GUI bundle — macOS notarisation dislikes it
    console=False,          # windowed — no Terminal window on launch
    disable_windowed_traceback=False,
    argv_emulation=True,    # required for macOS .app open-file events
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

gui_coll = COLLECT(
    gui_exe,
    gui_a.binaries,
    gui_a.zipfiles,
    gui_a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="wright-telemetry-gui",
)

# Resolve optional icon — skip gracefully if the file doesn't exist yet
_icon_path = Path("assets/wright-telemetry.icns")
_icon_arg  = str(_icon_path) if _icon_path.exists() else None

app = BUNDLE(
    gui_coll,
    name="Wright Telemetry.app",
    icon=_icon_arg,
    bundle_identifier="com.wrightone.wright-telemetry",
    version="0.7.3",
    info_plist={
        # Human-readable name shown in Finder / Dock / menu bar
        "CFBundleName":            "Wright Telemetry",
        "CFBundleDisplayName":     "Wright Telemetry",
        "CFBundleVersion":         "0.7.3",
        "CFBundleShortVersionString": "0.7.3",
        "CFBundleIdentifier":      "com.wrightone.wright-telemetry",
        "CFBundleExecutable":      "wright-telemetry-gui",

        # macOS category — shown in Launchpad / App Store searches
        "LSApplicationCategoryType": "public.app-category.utilities",

        # Allow the app to be launched from a read-only DMG without
        # macOS complaining about writing into the bundle directory.
        "LSEnvironment": {},

        # Don't show a Dock icon before the first window appears
        "LSUIElement": False,

        # High-resolution Retina display support
        "NSHighResolutionCapable": True,

        # Microphone / camera / network — not used; listed to silence
        # macOS privacy prompts that can appear on first launch
        "NSLocalNetworkUsageDescription":
            "Wright Telemetry scans your local network to discover miners.",

        # Allow the app to open immediately without Gatekeeper blocking
        # the whole process on first run (user still gets the one-time
        # right-click → Open prompt if unsigned)
        "LSMinimumSystemVersion": "12.0",
    },
)
