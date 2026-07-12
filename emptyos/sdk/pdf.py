"""Markdown → styled PDF rendering for EmptyOS.

One renderer so every PDF EmptyOS emits (CVs, reports, devlog exports, published
posts) follows a single markdown contract and a single visual system. Apps call
``BaseApp.render_pdf(...)``; the standalone ``render_markdown_pdf(...)`` is the
pure function (no kernel needed — testable directly).

Rendering toolchain: ``markdown_render.render_markdown`` (the shared vault-flavoured
markdown renderer) → HTML → Playwright Chromium ``page.pdf()``. No pandoc/weasyprint
dependency (neither is reliably present on Windows). Playwright is imported lazily
so importing this module is cheap; a missing dep raises a clear, actionable error
only when you actually render.

Vault notes carry the extended-markdown dialect, so the renderer handles ``[[wikilinks]]``
(resolved to display text — PDFs are static, no live links), ``> [!callouts]``,
and ``![[embeds]]`` via the shared renderer, plus ``%%comments%%`` (stripped),
``==highlight==`` (→ mark), and ``- [ ]`` task checkboxes (→ ☐/☑) locally. Web-only
heading permalinks are stripped.

THE MARKDOWN PROFILE (the contract every rendered doc follows) is specified in
``.claude/rules/pdf-markdown.md``. Summary:

  - YAML frontmatter (``---``) → metadata, NOT rendered.
  - Preamble between frontmatter and the masthead (an ``# H1`` title, ``>`` author
    notes) → NOT rendered. Authoring notes live here safely.
  - MASTHEAD = the first fenced code block (```` ``` ````). Inside it:
        line 1 → name / document title   (largest, accent colour)
        line 2 → subtitle / role          (medium)
        line 3+ → contact / meta lines     (mono, muted)
    Optional — a doc with no fenced block renders body-only from the top.
  - ``## Heading``  → section header (accent + bottom rule + pop tick)
  - ``### Heading`` → entry title (e.g. a job/role); the paragraph immediately
    after it renders as muted meta (location / dates).
  - ``- item`` lists, ``**bold**`` (accent), ``| tables |``, ``---`` divider — standard.
  - Each ``###`` entry is wrapped so it never splits across a page break.

Visual system comes from ``PdfStyle`` — pass a brand palette to theme the output
(e.g. colours scraped from a company site via Playwright getComputedStyle).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PdfStyle:
    """Visual system for a rendered PDF. Defaults are EmptyOS-neutral; override
    ``accent`` / ``pop`` to brand the output."""

    accent: str = "#1a3a5c"      # section headers + masthead name
    pop: str = "#6c8cff"         # thin accent tick on header rule + section bars
    text: str = "#14181c"        # body text
    muted: str = "#6f6f6f"       # meta lines, entry sub-text
    border: str = "#dbe3db"      # rules + table borders
    th_bg: str = "#eef3f8"       # table header fill
    font: str = "'Inter','Segoe UI','Helvetica Neue',Arial,sans-serif"
    mono: str = "'Consolas','SF Mono',monospace"
    page_size: str = "A4"
    margin: str = "14mm 15mm"    # @page + pdf() margin (kept in sync)

    def margin_dict(self) -> dict:
        parts = self.margin.split()
        v = parts[0]
        h = parts[1] if len(parts) > 1 else parts[0]
        return {"top": v, "bottom": v, "left": h, "right": h}


# ── Named themes ──────────────────────────────────────────────────────────────
# One registry so render style is selected by name, not hand-passed hex. Add a
# theme here and every caller can use it via ``style="<name>"``. Brand themes are
# typically derived by scraping a company's palette (Playwright getComputedStyle).
PDF_THEMES: dict[str, PdfStyle] = {
    "default": PdfStyle(),  # EmptyOS-neutral slate
    "forest": PdfStyle(accent="#003b0e", pop="#ccff33", th_bg="#eef4ee"),  # deep green + lime
    "slate": PdfStyle(accent="#334155", pop="#64748b", th_bg="#f1f5f9"),   # cool professional
    "mono": PdfStyle(accent="#1a1a1a", pop="#888888", th_bg="#f4f4f4"),    # black/white minimal
}


def resolve_pdf_style(style: "PdfStyle | str | None" = None) -> PdfStyle:
    """Resolve a style arg to a PdfStyle: None → 'default', a theme name → registry
    lookup (unknown name falls back to 'default'), a PdfStyle → returned as-is."""
    if style is None:
        return PDF_THEMES["default"]
    if isinstance(style, PdfStyle):
        return style
    if isinstance(style, str):
        return PDF_THEMES.get(style.lower().strip(), PDF_THEMES["default"])
    raise TypeError(f"style must be a PdfStyle, theme name, or None — got {type(style).__name__}")


def _split_frontmatter(md: str) -> str:
    if md.startswith("---"):
        parts = md.split("---", 2)
        if len(parts) == 3:
            return parts[2]
    return md


def _obsidian_clean(text: str) -> str:
    """Convert/strip the Obsidian-only syntax that python-markdown leaves raw.

    ``render_markdown`` already handles [[wikilinks]], > [!callouts] and ![[embeds]].
    This covers the rest so vault notes don't render with literal markup:
      - ``%%comments%%`` (inline + multi-line) → removed (never meant for output)
      - ``==highlight==`` → <mark>
      - ``- [ ]`` / ``- [x]`` task checkboxes → ☐ / ☑ glyphs (no raw brackets)
    """
    text = re.sub(r"%%.*?%%", "", text, flags=re.S)
    text = re.sub(r"==(.+?)==", r"<mark>\1</mark>", text)
    text = re.sub(r"(?m)^(\s*[-*]\s+)\[ \]\s+", r"\1☐ ", text)
    text = re.sub(r"(?m)^(\s*[-*]\s+)\[[xX]\]\s+", r"\1☑ ", text)
    return text


def _extract_masthead(md: str) -> tuple[list[str], str]:
    """Return (masthead_lines, body_md). Masthead = first fenced code block;
    everything before it is dropped (authoring preamble), everything after is body.
    No fence → ([], whole content)."""
    lines = md.splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip().startswith("```"):
        i += 1
    if i >= len(lines):
        return [], md.strip()
    i += 1
    masthead: list[str] = []
    while i < len(lines) and not lines[i].strip().startswith("```"):
        masthead.append(lines[i])
        i += 1
    i += 1  # past closing fence
    return [l for l in masthead if l.strip()], "\n".join(lines[i:]).strip()


def _wrap_entries(body_html: str) -> str:
    """Wrap each ``<h3>`` entry (heading + following block up to the next h2/h3)
    in a no-split container so a heading never strands at a page break."""
    segs = re.split(r"(?=<h3>)", body_html)
    out = [segs[0]]
    for seg in segs[1:]:
        m = re.search(r"<h2", seg)
        if m:
            out.append(f'<div class="entry">{seg[:m.start()]}</div>{seg[m.start():]}')
        else:
            out.append(f'<div class="entry">{seg}</div>')
    return "".join(out)


def _build_html(masthead: list[str], body_html: str, s: PdfStyle) -> str:
    name = masthead[0].strip() if masthead else ""
    subtitle = masthead[1].strip() if len(masthead) > 1 else ""
    meta = " &nbsp;·&nbsp; ".join(l.strip() for l in masthead[2:])
    hdr = ""
    if masthead:
        hdr = (
            '<div class="masthead">'
            f'<div class="mh-name">{name}</div>'
            + (f'<div class="mh-sub">{subtitle}</div>' if subtitle else "")
            + (f'<div class="mh-meta">{meta}</div>' if meta else "")
            + "</div>"
        )
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
@page {{ size: {s.page_size}; margin: {s.margin}; }}
* {{ box-sizing: border-box; }}
body {{ font-family: {s.font}; font-size: 10pt; line-height: 1.4; color: {s.text}; }}
.masthead {{ position: relative; border-bottom: 2px solid {s.accent}; padding-bottom: 10px; margin-bottom: 15px; }}
.masthead::after {{ content:''; position:absolute; left:0; bottom:-2px; width:72px; height:2px; background:{s.pop}; }}
.mh-name {{ font-size: 22pt; font-weight: 800; letter-spacing: -.4px; color: {s.accent}; line-height: 1.04; }}
.mh-sub {{ font-size: 10.5pt; font-weight: 600; color: {s.text}; margin-top: 4px; }}
.mh-meta {{ font-family: {s.mono}; font-size: 8.3pt; color: {s.muted}; margin-top: 7px; letter-spacing: -.2px; }}
h2 {{ position: relative; font-size: 10pt; text-transform: uppercase; letter-spacing: 1.3px; color: {s.accent}; font-weight: 800; border-bottom: 1px solid {s.border}; padding: 0 0 3px 11px; margin: 15px 0 7px; break-after: avoid; }}
h2::before {{ content:''; position:absolute; left:0; top:1px; bottom:4px; width:3px; background:{s.pop}; }}
h3 {{ font-size: 10.5pt; margin: 10px 0 1px; color: {s.text}; font-weight: 700; break-after: avoid; }}
h3 + p {{ color: {s.muted}; font-size: 9pt; margin-top: 0; margin-bottom: 4px; }}
.entry {{ break-inside: avoid; }}
p {{ margin: 4px 0; }}
ul {{ margin: 4px 0 9px; padding-left: 17px; }}
li {{ margin: 3px 0; }}
li::marker {{ color: {s.accent}; }}
strong {{ color: {s.accent}; }}
em {{ color: {s.muted}; }}
table {{ border-collapse: collapse; width: 100%; margin: 4px 0 9px; font-size: 9.2pt; break-inside: avoid; }}
th, td {{ border: 1px solid {s.border}; padding: 3px 7px; text-align: left; vertical-align: top; }}
th {{ background: {s.th_bg}; color: {s.accent}; font-weight: 700; }}
hr {{ border: none; border-top: 1px solid {s.border}; margin: 9px 0; }}
a {{ color: {s.accent}; text-decoration: none; }}
mark {{ background: {s.pop}; color: {s.text}; padding: 0 2px; border-radius: 2px; }}
.wikilink, .wikilink-private {{ color: {s.accent}; font-weight: 600; }}
.callout {{ border-left: 3px solid {s.pop}; background: {s.th_bg}; padding: 6px 12px; margin: 8px 0; border-radius: 4px; break-inside: avoid; }}
.callout-title {{ color: {s.accent}; }}
</style></head><body>
{hdr}
{body_html}
</body></html>"""


