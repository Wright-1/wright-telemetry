# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for wright-telemetry — cross-platform.

Produces:
  macOS:   wright-telemetry (CLI) + WrightData.app (GUI .app bundle)
  Windows: wright-telemetry.exe (CLI) + wright-telemetry-gui/ (GUI directory)
  Linux:   wright-telemetry (CLI) + wright-telemetry-gui/ (GUI directory)

Run with:
    pyinstaller wright-telemetry.spec

Or use the platform helper scripts:
    macOS:   ./build_mac.sh
    Windows: build_windows.bat
    Linux:   ./build_linux.sh
"""

import os
import sys
import importlib
from pathlib import Path
import pyfiglet

sys.path.insert(0, str(Path(SPECPATH).resolve()))
from wright_telemetry import __version__ as _VERSION

# block_cipher was removed in PyInstaller 6 — do not use cipher= in PYZ/EXE.

# ── shared data files ─────────────────────────────────────────────────────

_pyfiglet_root = Path(importlib.import_module("pyfiglet").__file__).parent

_shared_datas = [
    (str(_pyfiglet_root / "fonts"), "pyfiglet/fonts"),
]

# ── CLI-only hidden imports (no GUI) ──────────────────────────────────────

_cli_hiddenimports = [
    "certifi",
    "wright_telemetry",
    "wright_telemetry.collectors",
    "wright_telemetry.collectors.braiins",
    "wright_telemetry.collectors.bitmain",
    "wright_telemetry.collectors.luxos",
    "wright_telemetry.collectors.vnish",
    "pyfiglet",
    "pyfiglet.fonts",
    "websockets",
    "websockets.legacy",
    "websockets.legacy.client",
    "websockets.legacy.server",
    "websockets.asyncio",
    "websockets.asyncio.client",
    "websockets.asyncio.server",
]

# ── GUI hidden imports (CLI imports + Qt + GUI submodules) ────────────────

_gui_hiddenimports = _cli_hiddenimports + [
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
    hiddenimports=_cli_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_shared_excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)

cli_pyz = PYZ(cli_a.pure, cli_a.zipped_data)

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
    hiddenimports=_gui_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_shared_excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)

gui_pyz = PYZ(gui_a.pure, gui_a.zipped_data)

# ── Platform-specific icon resolution ────────────────────────────────────────
_is_mac = sys.platform == "darwin"
_is_win = sys.platform == "win32"

if _is_mac:
    _gui_icon = str(Path("assets/wright-telemetry.icns")) if Path("assets/wright-telemetry.icns").exists() else None
elif _is_win:
    _gui_icon = str(Path("assets/wright-telemetry.ico")) if Path("assets/wright-telemetry.ico").exists() else None
else:
    _gui_icon = None

# GUI executable — COLLECT layout (works on all platforms; macOS wraps in BUNDLE below)
gui_exe = EXE(
    gui_pyz,
    gui_a.scripts,
    [],                     # binaries / zipfiles / datas go into COLLECT
    exclude_binaries=True,
    name="wright-telemetry-gui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # windowed — no Terminal window on launch
    disable_windowed_traceback=False,
    argv_emulation=_is_mac,  # only meaningful on macOS (.app open-file events)
    icon=_gui_icon,
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

# macOS: wrap the COLLECT into a .app bundle
if _is_mac:
    app = BUNDLE(
        gui_coll,
        name="WrightData.app",
        icon=_gui_icon,
        bundle_identifier="com.wrightone.wrightdata",
        version=_VERSION,
        info_plist={
            "CFBundleName":            "WrightData",
            "CFBundleDisplayName":     "WrightData",
            "CFBundleVersion":         _VERSION,
            "CFBundleShortVersionString": _VERSION,
            "CFBundleIdentifier":      "com.wrightone.wrightdata",
            "CFBundleExecutable":      "wright-telemetry-gui",
            "LSApplicationCategoryType": "public.app-category.utilities",
            "LSEnvironment": {},
            "LSUIElement": False,
            "NSHighResolutionCapable": True,
            "NSLocalNetworkUsageDescription":
                "WrightData scans your local network to discover miners.",
            "LSMinimumSystemVersion": "12.0",
        },
    )
