"""
Generate wright-telemetry.ico for Windows from the same design as the macOS icon.

Requirements:
    • PyQt6     (pip install PyQt6)
    • Pillow    (pip install Pillow)

Run from repo root:
    python assets/make_app_icon_win.py

Outputs:
    assets/wright-telemetry.png   — 1024×1024 master PNG (shared with macOS)
    assets/wright-telemetry.ico   — Windows multi-size icon
"""

import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QColor, QImage, QLinearGradient, QPainter, QBrush
from PyQt6.QtCore import Qt, QPointF, QRectF

app = QApplication.instance() or QApplication(sys.argv)

SIZE = 1024
img = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32_Premultiplied)
img.fill(QColor(0, 0, 0, 0))

p = QPainter(img)
p.setRenderHint(QPainter.RenderHint.Antialiasing)
S = SIZE

BG_TOP  = QColor("#1C2033")
BG_BOT  = QColor("#0D1017")
BLUE    = QColor("#3B82F6")
BLUE_LT = QColor("#60A5FA")
WHITE   = QColor("#FFFFFF")

CORNER = 224
bg = QLinearGradient(QPointF(0, 0), QPointF(0, S))
bg.setColorAt(0.0, BG_TOP)
bg.setColorAt(1.0, BG_BOT)
p.setPen(Qt.PenStyle.NoPen)
p.setBrush(QBrush(bg))
p.drawRoundedRect(QRectF(0, 0, S, S), CORNER, CORNER)

PAD_X   = 168
PAD_BOT = 200
BAR_R   = 28
GAP     = 52
BOTTOM  = S - PAD_BOT
BIG_W, BIG_H = 234, 490
SM_W, SM1_H, SM2_H = 152, 320, 210
TOTAL_W = BIG_W + GAP + SM_W + GAP + SM_W
START_X = (S - TOTAL_W) // 2

bars = [
    (START_X, BOTTOM - BIG_H, BIG_W, BIG_H),
    (START_X + BIG_W + GAP, BOTTOM - SM1_H, SM_W, SM1_H),
    (START_X + BIG_W + GAP + SM_W + GAP, BOTTOM - SM2_H, SM_W, SM2_H),
]

for i, (bx, by, bw, bh) in enumerate(bars):
    bar_g = QLinearGradient(QPointF(bx + bw / 2, by), QPointF(bx + bw / 2, by + bh))
    if i == 0:
        bar_g.setColorAt(0.0, WHITE)
        bar_g.setColorAt(0.55, BLUE_LT)
        bar_g.setColorAt(1.0, BLUE)
    else:
        bar_g.setColorAt(0.0, BLUE_LT)
        bar_g.setColorAt(1.0, BLUE)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(bar_g))
    p.drawRoundedRect(QRectF(bx, by, bw, bh), BAR_R, BAR_R)

p.end()

ASSETS  = Path(__file__).parent
PNG_OUT = ASSETS / "wright-telemetry.png"
ICO_OUT = ASSETS / "wright-telemetry.ico"

if not img.save(str(PNG_OUT)):
    print("FAILED  Failed to save {PNG_OUT}", file=sys.stderr)
    sys.exit(1)
print(f"OK  Master PNG saved: {PNG_OUT}")

try:
    from PIL import Image as PILImage
except ImportError:
    print("ERROR  Pillow not installed. Run: pip install Pillow", file=sys.stderr)
    sys.exit(1)

src = PILImage.open(PNG_OUT).convert("RGBA")
sizes = [16, 24, 32, 48, 64, 128, 256]
frames = [src.resize((s, s), PILImage.LANCZOS) for s in sizes]
frames[0].save(str(ICO_OUT), format="ICO", sizes=[(s, s) for s in sizes],
               append_images=frames[1:])
print(f"OK  Windows icon saved: {ICO_OUT}")
