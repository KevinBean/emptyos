"""PDF export — drives Chromium via Playwright to print the assembled HTML.

Playwright is already an EmptyOS dev dep (for UI tests). If it's not installed
this function raises `PlaywrightMissing` with the install command.
"""

from __future__ import annotations

from pathlib import Path


class PlaywrightMissing(RuntimeError):
    """Raised when Playwright isn't installed. Message includes the install command."""


INSTALL_HINT = (
    "PDF export needs Playwright. Install with:\n"
    "    pip install playwright\n"
    "    playwright install chromium"
)


async def to_pdf(html: str, out_path: Path) -> None:
    """Render `html` to `out_path` as A4 PDF with 20mm margins and page numbers."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        raise PlaywrightMissing(INSTALL_HINT) from e

    out_path.parent.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            ctx = await browser.new_context()
            page = await ctx.new_page()
            # Give file:// figure references access — already supported by default.
            await page.set_content(html, wait_until="networkidle")
            await page.pdf(
                path=str(out_path),
                format="A4",
                margin={"top": "20mm", "bottom": "22mm", "left": "18mm", "right": "18mm"},
                display_header_footer=True,
                header_template='<div style="width:100%;font-size:8px;color:#888;padding:0 18mm;text-align:right;"><span class="title"></span></div>',
                footer_template=(
                    '<div style="width:100%;font-size:8px;color:#888;padding:0 18mm;">'
                    '<span style="float:left;"><span class="date"></span></span>'
                    '<span style="float:right;">Page <span class="pageNumber"></span> of <span class="totalPages"></span></span>'
                    "</div>"
                ),
                print_background=True,
                prefer_css_page_size=True,
            )
        finally:
            await browser.close()
