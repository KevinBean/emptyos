"""HTML → PNG frame rendering for EmptyOS.

The video-frame sibling of ``emptyos.sdk.pdf``: render an HTML template to a
fixed-size PNG via Playwright Chromium ``page.screenshot()`` (vs ``page.pdf()``).
This is the genuinely-new capability borrowed in *shape* from AIDC-AI
Pixelle-Video's HTML frame templates (``templates/<size>/<style>.html`` rendered
per frame) — see the repo-review note ``30_Resources/Web-Clips/2026-06-08 AIDC-AI
- Pixelle-Video…``. EmptyOS already owns the toolchain (this module reuses the
exact lazy-Playwright pattern ``pdf.py`` established); a *static* HTML frame needs
no GPU and no cloud, so the cheap path stays local-first.

Pure functions (no kernel) — testable directly. Playwright is imported lazily so
importing this module is cheap; a missing dep raises an actionable error only when
you actually render.

Two cross-cutting uses make this worth an SDK primitive at one consumer (the same
reasoning that justified ``render_markdown_pdf``): music-video lyric/caption cards
(first consumer: music-studio ``mode="cards"``) and, later, og-images / thumbnails
/ slide frames. ``render_html_frames`` launches ONE browser for a batch (N cards =
N pages, one launch); ``render_html_frame`` is the single-item convenience.

ASYNC CALLERS MUST OFFLOAD: ``sync_playwright`` blocks the event loop. From an
async path wrap the call in ``await asyncio.to_thread(render_html_frames, …)`` —
see ``.claude/rules/dev-gotchas.md`` / the "sync-call-in-async wedge".
"""

from __future__ import annotations

import html as _html
from pathlib import Path


def render_html_frames(
    items: list[tuple[str, str | Path]],
    *,
    width: int = 1280,
    height: int = 720,
    scale: int = 1,
    transparent: bool = False,
) -> list[Path]:
    """Render ``[(html, out_path), …]`` to PNG, one browser for the whole batch.

    Each page's viewport is ``width × height`` (× ``scale`` device pixels), so the
    screenshot is exactly that size — the HTML should fill the viewport. Returns
    the written paths in input order. Blocking — offload from async callers."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # pragma: no cover - dep-missing path
        raise RuntimeError(
            "render_html_frame needs Playwright Chromium: "
            "pip install playwright && playwright install chromium"
        ) from e

    out_paths: list[Path] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for html, out_path in items:
                out = Path(out_path)
                out.parent.mkdir(parents=True, exist_ok=True)
                page = browser.new_page(
                    viewport={"width": width, "height": height},
                    device_scale_factor=scale,
                )
                page.set_content(html, wait_until="networkidle")
                page.screenshot(path=str(out), omit_background=transparent, type="png")
                page.close()
                out_paths.append(out)
        finally:
            browser.close()
    return out_paths


def render_html_frame(
    html: str,
    out_path: str | Path,
    *,
    width: int = 1280,
    height: int = 720,
    scale: int = 1,
    transparent: bool = False,
) -> Path:
    """Render one HTML string to a PNG of ``width × height``. See
    :func:`render_html_frames` for the batch form (cheaper for many frames)."""
    return render_html_frames(
        [(html, out_path)], width=width, height=height, scale=scale, transparent=transparent,
    )[0]


def caption_card_html(
    text: str,
    *,
    subtitle: str = "",
    width: int = 1280,
    height: int = 720,
    theme: str = "dark",
    accent: str = "#6c8cff",
    font: str = "'Inter','Segoe UI',system-ui,-apple-system,sans-serif",
) -> str:
    """A full-bleed centred caption card — the one built-in frame template.

    Drives the lyric/quote-card video aesthetic that AI-image generation can't do
    cheaply. ``text`` is the headline (a lyric / scene line); ``subtitle`` is an
    optional smaller line. ``theme`` ∈ ``dark`` | ``light``. Sized to fill the
    ``width × height`` viewport so :func:`render_html_frame` captures it exactly.
    Apps wanting more styles add sibling builders here (the Pixelle template set
    is the reference for what's worth adding next)."""
    if theme == "light":
        bg, fg, sub_c = "#f6f7f9", "#14181c", "#5b636c"
    else:
        bg, fg, sub_c = "#0b0d10", "#f2f4f8", "#9aa3ad"
    title_px = max(28, int(height * 0.075))
    sub_px = max(16, int(height * 0.030))
    text_e = _html.escape(text or "")
    sub_e = _html.escape(subtitle or "")
    sub_html = f'<div class="sub">{sub_e}</div>' if subtitle else ""
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"><style>"
        "*{margin:0;box-sizing:border-box}"
        f"html,body{{width:{width}px;height:{height}px;overflow:hidden}}"
        "body{display:flex;flex-direction:column;align-items:center;justify-content:center;"
        f"gap:.45em;padding:8% 10%;text-align:center;background:{bg};color:{fg};"
        f"font-family:{font};-webkit-font-smoothing:antialiased}}"
        f".accent{{width:64px;height:5px;border-radius:3px;background:{accent};margin-bottom:.5em}}"
        f".title{{font-size:{title_px}px;line-height:1.18;font-weight:650;"
        "letter-spacing:-.01em;text-wrap:balance}"
        f".sub{{font-size:{sub_px}px;color:{sub_c};font-weight:450}}"
        "</style></head><body>"
        '<div class="accent"></div>'
        f'<div class="title">{text_e}</div>'
        f"{sub_html}"
        "</body></html>"
    )
