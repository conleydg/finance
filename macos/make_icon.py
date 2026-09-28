"""Draws the app icon (an envelope on a blue tile) and writes Finance.icns / FinanceDemo.icns."""
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).parent


def tile(size: int, color: str, badge: bool) -> Image.Image:
    s = size / 1024
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([100 * s, 100 * s, 924 * s, 924 * s], radius=185 * s, fill=color)
    # envelope
    w = max(2, round(34 * s))
    box = [250 * s, 330 * s, 774 * s, 694 * s]
    d.rounded_rectangle(box, radius=40 * s, outline="white", width=w)
    d.line([(262 * s, 350 * s), (512 * s, 540 * s), (762 * s, 350 * s)], fill="white", width=w, joint="curve")
    if badge:
        d.rounded_rectangle([560 * s, 640 * s, 880 * s, 800 * s], radius=50 * s, fill="#e0b04a")
        d.text((600 * s, 668 * s), "DEMO", fill="#15181d", font_size=int(92 * s) or 1)
    return img


def build(name: str, color: str, badge: bool) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / f"{name}.iconset"
        iconset.mkdir()
        for px in (16, 32, 128, 256, 512):
            tile(px, color, badge).save(iconset / f"icon_{px}x{px}.png")
            tile(px * 2, color, badge).save(iconset / f"icon_{px}x{px}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(HERE / f"{name}.icns")], check=True)


if __name__ == "__main__":
    build("Finance", "#1f5fbf", False)
    build("FinanceDemo", "#1f5fbf", True)
    sys.exit(0)
