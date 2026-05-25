"""
Generate the WrightData DMG background as a multi-resolution TIFF.

A multi-res TIFF bundles a 1× (800×680) and 2× (1600×1360) image in one file.
macOS Finder picks the right resolution automatically — sharp on Retina,
correctly laid out on standard displays.

Tools used:
    • PyQt6      (pip install PyQt6)     — renders both images
    • sips       (built into macOS)      — converts PNG → TIFF with DPI tag
    • tiffutil   (built into macOS)      — merges 1× + 2× into one TIFF

Run from repo root:
    python assets/make_dmg_background.py

Output:
    assets/dmg-background.tiff   (multi-resolution, used by dmg_settings.py)
"""

import subprocess
import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import (
    QColor, QFont, QImage, QLinearGradient,
    QPainter, QPen, QBrush, QPolygonF,
)
from PyQt6.QtCore import Qt, QPointF, QRectF

app = QApplication.instance() or QApplication(sys.argv)

# ─────────────────────────────────────────────────────────────────────────────
# Logical dimensions  (= Finder window size in dmg_settings.py)
# ─────────────────────────────────────────────────────────────────────────────
LW, LH = 800, 680

# ─────────────────────────────────────────────────────────────────────────────
# Colours
# ─────────────────────────────────────────────────────────────────────────────
WHITE       = QColor("#FFFFFF")
BG          = QColor("#F5F5F7")
HEADER_BG   = QColor("#FFFFFF")
DIVIDER     = QColor("#D2D2D7")
TEXT_DARK   = QColor("#1D1D1F")
TEXT_MED    = QColor("#3A3A3C")
TEXT_LIGHT  = QColor("#6E6E73")
BLUE        = QColor("#3B82F6")
BLUE_LIGHT  = QColor("#E8F1FB")
CARD_BG     = QColor("#FFFFFF")
CARD_BORDER = QColor("#D2D2D7")
ZONE_FILL   = QColor("#EFEFEF")
ZONE_BORDER = QColor("#C8C8CC")

