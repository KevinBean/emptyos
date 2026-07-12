"""One-off renderer: SVG -> PNG via Playwright headless Chromium.

Inlines the SVG content directly in the HTML to dodge file:// security
blocks. Usage: python brand/plekto/_render_preview.py <svg-name> [size]
"""
from __future__ import annotations

import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "icon-B-weave-P.svg"
    size = int(sys.argv[2]) if len(sys.argv) > 2 else 512

    here = Path(__file__).resolve().parent
    svg_path = here / name
    if not svg_path.exists():
        print(f"NOT FOUND: {svg_path}")
        sys.exit(1)

    svg_text = svg_path.read_text(encoding="utf-8")
    # Strip any width/height on the root <svg> so CSS sizes it
    import re
    svg_text = re.sub(r'\s(width|height)="[^"]*"', "", svg_text, count=2)

    out = svg_path.with_name(f"{svg_path.stem}_{size}px.png")
    html = f"""<!DOCTYPE html>
<html><head><style>
  html, body {{ margin:0; padding:0; background:#ffffff; }}
  .frame {{ width:{size}px; height:{size}px; }}
  .frame svg {{ width:100%; height:100%; display:block; }}
</style></head>
<body><div class="frame">{svg_text}</div></body></html>"""

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": size, "height": size})
        page = ctx.new_page()
        page.set_content(html, wait_until="load")
        page.locator(".frame").screenshot(path=str(out))
        browser.close()
    print(f"wrote {out}  ({out.stat().st_size} bytes)")
