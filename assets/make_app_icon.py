"""
Generate the WrightData app icon and convert it to .icns.

Design: three vertical rounded bars on a dark background —
one tall bar (left) + two shorter bars (right), in brand blue.

Requirements:
    • PyQt6     (pip install PyQt6)
    • sips + iconutil  (built into macOS)

Run from repo root:
    python assets/make_app_icon.py

Outputs:
    assets/wright-telemetry.png   — 1024×1024 master PNG
    assets/wright-telemetry.icns  — macOS icon bundle (all sizes)
"""

import shutil
import subprocess
import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import (
    QColor, QImage, QLinearGradient,
    QPainter, QPen, QBrush,
)
from PyQt6.QtCore import Qt, QPointF, QRectF

app = QApplication.instance() or QApplication(sys.argv)

# ─────────────────────────────────────────────────────────────────────────────
# Canvas
# ─────────────────────────────────────────────────────────────────────────────
SIZE = 1024
img = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32_Premultiplied)
img.fill(QColor(0, 0, 0, 0))

p = QPainter(img)
p.setRenderHint(QPainter.RenderHint.Antialiasing)

S = SIZE

# ─────────────────────────────────────────────────────────────────────────────
# Colors
# ─────────────────────────────────────────────────────────────────────────────
BG_TOP   = QColor("#1C2033")
BG_BOT   = QColor("#0D1017")
BLUE     = QColor("#3B82F6")   # ACCENT_BLUE
BLUE_LT  = QColor("#60A5FA")   # lighter blue for big bar top
WHITE    = QColor("#FFFFFF")

# ─────────────────────────────────────────────────────────────────────────────
# 1. Background — dark rounded square
# ─────────────────────────────────────────────────────────────────────────────
CORNER = 224
bg = QLinearGradient(QPointF(0, 0), QPointF(0, S))
bg.setColorAt(0.0, BG_TOP)
bg.setColorAt(1.0, BG_BOT)
p.setPen(Qt.PenStyle.NoPen)
p.setBrush(QBrush(bg))
p.drawRoundedRect(QRectF(0, 0, S, S), CORNER, CORNER)

# ─────────────────────────────────────────────────────────────────────────────
# 2. Three bars
#
#   [ BIG  ] [ sm ] [ sm ]
#
#   All bars are bottom-anchored.
#   Big bar: tall, wide.  Two small bars: shorter, narrower.
# ─────────────────────────────────────────────────────────────────────────────
PAD_X   = 168   # left/right padding
PAD_BOT = 200   # distance from icon bottom to bar bottom
BAR_R   = 28    # corner radius on bars
GAP     = 52    # gap between bars

BOTTOM  = S - PAD_BOT

# Bar dimensions
BIG_W   = 234
BIG_H   = 490

SM_W    = 152
SM1_H   = 320
SM2_H   = 210

TOTAL_W = BIG_W + GAP + SM_W + GAP + SM_W   # 652
START_X = (S - TOTAL_W) // 2                # left edge of big bar (≈ 186)

# Bar positions (left-x, top-y, width, height)
bars = [
    (START_X,                         BOTTOM - BIG_H, BIG_W, BIG_H),
    (START_X + BIG_W + GAP,           BOTTOM - SM1_H, SM_W,  SM1_H),
    (START_X + BIG_W + GAP + SM_W + GAP, BOTTOM - SM2_H, SM_W,  SM2_H),
]

for i, (bx, by, bw, bh) in enumerate(bars):
    # Vertical gradient: lighter at top, full blue at bottom
    bar_g = QLinearGradient(QPointF(bx + bw / 2, by), QPointF(bx + bw / 2, by + bh))
    if i == 0:
        # Big bar: white → blue
        bar_g.setColorAt(0.0, WHITE)
        bar_g.setColorAt(0.55, BLUE_LT)
        bar_g.setColorAt(1.0, BLUE)
    else:
        # Small bars: light-blue → blue
        bar_g.setColorAt(0.0, BLUE_LT)
        bar_g.setColorAt(1.0, BLUE)

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(bar_g))
    p.drawRoundedRect(QRectF(bx, by, bw, bh), BAR_R, BAR_R)

p.end()

# ─────────────────────────────────────────────────────────────────────────────
# Save master PNG
# ─────────────────────────────────────────────────────────────────────────────
ASSETS   = Path(__file__).parent
PNG_OUT  = ASSETS / "wright-telemetry.png"
ICNS_OUT = ASSETS / "wright-telemetry.icns"

if not img.save(str(PNG_OUT)):
    print(f"✘  Failed to save {PNG_OUT}", file=sys.stderr)
    sys.exit(1)

print(f"✔  Master PNG saved: {PNG_OUT}")

# ─────────────────────────────────────────────────────────────────────────────
# Build .icns  (sips + iconutil — bundled with macOS)
# ─────────────────────────────────────────────────────────────────────────────
ICONSET = ASSETS / "wright.iconset"
ICONSET.mkdir(exist_ok=True)

sizes = [16, 32, 64, 128, 256, 512, 1024]

print("  Generating iconset sizes…")
for sz in sizes:
    for scale, suffix in [(1, ""), (2, "@2x")]:
        px = sz * scale
        if px > 1024:
            continue
        out_name = f"icon_{sz}x{sz}{suffix}.png"
        result = subprocess.run(
            ["sips", "-z", str(px), str(px), str(PNG_OUT),
             "--out", str(ICONSET / out_name)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"  ✘  sips failed for {out_name}: {result.stderr.strip()}", file=sys.stderr)
            sys.exit(1)
        print(f"     {out_name}  ({px}×{px})")

print("  Running iconutil…")
result = subprocess.run(
    ["iconutil", "-c", "icns", str(ICONSET), "-o", str(ICNS_OUT)],
    capture_output=True, text=True,
)
if result.returncode != 0:
    print(f"  ✘  iconutil failed: {result.stderr.strip()}", file=sys.stderr)
    sys.exit(1)

shutil.rmtree(ICONSET)
print(f"✔  Icon bundle saved: {ICNS_OUT}")
