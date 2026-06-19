"""
Generate wright-telemetry.ico for Windows.

Renders each icon size natively with PyQt6 so geometry snaps to the pixel
grid at every size — no blurry downscaling from a large master image.

Requirements:
    pip install PyQt6 Pillow

Run from repo root:
    python assets/make_app_icon_win.py

Outputs:
    assets/wright-telemetry.png   -- 1024x1024 master PNG (shared with macOS)
    assets/wright-telemetry.ico   -- Windows multi-size icon
"""

import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QColor, QImage, QLinearGradient, QPainter, QBrush
from PyQt6.QtCore import Qt, QPointF, QRectF

app = QApplication.instance() or QApplication(sys.argv)

ASSETS  = Path(__file__).parent
PNG_OUT = ASSETS / "wright-telemetry.png"
ICO_OUT = ASSETS / "wright-telemetry.ico"

BG_TOP  = QColor("#1C2033")
BG_BOT  = QColor("#0D1017")
BLUE    = QColor("#3B82F6")
BLUE_LT = QColor("#60A5FA")
WHITE   = QColor("#FFFFFF")


def render(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    S = size

    # Scale factor relative to the reference 1024-px design
    sc = S / 1024.0

    # ── Background rounded square ─────────────────────────────────────────
    corner = max(1.0, 224 * sc)
    bg = QLinearGradient(QPointF(0, 0), QPointF(0, S))
    bg.setColorAt(0.0, BG_TOP)
    bg.setColorAt(1.0, BG_BOT)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(bg))
    p.drawRoundedRect(QRectF(0, 0, S, S), corner, corner)

    # ── Three bars ────────────────────────────────────────────────────────
    PAD_BOT = 200 * sc
    GAP     = 52  * sc
    BAR_R   = max(1.0, 28 * sc)
    BOTTOM  = S - PAD_BOT

    BIG_W, BIG_H = 234 * sc, 490 * sc
    SM_W, SM1_H, SM2_H = 152 * sc, 320 * sc, 210 * sc

    TOTAL_W = BIG_W + GAP + SM_W + GAP + SM_W
    START_X = (S - TOTAL_W) / 2

    bars = [
        (START_X,                              BOTTOM - BIG_H, BIG_W, BIG_H),
        (START_X + BIG_W + GAP,                BOTTOM - SM1_H, SM_W,  SM1_H),
        (START_X + BIG_W + GAP + SM_W + GAP,  BOTTOM - SM2_H, SM_W,  SM2_H),
    ]

    for i, (bx, by, bw, bh) in enumerate(bars):
        g = QLinearGradient(QPointF(bx + bw / 2, by), QPointF(bx + bw / 2, by + bh))
        if i == 0:
            g.setColorAt(0.0,  WHITE)
            g.setColorAt(0.55, BLUE_LT)
            g.setColorAt(1.0,  BLUE)
        else:
            g.setColorAt(0.0, BLUE_LT)
            g.setColorAt(1.0, BLUE)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawRoundedRect(QRectF(bx, by, bw, bh), BAR_R, BAR_R)

    p.end()
    return img


# ── Save 1024-px master PNG ───────────────────────────────────────────────────
master = render(1024)
if not master.save(str(PNG_OUT)):
    print(f"FAILED  Could not save {PNG_OUT}", file=sys.stderr)
    sys.exit(1)
print(f"OK  Master PNG saved: {PNG_OUT}")

# ── Build .ico from natively-rendered frames ──────────────────────────────────
try:
    from PIL import Image as PILImage
except ImportError:
    print("ERROR  Pillow not installed. Run: pip install Pillow", file=sys.stderr)
    sys.exit(1)

ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]
frames = []
for sz in ICO_SIZES:
    qt_img = render(sz)
    # QImage -> bytes -> PIL
    ptr = qt_img.bits()
    ptr.setsize(qt_img.sizeInBytes())
    pil = PILImage.frombuffer("RGBA", (sz, sz), bytes(ptr), "raw", "BGRA", 0, 1)
    frames.append(pil)
    print(f"   rendered {sz}x{sz}")

frames[0].save(
    str(ICO_OUT),
    format="ICO",
    sizes=[(s, s) for s in ICO_SIZES],
    append_images=frames[1:],
)
print(f"OK  Windows icon saved: {ICO_OUT}")