def render_markdown_pdf(markdown_text: str, out_path, *, style: "PdfStyle | str | None" = None) -> Path:
    """Render markdown to a styled PDF following the EmptyOS PDF markdown profile.

    See module docstring + ``.claude/rules/pdf-markdown.md`` for the contract.
    ``out_path`` is where the PDF is written (str or Path). ``style`` may be a
    ``PdfStyle``, a theme name from ``PDF_THEMES``, or None (→ ``"default"``).
    Returns the Path. Raises ``RuntimeError`` if a dependency is missing.

    SYNC + Playwright sync API: do NOT call this inside a running asyncio loop
    (it will raise). From an app/daemon context use ``BaseApp.render_pdf`` (it
    offloads to a thread); this function is for scripts, CLI, and tests.
    """
    from emptyos.sdk.markdown_render import HAS_MARKDOWN, render_markdown

    if not HAS_MARKDOWN:
        raise RuntimeError("render_markdown_pdf needs the 'markdown' package: pip install markdown")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "render_markdown_pdf needs Playwright Chromium: pip install playwright && playwright install chromium"
        ) from e

    s = resolve_pdf_style(style)
    out = Path(out_path)
    md_body = _split_frontmatter(markdown_text)
    masthead, body_md = _extract_masthead(md_body)
    # Vault notes are Obsidian-flavored. render_markdown resolves [[wikilinks]] to
    # display text (published_slugs=None), renders > [!callouts] and ![[embeds]];
    # _obsidian_clean handles %%comments%% / ==highlight== / task checkboxes.
    body_md = _obsidian_clean(body_md)
    body_html, _toc = render_markdown(body_md, published_slugs=None)
    # Strip web-only heading permalinks (toc extension) — meaningless in a PDF.
    body_html = re.sub(r'<a[^>]*class="header-link"[^>]*>.*?</a>', "", body_html)
    body_html = _wrap_entries(body_html)
    html = _build_html(masthead, body_html, s)

    return _chromium_pdf(html, out, page_size=s.page_size, margin=s.margin_dict())


