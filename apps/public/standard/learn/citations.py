"""Learn — citation parser for PDF page anchors.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: regex matcher for ``[[<ref-slug>]] p.<N>`` (or ``pp.<N>-<M>``,
en-dash and hyphen accepted) and the substitution that turns those into
HTML button placeholders the lesson player binds to ``EOS_PDF.open()``.

The substitution is **async** because it has to look up each referenced
slug via ``call_app("kb", "get_note", slug=...)`` to check for
``local_pdf:`` frontmatter — only then is the citation upgraded to a
button.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``self.call_app`` (BaseApp).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import LearnApp  # noqa: F401 — for type hints only


# ─── Bind to LearnApp class as ───────────────────────────────────────
#   _annotate_pdf_citations = _citations._annotate_pdf_citations  # async
#   _annotate_wikilinks     = _citations._annotate_wikilinks      # sync
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


# Matches Obsidian wikilinks: [[slug]] or [[slug|display alias]].
# Excludes image embeds (![[...]]) and matches already inside HTML tags are
# rare in markdown body (markdown HTML passes through; we'd only see literal
# `[[...]]` text in body prose).
_WIKILINK_RE = re.compile(
    r"(?<!\!)\[\[(?P<target>[^\]\|]+?)(?:\|(?P<alias>[^\]]+))?\]\]",
)


def _annotate_wikilinks(self, body_md: str) -> str:
    """Convert `[[slug]]` and `[[slug|alias]]` to standard markdown links
    pointing at the kb app. Runs AFTER `_annotate_pdf_citations` so any
    wikilink already upgraded to a PDF-anchor button is left alone.

    Doesn't try to detect whether the slug exists — kb resolves at click
    time and shows "not found" if missing. Cheaper than per-link lookup,
    and tolerates references to notes that exist but aren't kb-indexed.
    """
    if not body_md or "[[" not in body_md:
        return body_md
    from urllib.parse import quote

    def _sub(m: re.Match) -> str:
        target = m.group("target").strip()
        alias = (m.group("alias") or "").strip() or target
        # kb uses hash routing — `#<slug>` triggers showDetail() on load.
        # Quote the slug so embedded `/` survives the URL fragment.
        url = f"/kb/#{quote(target, safe='')}"
        # Standard markdown link — marked.js renders this as <a href=...>.
        return f'[{alias}]({url})'

    return _WIKILINK_RE.sub(_sub, body_md)


# Matches: [[<slug>]] p.<N>     or  [[<slug>]] p<N>
#       or [[<slug>]] pp.<N>-<M>  or [[<slug>]] pp.<N>–<M>  (hyphen / en-dash)
# Captures: (slug, first_page_int_str, optional_end_page_str)
_CITATION_RE = re.compile(
    r"\[\[([^\]\|]+?)(?:\|[^\]]*)?\]\]\s*(?:pp?\.?\s*)(\d+)(?:\s*[-–]\s*(\d+))?",
    re.IGNORECASE,
)


async def _annotate_pdf_citations(self, body_md: str) -> str:
    """Walk `body_md`, find `[[ref-slug]] p.<N>` patterns, and for any
    referenced note that carries a `local_pdf:` frontmatter field, replace
    the citation text with an HTML `<button>` the frontend can bind to
    `EOS_PDF.open()`. Citations to notes without `local_pdf:` are left
    untouched (rendered as the original markdown text)."""
    if not body_md:
        return body_md

    matches = list(_CITATION_RE.finditer(body_md))
    if not matches:
        return body_md

    # Cache per-slug lookups so we don't ping kb twice for the same ref.
    slug_pdfs: dict[str, str | None] = {}

    async def _resolve_pdf(slug: str) -> str | None:
        if slug in slug_pdfs:
            return slug_pdfs[slug]
        pdf_path: str | None = None
        try:
            res = await self.call_app("kb", "get_note", slug=slug)
            if isinstance(res, dict) and not res.get("error"):
                props = res.get("properties") or {}
                lp = props.get("local_pdf")
                if isinstance(lp, str) and lp.strip():
                    pdf_path = lp.strip()
        except Exception:
            pdf_path = None
        slug_pdfs[slug] = pdf_path
        return pdf_path

    # Walk left-to-right, building the output by alternating original
    # spans and (when applicable) replacement HTML.
    out_parts: list[str] = []
    cursor = 0
    for m in matches:
        slug = m.group(1).strip()
        page = m.group(2)
        # Append the un-touched span before this match.
        out_parts.append(body_md[cursor:m.start()])
        cursor = m.end()
        pdf_path = await _resolve_pdf(slug)
        if not pdf_path:
            # Leave the original text in place — citation refers to a non-PDF.
            out_parts.append(body_md[m.start():m.end()])
            continue
        # Build a button placeholder. Use data-* attrs so the frontend
        # binds without parsing the URL again. Escape user-controllable
        # bits with html.escape.
        attrs = (
            f'data-pdf-path="{html.escape(pdf_path, quote=True)}" '
            f'data-pdf-page="{int(page)}" '
            f'data-pdf-title="{html.escape(slug, quote=True)}"'
        )
        label_page = f"p.{page}"
        out_parts.append(
            f'<button class="eos-pdf-anchor" {attrs}>'
            f'\U0001F4C4 {html.escape(label_page)}</button>'
        )
    out_parts.append(body_md[cursor:])
    return "".join(out_parts)
