# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for wright-telemetry single-file executable."""

import os
import sys
import importlib
from pathlib import Path
import pyfiglet

block_cipher = None

# Locate pyfiglet fonts so they're bundled in the frozen binary
_pyfiglet_fonts = os.path.join(
    os.path.dirname(importlib.import_module("pyfiglet").__file__), "fonts"
)

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[
        (str(Path(pyfiglet.__file__).parent / "fonts"), "pyfiglet/fonts"),
    ],
    hiddenimports=[
        "wright_telemetry",
        "wright_telemetry.collectors",
        "wright_telemetry.collectors.braiins",
        "pyfiglet",
        "pyfiglet.fonts",
        "websockets",
        "websockets.legacy",
        "websockets.legacy.client",
        "websockets.legacy.server",
        "websockets.asyncio",
        "websockets.asyncio.client",
        "websockets.asyncio.server",
        "pyfiglet.fonts",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="wright-telemetry",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
