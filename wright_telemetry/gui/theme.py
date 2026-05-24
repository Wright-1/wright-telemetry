"""Centralized color tokens and style constants.

Every color, font size, and spacing value used by the GUI lives here.
Import tokens by name — never hardcode hex values in widget code.
"""

from __future__ import annotations


# ── Colors ────────────────────────────────────────────────────────────────────

BG_WINDOW = "#FAFBFC"
BG_SIDEBAR = "#F3F4F6"
BG_CARD = "#FFFFFF"
BG_CARD_HOVER = "#F9FAFB"
BG_SECURITY = "#1A1D23"

BORDER_DEFAULT = "#E5E7EB"
BORDER_SUBTLE = "#F0F1F3"

TEXT_PRIMARY = "#111318"
TEXT_SECONDARY = "#4B5563"
TEXT_MUTED = "#9CA3AF"
TEXT_ON_DARK = "#FFFFFF"
TEXT_ON_DARK_MUTED = "#9CA3AF"

ACCENT_BLUE = "#3B82F6"
ACCENT_BLUE_HOVER = "#2563EB"
ACCENT_GREEN = "#22C55E"
ACCENT_RED = "#EF4444"
ACCENT_ORANGE = "#F97316"
ACCENT_PURPLE = "#8B5CF6"

NAV_ACTIVE_BG = "#E4E5E7"

# ── Permission category colors (left border) ─────────────────────────────────

CATEGORY_COLORS: dict[str, str] = {
    "cooling": ACCENT_BLUE,
    "hashrate": ACCENT_GREEN,
    "uptime": ACCENT_GREEN,
    "hashboards": ACCENT_PURPLE,
    "errors": ACCENT_RED,
    "auto_update": ACCENT_ORANGE,
    "remote_config": ACCENT_BLUE,
}

# Unicode icons for each permission category
CATEGORY_ICONS: dict[str, str] = {
    "cooling":      "⬡",   # temperature / fan
    "hashrate":     "⚡",   # power / hashrate
    "uptime":       "⊛",   # reliability / shield
    "hashboards":   "⊞",   # grid / hashboards
    "errors":       "△",   # warning / errors
    "auto_update":  "↻",   # refresh / updates
    "remote_config":"⇌",   # remote / sync
}


# ── Typography ────────────────────────────────────────────────────────────────

# Use system font — Roboto is not always installed
# On macOS this resolves to SF Pro; on Windows to Segoe UI
FONT_FAMILY = ".AppleSystemUIFont, Helvetica Neue, Arial"

# (size_px, weight)  — weight uses QFont constants: 400=Normal, 500=Medium, 600=DemiBold, 700=Bold
FONT_PAGE_HEADING = (22, 600)
FONT_PAGE_DESC = (13, 400)
FONT_SECTION_HEADING = (11, 700)
FONT_PERM_TITLE = (14, 600)
FONT_PERM_DESC = (12, 400)
FONT_NAV_ITEM = (13, 500)
FONT_NAV_HEADER = (14, 700)
FONT_NAV_SUB = (11, 400)
FONT_BUTTON = (13, 600)
FONT_VERSION = (11, 400)
FONT_BODY_SMALL = (12, 400)


# ── Layout ────────────────────────────────────────────────────────────────────

WINDOW_W = 1060
WINDOW_H = 720
WINDOW_MIN_W = 900
WINDOW_MIN_H = 600

SIDEBAR_W = 180
SECURITY_PANEL_W = 240

CONTENT_PADDING = 32
SIDEBAR_PADDING = 16
SECURITY_PADDING = 20

NAV_ROW_H = 40
PERM_ROW_BORDER_W = 3
BUTTON_RADIUS = 8
