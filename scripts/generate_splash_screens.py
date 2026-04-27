"""Generate iOS PWA splash screens (apple-touch-startup-image).

Run once after the icon or background_color changes. Output goes to
emptyos/web/static/splash/ and is committed.

Apple ignores splash images that don't match exact device dimensions, so we
ship the most common iPhone + iPad sizes (portrait only). Each splash is the
manifest background_color with the app icon centered at ~30% of the shorter
side.

The `SIZES` list here must stay in sync with the device table in
emptyos/web/static/eos.js (search "apple-touch-startup-image"). If a row is
added or removed, update both.
"""

import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "emptyos" / "web" / "static"
ICON = STATIC / "icon-512.png"
OUT_DIR = STATIC / "splash"
MANIFEST = STATIC / "manifest.json"


def _bg_from_manifest() -> tuple[int, int, int, int]:
    hex_color = json.loads(MANIFEST.read_text())["background_color"].lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    return (r, g, b, 255)


# (width, height) — portrait.
SIZES = [
    (750, 1334),   # iPhone SE / 8
    (828, 1792),   # iPhone XR / 11
    (1125, 2436),  # iPhone X / XS / 11 Pro
    (1170, 2532),  # iPhone 12 / 13 / 14
    (1179, 2556),  # iPhone 14 Pro / 15 / 16
    (1242, 2688),  # iPhone XS Max / 11 Pro Max
    (1284, 2778),  # iPhone 12/13 Pro Max / 14 Plus
    (1290, 2796),  # iPhone 14/15/16 Pro Max
    (1536, 2048),  # iPad mini / Air (non-retina ratio)
    (1620, 2160),  # iPad 10.2
    (1668, 2224),  # iPad Air 10.5
    (1668, 2388),  # iPad Pro 11
    (2048, 2732),  # iPad Pro 12.9
]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    icon = Image.open(ICON).convert("RGBA")
    bg = _bg_from_manifest()

    for w, h in SIZES:
        canvas = Image.new("RGBA", (w, h), bg)
        target = int(min(w, h) * 0.30)
        scaled = icon.resize((target, target), Image.LANCZOS)
        x = (w - target) // 2
        y = (h - target) // 2
        canvas.alpha_composite(scaled, (x, y))
        out = OUT_DIR / f"splash-{w}x{h}.png"
        canvas.save(out, "PNG", optimize=True)
        print(f"  {out.relative_to(ROOT)}  ({out.stat().st_size // 1024} KB)")

    print(f"\nWrote {len(SIZES)} splash images to {OUT_DIR.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
