"""Generate brand/plekto/icon.ico from the existing PNG suite.

Plays the role iconutil plays on macOS — packs multiple PNG sizes into
a single ICO file so Windows resource compilers and installer wrappers
pick the right resolution per context.

Called by .github/workflows/build-plekto.yml on the Windows runner.
Standalone .py instead of an inline shell snippet because PowerShell
doesn't support bash heredoc syntax.
"""
from __future__ import annotations

from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "brand" / "plekto"
OUT = SRC / "icon.ico"

# Sizes Windows uses across Start menu, taskbar, file explorer, alt-tab.
SIZES = [(16, 16), (32, 32), (64, 64), (128, 128), (256, 256)]
SOURCES = ["icon_16px", "icon_32px", "icon_64px", "icon_128px", "icon_256px"]


def main() -> int:
    images = []
    for name in SOURCES:
        p = SRC / f"{name}.png"
        if not p.exists():
            raise SystemExit(f"missing source: {p}")
        images.append(Image.open(p))

    # Pillow packs all SIZES into the single .ico, picking the closest
    # source per size automatically.
    images[0].save(OUT, format="ICO", sizes=SIZES)
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes, {len(SIZES)} sizes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