# ─────────────────────────────────────────────────────────────────────────────
# Render at a given pixel scale (1 or 2)
# ─────────────────────────────────────────────────────────────────────────────
def render(scale: int) -> QImage:
    PW, PH = LW * scale, LH * scale

    img = QImage(PW, PH, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.white)

    p = QPainter(img)
    if scale > 1:
        p.scale(scale, scale)          # paint in logical pts; output is physical
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

    # ── Background ────────────────────────────────────────────────────────────
    p.fillRect(QRectF(0, 0, LW, LH), BG)

    # ── Header (y 0 – 68) ─────────────────────────────────────────────────────
    HEADER_H = 68
    p.fillRect(QRectF(0, 0, LW, HEADER_H), HEADER_BG)

    title_f = QFont(".AppleSystemUIFont", 17, QFont.Weight.Bold)
    title_f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.5)
    p.setFont(title_f)
    p.setPen(QPen(TEXT_DARK))
    p.drawText(QRectF(0, 8, LW, 32),
               Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
               "WrightData")

    sub_f = QFont(".AppleSystemUIFont", 11)
    p.setFont(sub_f)
    p.setPen(QPen(TEXT_LIGHT))
    p.drawText(QRectF(0, 40, LW, 20),
               Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
               "Miner telemetry collector  ·  v0.7.3")

    p.setPen(QPen(DIVIDER, 1))
    p.drawLine(QPointF(0, HEADER_H), QPointF(LW, HEADER_H))

    # ── Drag zone (icons at y=185) ────────────────────────────────────────────
    APP_X,  ICON_Y = 175, 155
    APPS_X         = 575
    ZONE_R         = 54

    p.setBrush(Qt.BrushStyle.NoBrush)

    # Arrow
    AX1, AX2 = APP_X + ZONE_R + 10, APPS_X - ZONE_R - 32
    AY = ICON_Y
    SHAFT_H, AHEAD_W, AHEAD_H = 5, 20, 18

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(BLUE))
    p.drawRect(QRectF(AX1, AY - SHAFT_H, AX2 - AX1, SHAFT_H * 2))
    head = QPolygonF([
        QPointF(AX2,           AY - AHEAD_H),
        QPointF(AX2 + AHEAD_W, AY),
        QPointF(AX2,           AY + AHEAD_H),
    ])
    p.drawPolygon(head)
    p.setBrush(Qt.BrushStyle.NoBrush)

    hint_f = QFont(".AppleSystemUIFont", 10)
    p.setFont(hint_f)
    p.setPen(QPen(TEXT_LIGHT))
    mid_x = (AX1 + AX2 + AHEAD_W) / 2
    p.drawText(QRectF(mid_x - 100, AY + SHAFT_H + 10, 200, 18),
               Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
               "Drag to install")

    # ── Divider ───────────────────────────────────────────────────────────────
    BYPASS_TOP = 272
    p.setPen(QPen(DIVIDER, 1))
    p.drawLine(QPointF(0, BYPASS_TOP), QPointF(LW, BYPASS_TOP))

    # ── Bypass section ────────────────────────────────────────────────────────
    sec_f = QFont(".AppleSystemUIFont", 11, QFont.Weight.Medium)
    p.setFont(sec_f)
    p.setPen(QPen(TEXT_MED))
    p.drawText(QRectF(0, BYPASS_TOP + 10, LW, 22),
               Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
               "First launch blocked by macOS? Here’s how to open it:")

    CARD_Y = BYPASS_TOP + 38
    CARD_H, CARD_W, GAP = 168, 346, 16
    C1X = (LW - CARD_W * 2 - GAP) // 2
    C2X = C1X + CARD_W + GAP
    RADIUS = 10

    for cx in (C1X, C2X):
        p.setPen(QPen(CARD_BORDER, 1))
        p.setBrush(QBrush(CARD_BG))
        p.drawRoundedRect(QRectF(cx, CARD_Y, CARD_W, CARD_H), RADIUS, RADIUS)
    p.setBrush(Qt.BrushStyle.NoBrush)

    def badge(cx, cy, num):
        R = 10
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(BLUE))
        p.drawEllipse(QRectF(cx, cy, R*2, R*2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        bf = QFont(".AppleSystemUIFont", 10, QFont.Weight.Bold)
        p.setFont(bf)
        p.setPen(QPen(WHITE))
        p.drawText(QRectF(cx, cy, R*2, R*2),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, num)

    BX, BY = 16, 16
    badge(C1X + BX, CARD_Y + BY, "1")
    badge(C2X + BX, CARD_Y + BY, "2")

    ct_f = QFont(".AppleSystemUIFont", 12, QFont.Weight.DemiBold)
    cb_f = QFont(".AppleSystemUIFont", 11)
    TX = BX + 26

    p.setFont(ct_f); p.setPen(QPen(TEXT_DARK))
    p.drawText(QRectF(C1X + TX, CARD_Y + BY, CARD_W - TX - 12, 22),
               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
               "Right-click → Open")
    p.setFont(cb_f); p.setPen(QPen(TEXT_MED))
    p.drawText(QRectF(C1X + 16, CARD_Y + 48, CARD_W - 32, CARD_H - 58),
               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
               'Open your Applications folder, right-click WrightData, and choose “Open”. '
               'When the security dialog appears, click “Open” again. '
               'You only need to do this once.')

    p.setFont(ct_f); p.setPen(QPen(TEXT_DARK))
    p.drawText(QRectF(C2X + TX, CARD_Y + BY, CARD_W - TX - 12, 22),
               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
               "Apple menu → System Settings")
    p.setFont(cb_f); p.setPen(QPen(TEXT_MED))
    p.drawText(QRectF(C2X + 16, CARD_Y + 48, CARD_W - 32, CARD_H - 58),
               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
               'Go to System Settings → Privacy & Security. '
               'Scroll down to the Security section and click “Open Anyway” '
               'next to WrightData.')

    # ── Footer ────────────────────────────────────────────────────────────────
    p.setPen(QPen(DIVIDER, 1))
    p.drawLine(QPointF(0, LH - 30), QPointF(LW, LH - 30))
    ff = QFont(".AppleSystemUIFont", 9)
    p.setFont(ff)
    p.setPen(QPen(QColor("#B0B0B5")))
    p.drawText(QRectF(0, LH - 26, LW, 20),
               Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
               "Open source  ·  Read-only  ·  Passwords never leave this machine  ·  wrightone.io")

    p.end()
    return img


# ─────────────────────────────────────────────────────────────────────────────
# Save both resolutions as PNG then combine into a multi-res TIFF
# ─────────────────────────────────────────────────────────────────────────────
ASSETS = Path(__file__).parent
PNG_1X = ASSETS / "dmg-bg-1x.png"
PNG_2X = ASSETS / "dmg-bg-2x.png"
TIFF_1X = ASSETS / "dmg-bg-1x.tiff"
TIFF_2X = ASSETS / "dmg-bg-2x.tiff"
OUT_TIFF = ASSETS / "dmg-background.tiff"

print("  Rendering 1× (800×680)…")
img1x = render(1)
img1x.save(str(PNG_1X))

print("  Rendering 2× (1600×1360)…")
img2x = render(2)
img2x.save(str(PNG_2X))

print("  Converting to TIFF with correct DPI tags (sips)…")
for (png, tiff, dpi) in [(PNG_1X, TIFF_1X, 72), (PNG_2X, TIFF_2X, 144)]:
    r = subprocess.run(
        ["sips", "-s", "format", "tiff",
         "-s", "dpiHeight", str(dpi),
         "-s", "dpiWidth",  str(dpi),
         str(png), "--out", str(tiff)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"  ✘  sips failed: {r.stderr.strip()}", file=sys.stderr)
        sys.exit(1)

print("  Merging into multi-resolution TIFF (tiffutil)…")
r = subprocess.run(
    ["tiffutil", "-cathidpicheck", str(TIFF_1X), str(TIFF_2X), "-out", str(OUT_TIFF)],
    capture_output=True, text=True,
)
if r.returncode != 0:
    print(f"  ✘  tiffutil failed: {r.stderr.strip()}", file=sys.stderr)
    sys.exit(1)

# Clean up intermediates
for f in (PNG_1X, PNG_2X, TIFF_1X, TIFF_2X):
    f.unlink(missing_ok=True)

print(f"✔  Saved {OUT_TIFF}  (1× 800×680 + 2× 1600×1360)")
