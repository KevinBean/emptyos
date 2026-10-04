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


def _target_dimensions(svg_text: str, target_size: int | tuple[int, int] | None) -> tuple[int, int]:
    if target_size is None:
        return svg_size(svg_text)
    if isinstance(target_size, int):
        if target_size <= 0:
            raise ValueError("target_size must be positive")
        return target_size, target_size
    width, height = int(target_size[0]), int(target_size[1])
    if width <= 0 or height <= 0:
        raise ValueError("target_size dimensions must be positive")
    return width, height


def _render_svg_page(page, svg_text: str, width: int, height: int, output: Path, transparent: bool) -> None:
    """Render one SVG string into an exact CSS-pixel viewport."""
    page.set_viewport_size({"width": width, "height": height})
    background = "transparent" if transparent else "white"
    page.set_content(
        "<!doctype html><style>html,body{margin:0;width:100%;height:100%;"
        f"overflow:hidden;background:{background}}}svg{{display:block;width:100%;height:100%}}</style>"
        + svg_text,
        wait_until="load",
    )
    page.screenshot(path=str(output), omit_background=transparent)


def rasterize_svg(
    svg_path: Path,
    output_path: Path | None = None,
    *,
    target_size: int | tuple[int, int] | None = None,
    scale: int = 1,
    transparent: bool = False,
) -> Path:
    """Render one SVG at an exact target size, optionally with transparency.

    This is the icon/export-oriented sibling of :func:`rasterize_svgs`. Its
    defaults keep a one-CSS-pixel render; the older batch API retains its 2x,
    white-page publish behavior unchanged.
    """
    from playwright.sync_api import sync_playwright

    svg_path = Path(svg_path)
    svg_text = svg_path.read_text(encoding="utf-8")
    width, height = _target_dimensions(svg_text, target_size)
    if scale <= 0:
        raise ValueError("scale must be positive")
    width, height = width * scale, height * scale
    output = Path(output_path) if output_path else svg_path.with_suffix(".png")
    output.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": width, "height": height})
            _render_svg_page(page, svg_text, width, height, output, transparent)
            page.close()
        finally:
            browser.close()
    return output


def rasterize_svg_sizes(
    svg_path: Path,
    output_dir: Path,
    sizes: tuple[int, ...] | list[int],
    transparent: bool = True,
) -> list[Path]:
    """Render square ``icon-<size>.png`` variants in one browser session."""
    from playwright.sync_api import sync_playwright

    svg_path, output_dir = Path(svg_path), Path(output_dir)
    svg_text = svg_path.read_text(encoding="utf-8")
    clean_sizes = [int(size) for size in sizes]
    if not clean_sizes or any(size <= 0 for size in clean_sizes):
        raise ValueError("sizes must contain positive integers")
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": clean_sizes[0], "height": clean_sizes[0]})
            for size in clean_sizes:
                target = output_dir / f"icon-{size}.png"
                _render_svg_page(page, svg_text, size, size, target, transparent)
                outputs.append(target)
            page.close()
        finally:
            browser.close()
    return outputs
