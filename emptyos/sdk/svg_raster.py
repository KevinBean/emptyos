"""SVG diagram rasterization — SVG sources paired with shipped 2x PNGs.

The Published-site diagram standard (vault CLAUDE.md § "Article diagram
standard") keeps a hand-written SVG as the editable source and ships a 2x
raster PNG beside it, because articles get shared on surfaces that don't
embed SVG (LinkedIn, email, most RSS readers).

Consumers: ``scripts/rasterize_svg.py`` (CLI) and the publish app's
diagram endpoints + build hook (``apps/public/standard/publish/media.py``).

Pure module — no kernel import, safe to run standalone. ``rasterize_svgs``
uses Playwright's *sync* API, which refuses to run inside a running asyncio
loop: async callers must wrap it in ``asyncio.to_thread``.
"""
from __future__ import annotations

import re
from pathlib import Path

_VIEWBOX_RE = re.compile(r'viewBox="[\d.\-]+\s+[\d.\-]+\s+([\d.]+)\s+([\d.]+)"')
_WIDTH_RE = re.compile(r'width="([\d.]+)"')
_HEIGHT_RE = re.compile(r'height="([\d.]+)"')


def svg_size(svg_text: str) -> tuple[int, int]:
    """Pixel dimensions of an SVG, from viewBox (preferred) or width/height."""
    m = _VIEWBOX_RE.search(svg_text)
    if m:
        return int(float(m.group(1))), int(float(m.group(2)))
    mw, mh = _WIDTH_RE.search(svg_text), _HEIGHT_RE.search(svg_text)
    if mw and mh:
        return int(float(mw.group(1))), int(float(mh.group(1)))
    raise ValueError("SVG has no viewBox or width/height")


def stale_svg_pairs(root: Path) -> list[Path]:
    """SVGs under ``root`` (recursive) whose sibling ``.png`` is missing or older.

    A pair is ``name.svg`` + ``name.png`` in the same directory. Returns the
    SVG paths that need (re-)rasterizing, sorted for stable output. A missing
    root returns [] — callers treat "nothing to do" and "no such folder" alike.
    """
    if not root.is_dir():
        return []
    out: list[Path] = []
    for svg in root.rglob("*.svg"):
        png = svg.with_suffix(".png")
        if not png.exists() or png.stat().st_mtime < svg.stat().st_mtime:
            out.append(svg)
    return sorted(out)


def rasterize_svgs(paths: list[Path], scale: int = 2) -> list[Path]:
    """Render each SVG to a sibling PNG at ``scale``x via Playwright Chromium.

    Sync — never call from a running event loop (wrap in asyncio.to_thread).
    Raises ImportError when Playwright isn't installed; callers decide whether
    that's fatal (CLI) or a graceful skip (publish build hook).
    """
    from playwright.sync_api import sync_playwright

    out: list[Path] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for svg_path in paths:
                w, h = svg_size(svg_path.read_text(encoding="utf-8"))
                page = browser.new_page(
                    viewport={"width": w, "height": h},
                    device_scale_factor=scale,
                )
                page.goto(svg_path.resolve().as_uri())
                png_path = svg_path.with_suffix(".png")
                page.screenshot(path=str(png_path))
                page.close()
                out.append(png_path)
        finally:
            browser.close()
    return out