def render_html_pdf(
    html: str,
    out_path,
    *,
    page_size: str = "A4",
    margin: "dict | None" = None,
) -> Path:
    """Render a self-contained HTML string straight to PDF (no markdown pipeline).

    Sibling of ``render_markdown_pdf`` for shapes markdown can't express (e.g.
    SVG music notation — see ``.claude/rules/pdf-markdown.md`` graduation note).
    The HTML must be fully self-contained: inline CSS + inline JS + inline data,
    no network fetches — ``set_content`` runs scripts before ``networkidle``, so
    script-generated DOM (SVG) exists when the PDF snapshots.

    SYNC + Playwright sync API: never call inside a running asyncio loop — from
    an app use ``await asyncio.to_thread(render_html_pdf, ...)``.
    """
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            "render_html_pdf needs Playwright Chromium: pip install playwright && playwright install chromium"
        ) from e
    margin = margin or {"top": "14mm", "bottom": "14mm", "left": "13mm", "right": "13mm"}
    return _chromium_pdf(html, Path(out_path), page_size=page_size, margin=margin)


def _chromium_pdf(html: str, out: Path, *, page_size: str, margin: dict) -> Path:
    """Shared Chromium print step: HTML string → PDF file at ``out``."""
    from playwright.sync_api import sync_playwright

    out.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.set_content(html, wait_until="networkidle")
        pg.pdf(path=str(out), format=page_size, print_background=True, margin=margin)
        b.close()
    return out


