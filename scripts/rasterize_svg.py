"""Rasterize SVG diagrams to 2x PNG — thin CLI over emptyos.sdk.svg_raster.

Part of the Published-site diagram standard (vault CLAUDE.md § "Article
diagram standard"): the SVG is the editable source; the shipped asset is a
2x PNG sibling, because share surfaces (LinkedIn, email) don't embed SVG.

Usage:
    python scripts/rasterize_svg.py <file.svg> [more.svg ...]   # sibling .png
    python scripts/rasterize_svg.py --stale <dir>               # all stale pairs under dir
    python scripts/rasterize_svg.py --scale 3 <file.svg>

The publish app also does this automatically for its site's images/ on every
build (and via POST /publish/api/diagrams/rasterize).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from emptyos.sdk.svg_raster import rasterize_svgs, stale_svg_pairs, svg_size  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="+", type=Path, help="SVG files, or a directory with --stale")
    ap.add_argument("--stale", action="store_true", help="treat paths as dirs; rasterize stale pairs")
    ap.add_argument("--scale", type=int, default=2)
    args = ap.parse_args()

    if args.stale:
        svgs = [s for d in args.paths for s in stale_svg_pairs(d)]
        if not svgs:
            print("all SVG/PNG pairs are fresh")
            return 0
    else:
        missing = [p for p in args.paths if not p.is_file()]
        if missing:
            print(f"not found: {', '.join(map(str, missing))}", file=sys.stderr)
            return 1
        svgs = args.paths

    for svg, png in zip(svgs, rasterize_svgs(svgs, scale=args.scale)):
        w, h = svg_size(svg.read_text(encoding="utf-8"))
        print(f"  {svg.name} -> {png.name} ({w * args.scale}x{h * args.scale})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
