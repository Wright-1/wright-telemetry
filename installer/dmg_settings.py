"""
dmgbuild settings for WrightData.

Used by build_mac.sh:
    dmgbuild -s installer/dmg_settings.py "WrightData" dist/WrightData-Installer.dmg

Pass overrides via -D:
    dmgbuild -s installer/dmg_settings.py -D version=1.0.0 "WrightData" out.dmg
"""

import os
from pathlib import Path

# ── Resolve paths relative to repo root ──────────────────────────────────────
# dmgbuild runs with the repo root as cwd (set in build_mac.sh)
REPO = Path(os.getcwd())
DIST = REPO / "dist"

version = defines.get("version", "1.0.0")  # noqa: F821  (injected by dmgbuild)

# ── Volume name (shown in Finder title bar and Spotlight) ────────────────────
volume_name = f"WrightData {version}"

# ── Disk image settings ───────────────────────────────────────────────────────
format          = "UDZO"     # gzip-compressed — widest compatibility
filesystem      = "HFS+"     # use HFS+ for max macOS version support
size            = None       # auto-size

# ── Contents ─────────────────────────────────────────────────────────────────
# Only the app — bypass instructions are baked into the background image.
files = [
    str(DIST / "WrightData.app"),
]

# Symlink to /Applications for drag-to-install
symlinks = {"Applications": "/Applications"}

# Hide .app extension in Finder
hide_extensions = ["WrightData.app"]

# ── Volume appearance ─────────────────────────────────────────────────────────
icon = str(REPO / "assets" / "wright-telemetry.icns") \
    if (REPO / "assets" / "wright-telemetry.icns").exists() else None

background = str(REPO / "assets" / "dmg-background.tiff")

# ── Window layout ─────────────────────────────────────────────────────────────
# ((x from left of screen, y from BOTTOM of screen), (width, height))
# Width × Height MUST match W × H in make_dmg_background.py
window_rect = ((200, 200), (800, 680))

show_status_bar = False
show_tab_view   = False
show_toolbar    = False
show_pathbar    = False
show_sidebar    = False

default_view      = "icon-view"
show_icon_preview = False

# ── Icon view ─────────────────────────────────────────────────────────────────
arrange_by      = None
icon_size       = 96
text_size       = 13
label_pos       = "bottom"
scroll_position = (0, 0)

# Icon positions (x, y) inside the 800×680 window.
# These MUST match APP_X / APPS_X / ICON_Y in make_dmg_background.py.
icon_locations = {
    "WrightData.app":  (175, 160),
    "Applications":    (575, 160),
}
