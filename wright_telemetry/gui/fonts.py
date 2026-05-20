"""Font helper — builds QFont instances from theme tokens."""

from __future__ import annotations

from PyQt6.QtGui import QFont

from wright_telemetry.gui.theme import FONT_FAMILY


def make_font(size: int, weight: int) -> QFont:
    """Return a QFont for the given pixel size and CSS-style weight (400–700)."""
    f = QFont(FONT_FAMILY)
    f.setPixelSize(size)
    # Map CSS weight → QFont.Weight enum
    _map = {
        400: QFont.Weight.Normal,
        500: QFont.Weight.Medium,
        600: QFont.Weight.DemiBold,
        700: QFont.Weight.Bold,
    }
    f.setWeight(_map.get(weight, QFont.Weight.Normal))
    return f
