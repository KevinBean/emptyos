"""Generate ``<brand-dir>/icon.ico`` from a brand directory's PNG suite.

Plays the role iconutil plays on macOS — packs multiple PNG sizes into a single
ICO so Windows picks the right resolution per context (Start menu, taskbar,
explorer, alt-tab).

Usage:
    python scripts/make-windows-icon.py                # brand/plekto (default)
    python scripts/make-windows-icon.py brand/emptyos  # any product's brand dir

Called by the product build workflow on the Windows runner. A standalone .py
rather than an inline shell snippet because PowerShell has no heredoc.

Sources are whatever the brand dir actually has: the ``icon_<N>px.png`` suite if
present, otherwise a single ``icon.png`` (Pillow downsamples it). Previously this
demanded a fixed six-file list including ``icon_64px.png``, which brand/plekto
has never had — so the script could only ever have exited 1.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent

# Sizes Windows uses across Start menu, taskbar, file explorer, alt-tab.
SIZES = [(16, 16), (32, 32), (64, 64), (128, 128), (256, 256)]


def source_images(brand: Path) -> list[Image.Image]:
    """Every usable PNG in the brand dir, largest first (Pillow downsamples
    from the largest source it is given)."""
    sized: list[tuple[int, Path]] = []
    for p in brand.glob("icon_*px.png"):
        m = re.match(r"icon_(\d+)px\.png$", p.name)
        if m:
            sized.append((int(m.group(1)), p))
    if sized:
        return [Image.open(p) for _, p in sorted(sized, reverse=True)]

    for name in ("icon.png", "icon-512.png", "icon-256.png"):
        p = brand / name
        if p.exists():
            return [Image.open(p)]
    return []


def main() -> int:
    brand = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "brand" / "plekto"
    if not brand.is_absolute():
        brand = ROOT / brand
    if not brand.is_dir():
        raise SystemExit(f"brand dir not found: {brand}")

    images = source_images(brand)
    if not images:
        raise SystemExit(f"no icon PNGs in {brand} (want icon_<N>px.png or icon.png)")

    out = brand / "icon.ico"
    largest = images[0]
    sizes = [s for s in SIZES if s[0] <= max(largest.size)]
    largest.save(out, format="ICO", sizes=sizes)
    print(f"wrote {out} ({out.stat().st_size} bytes, {len(sizes)} sizes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