# ─── PDF text extraction (the read side) ────────────────────────────
#
# This module renders markdown → PDF; the two helpers below go the other way,
# pulling text back OUT of a born-digital PDF. They live here because "PDF
# utilities" is one coherent home, and because two consumers now need them:
# assistant's file-attachment extractor and reader's book importer.
#
# Both are synchronous — wrap in ``asyncio.to_thread`` when on the event loop.
#
# NOTE: a *scanned* PDF has no text layer; these return nothing useful for one.
# Probe with ``pdf_text_stats`` first, and route scans to the `ocr` plugin.


def extract_pdf_text(path) -> str:
    """Born-digital PDF → plain text. Raises ImportError when pypdf is absent.

    Pages are joined with blank lines; a page that fails to parse is skipped
    rather than failing the whole document.
    """
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        try:
            from PyPDF2 import PdfReader  # type: ignore
        except ImportError as e:
            raise ImportError("pypdf (or PyPDF2)") from e
    reader = PdfReader(str(path))
    parts: list[str] = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n\n".join(p for p in parts if p.strip())


def pdf_text_stats(path, *, sample_pages: int = 5, min_chars_per_page: int = 200):
    """Cheap in-process probe: ``(page_count, has_text_layer)``.

    ``has_text_layer`` is True (born-digital), False (scanned — OCR it), or
    None (couldn't determine — pypdf missing / unreadable file). Samples the
    first ``sample_pages`` pages so a 500-page PDF costs a few
    ``extract_text()`` calls. Never raises.
    """
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        return None, None
    try:
        reader = PdfReader(str(path))
        n = len(reader.pages)
    except Exception:
        return None, None
    if n == 0:
        return 0, None
    sample = list(reader.pages[:sample_pages]) if 0 < sample_pages < n else list(reader.pages)
    chars = 0
    for pg in sample:
        try:
            chars += len((pg.extract_text() or "").strip())
        except Exception:
            continue
    avg = chars / max(1, len(sample))
    return n, (avg >= min_chars_per_page)
