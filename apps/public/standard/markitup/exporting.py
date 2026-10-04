"""markitup — exports: markdown for editing, PDF for sending.

Extracted from app.py to keep the spine atomic (CLAUDE.md rule 4). Owns: the
two deliverable formats and the self-contained HTML the PDF is printed from.

**Printed, never annotated.** There is no PDF-mutation path in this repo —
``pypdf`` is read-only here and nothing writes annotation objects — so a marked
-up PDF is produced by printing HTML that already carries the pins, via
``sdk/pdf.py::render_html_pdf``. That keeps the export on the sanctioned
renderer and adds no dependency.

Two constraints that dictate the shape of the HTML, both easy to get wrong:

  * ``render_html_pdf`` calls ``set_content``, so the page cannot fetch
    anything. Every screenshot is inlined as a data URI or it prints blank.
  * Pins are positioned in **percent** against a ``position: relative`` wrapper,
    the same as on screen. A pixel offset measured in the browser would not
    survive being scaled onto a PDF page.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: rubrics (leaf), shared (leaf).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import asyncio
import html
import json
import math
import re
import time
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from .rubrics import get_rubric, tag_meta
from .shared import data_uri, ref_prefix, slugify, source_line

if TYPE_CHECKING:
    from .app import MarkitupApp  # noqa: F401 — for type hints only


# ─── Bind to MarkitupApp class as ────────────────────────────────────
#   export_markdown = _exporting.export_markdown
#   export_pdf      = _exporting.export_pdf
# Adding a new method here? Add a matching binding line in app.py.
#
# render_export_markdown / render_export_html are pure (doc in, text out) and
# are deliberately NOT bound: binding one would pass `self` as `doc`.
# ────────────────────────────────────────────────────────────────────

VAULT_DIR = "30_Resources/EmptyOS/markitup/outputs"

# Tag swatches for print. The daemon page uses theme tokens, which do not exist
# in a printed document, so the export carries its own literal palette — one of
# the few places a hardcoded colour is correct rather than a token violation.
PRINT_COLOURS = {
    "pass": "#15803d", "fail": "#b91c1c", "idea": "#a16207",
    "blocked": "#9a3412", "draft": "#475569", "active": "#1d4ed8",
    "running": "#0e7490", "shelved": "#6d28d9", "neutral": "#334155",
    "archived": "#57534e", "completed": "#166534", "published": "#065f46",
}


def _colour(rubric_id: str, tag: str) -> str:
    meta = tag_meta(rubric_id, tag)
    return PRINT_COLOURS.get((meta or {}).get("variant", "neutral"), "#334155")


# A4 minus 14mm margins, and how much of that page a band's image may occupy.
# The remainder carries that band's comments, so a note sits on the same sheet
# as the pin it describes.
# A4 (210mm) minus PAGE_MARGIN_MM on each side. The margin is passed explicitly
# to render_html_pdf below rather than left to its default, because its default
# is 13mm at the sides — and the `@page` rule in the stylesheet is inert without
# prefer_css_page_size, so CSS cannot settle it. Left implicit, PAGE_W_MM stated
# a derivation the print did not produce and every band was ~1% out.
PAGE_MARGIN_MM = 14.0
PAGE_W_MM = 210.0 - 2 * PAGE_MARGIN_MM      # 182.0
# How much of a page the image band may take; the rest carries that band's
# comments. A layout judgment, not a derived figure.
BAND_H_MM = 176.0
# A capture at or under this CSS width is labelled a phone view in the
# contents strip. The default capture set is 1440 and 390 (stages.py), and no
# tablet width sits between them; 480 keeps any plausible phone preset on the
# phone side without pulling a narrow desktop capture over.
PHONE_MAX_W = 480


def plan_bands(shot: dict, comments: list[dict], *,
               page_w_mm: float = PAGE_W_MM, band_h_mm: float = BAND_H_MM) -> list[dict]:
    """Slice one shot into page-height bands, keeping only the ones worth printing.

    A full-page capture is routinely taller than a sheet of paper — a 390x7955
    mobile shot rendered 182mm wide is 3.7 METRES, about fourteen A4 pages for
    one view, nearly all of them screenshot with nothing on them. Scaling the
    whole thing onto one page instead would make it unreadable, which defeats
    the pin.

    So: cut it into band-height windows, drop every band that contains no
    comment, and let each surviving band carry its own comments. That includes
    band 0: a first page reading "No comments on this part of the view" is a
    page the reader pays for and learns nothing from, so a view whose pins all
    sit lower down opens on its first commented band. Only a view with no
    comments at all keeps band 0, so the export still shows what it reviewed.
    An unpinned note counts as a comment and anchors band 0, so a view
    carrying one still opens on the top of the page.

    Pure, and deliberately geometric rather than pixel-based: the caller renders
    a band as a clipping window over a translated copy of the same image, so the
    pins keep the exact percent positions they already have and nothing has to
    rasterise anything.

    Geometry is returned twice over. ``*_mm`` is what the page layout is
    reasoned about in; ``top_pct`` and ``aspect`` are what the HTML actually
    uses, because a millimetre offset is only correct when the image renders at
    exactly ``page_w_mm`` wide. It does in the PDF and does not in any preview,
    which is how a first cut put three of four bands on the wrong slice.
    A percentage of the image's own height, and a window expressed as an aspect
    ratio, are right at any width.

    Returns ``[{index, top_mm, height_mm, rendered_h_mm, top_pct, aspect,
    comments}]``.
    """
    origin = shot.get("origin") or {}
    try:
        w = float(origin.get("w") or 0)
        h = float(origin.get("h") or 0)
    except (TypeError, ValueError):
        w = h = 0.0
    ordered = sorted(comments, key=lambda c: int(c.get("n", 0)))
    if w <= 0 or h <= 0:
        # No usable geometry — one band, everything on it, no clipping.
        return [{"index": 0, "top_mm": 0.0, "height_mm": 0.0,
                 "rendered_h_mm": 0.0, "top_pct": 0.0, "aspect": 0.0,
                 "comments": ordered}]

    rendered = page_w_mm * (h / w)
    n = max(1, math.ceil(rendered / band_h_mm))

    buckets: dict[int, list[dict]] = {}
    for c in ordered:
        y = c.get("y")
        if not isinstance(y, (int, float)) or isinstance(y, bool):
            buckets.setdefault(0, []).append(c)   # unpinned: it rides band 0
            continue
        idx = min(int((float(y) * rendered) // band_h_mm), n - 1)
        buckets.setdefault(max(0, idx), []).append(c)

    keep = sorted(buckets) or [0]
    out = []
    for i in keep:
        top = i * band_h_mm
        height = min(band_h_mm, max(0.0, rendered - top))
        out.append({
            "index": i,
            "top_mm": top,
            "height_mm": height,
            "rendered_h_mm": rendered,
            "top_pct": (top / rendered * 100.0) if rendered else 0.0,
            "aspect": (page_w_mm / height) if height else 0.0,
            "comments": buckets.get(i, []),
        })
    return out


def _by_shot(doc: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for c in doc.get("comments") or []:
        out.setdefault(c.get("shot_id") or "", []).append(c)
    for group in out.values():
        group.sort(key=lambda c: int(c.get("n", 0)))
    return out


def _legend(rubric_id: str) -> list[dict]:
    return get_rubric(rubric_id)["tags"]


def _shot_width(shot: dict) -> int:
    vp = shot.get("viewport") or {}
    try:
        return int(vp.get("w") or 0)
    except (TypeError, ValueError):
        return 0


def _view_label(shot: dict, widths: set[int]) -> str:
    """A view's printed name, HTML-escaped, with the capture width folded in
    whenever it is what tells two captures of one view apart.

    ``widths`` is every distinct capture width in the review. A phone-width
    capture reads ", phone"; any other width that is not the review's widest
    reads ", 1024px" — so a user preset of two desktop widths still yields two
    distinct labels. The same string feeds the section heading (which Chromium
    turns into the bookmark) and the contents strip, so a view and its twin
    can be told apart everywhere a reader looks; a bare title gave two
    identical bookmarks per view.
    """
    w = _shot_width(shot)
    title = html.escape(str(shot.get("title") or shot["id"]))
    if 0 < w <= PHONE_MAX_W:
        return f"{title}, phone"
    if w and len(widths) > 1 and w != max(widths):
        return f"{title}, {w}px"
    return title


# ── markdown ─────────────────────────────────────────────────────────

def _fm_str(value) -> str:
    """One frontmatter string value. Whitespace runs (newlines, tabs) collapse
    to a space first — a page <title> can carry them, and EmptyOS's own parser
    does not unescape \\n — then json.dumps, whose output is a valid YAML
    double-quoted scalar, so a colon or a quote cannot break the block."""
    return json.dumps(re.sub(r"\s+", " ", str(value or "")).strip(), ensure_ascii=False)


def render_export_frontmatter(doc: dict, updated: str) -> str:
    """Frontmatter for the vault copy, so a vault query can find reviews and
    tell how far their triage got. Pure. Tags are block-style (CLAUDE.md).

    ``lifecycle: living`` — each export overwrites the file for that review,
    so it is the review's current state, not a snapshot of a past one.
    ``author`` is exact (.claude/rules/authorship-boundary.md): ``ai`` only
    when every comment is still the model's, ``both`` once a person wrote or
    edited one."""
    comments = doc.get("comments") or []
    counts = {"open": 0, "accepted": 0, "dismissed": 0}
    for c in comments:
        s = str(c.get("status") or "open")
        counts[s] = counts.get(s, 0) + 1
    human = any(str(c.get("author") or "ai") in ("human", "both") for c in comments)
    lines = [
        "---",
        "tags:",
        "  - markitup-review",
        f"title: {_fm_str(doc.get('title') or 'Visual review')}",
        f"source: {_fm_str(doc.get('source'))}",
        f"rubric: {_fm_str(doc.get('rubric') or 'site')}",
        f"review_id: {_fm_str(doc.get('id'))}",
        f"author: {'both' if human else 'ai'}",
        f"comments: {len(comments)}",
        f"comments_open: {counts['open']}",
        f"comments_accepted: {counts['accepted']}",
        f"comments_dismissed: {counts['dismissed']}",
        "lifecycle: living",
        f"updated: {updated}",
        "---",
        "",
    ]
    return "\n".join(lines)


def render_export_markdown(doc: dict) -> str:
    """The review as markdown — the editable half of the deliverable."""
    rubric_id = doc.get("rubric", "site")
    lines = [
        f"# {doc.get('title') or 'Visual review'}",
        "",
        f"Source: {doc.get('source', '')}".rstrip(),
        f"Captured: {doc.get('created', '')}".rstrip(),
        "",
    ]
    # The review-level argument prints before any view, so a reader gets the
    # themes before the dispositions. Already markdown — pasted through as-is.
    summary = (doc.get("summary") or "").strip()
    if summary:
        lines.extend(["## General comments", "", summary, ""])
    lines += [
        "> These are discussion prompts, not instructions. Each is tagged with "
        "what kind of observation it is.",
        "",
        "| Tag | Means |",
        "|---|---|",
    ]
    for t in _legend(rubric_id):
        lines.append(f"| **{t['label']}** | {t['desc']} |")
    lines.append("")

    grouped = _by_shot(doc)
    for shot in doc.get("shots") or []:
        comments = grouped.get(shot["id"], [])
        lines.append(f"## {shot.get('title') or shot['id']}")
        lines.append("")
        # By kind: a web shot prints its route; a document shot prints its
        # source ref and content hash, not the loopback render URL.
        where = source_line(shot)
        if where:
            lines.append(where)
        vp = shot.get("viewport") or {}
        if vp.get("w"):
            lines.append(f"Viewport: {vp.get('w')}x{vp.get('h')}")
        if shot.get("focus"):
            lines.append(f"Focus: {shot['focus']}")
        lines.append("")
        if not comments:
            lines.extend(["_No comments on this view._", ""])
            continue
        for c in comments:
            meta = tag_meta(rubric_id, c.get("tag", "")) or {}
            label = meta.get("label", c.get("tag", "?"))
            state = "" if c.get("status") == "open" else f" _({c.get('status')})_"
            # The requirement id the pinned row opens with, derived from the
            # measured anchor text — so a finding is addressable by id even
            # when the model's title did not repeat it.
            rp = ref_prefix(c)
            ref = f"`{rp}` — " if rp else ""
            lines.append(f"### {c.get('n')}. [{label}] {ref}{c.get('title', '')}{state}")
            lines.append("")
            if c.get("body"):
                lines.extend([c["body"], ""])
    return "\n".join(lines).rstrip() + "\n"


# ── html / pdf ───────────────────────────────────────────────────────

def _summary_html(text: str) -> str:
    """The summary as HTML, through the shared vault-markdown engine.

    Same trust posture as ``render_markdown_pdf``: this is the reviewer's own
    markdown, so raw HTML passes through exactly as it does for every vault
    note printed to PDF. That includes scripts — ``render_html_pdf`` prints via
    ``set_content`` and runs them before the snapshot — in an about:blank
    origin with no cookies, on a single-user daemon where the author is the
    reader. Accepted, not accidental. (A bare ``![](x.png)`` gets the engine's
    ``assets/`` prefix and cannot resolve here: a broken image, nothing worse.)

    Without the markdown package it prints as ESCAPED preformatted text — the
    engine's own fallback wraps the raw string in ``<pre>`` unescaped, which is
    fine for a vault note and not for text that arrived over HTTP.
    """
    from emptyos.sdk.markdown_render import HAS_MARKDOWN, render_markdown

    if not HAS_MARKDOWN:
        return f"<pre>{html.escape(text)}</pre>"
    body, _toc = render_markdown(text, published_slugs=None)
    # Web-only heading permalinks mean nothing on paper (same strip as pdf.py).
    return re.sub(r'<a[^>]*class="header-link"[^>]*>.*?</a>', "", body)


def render_export_html(doc: dict, images: dict) -> str:
    """Self-contained HTML for printing. ``images`` maps shot id → data URI.

    A shot with no image still renders its comments: losing a screenshot should
    cost the picture, not the review.
    """
    rubric_id = doc.get("rubric", "site")
    esc = html.escape
    grouped = _by_shot(doc)

    legend = "".join(
        f'<span class="lg"><i style="background:{_colour(rubric_id, t["id"])}"></i>'
        f'{esc(t["label"])}</span>'
        for t in _legend(rubric_id)
    )

    # The summary prints FIRST — above the tag legend, exactly where the
    # markdown export puts it — and as the first section it shares the title
    # page (the first section never forces a break), so every view starts on a
    # page of its own after it and the reader meets the argument before the
    # first pin.
    summary = (doc.get("summary") or "").strip()
    summary_block = (f'<section class="summary"><h2>General comments</h2>'
                     f'{_summary_html(summary)}</section>') if summary else ""

    # A one-line contents strip: every view with its comment count, in print
    # order, so page one shows the shape of the document. No page numbers —
    # Chromium cannot compute them into the body, and a stale number is worse
    # than none; the bookmark sidebar and the footer carry that.
    widths = {_shot_width(s) for s in doc.get("shots") or []} - {0}
    entries = [f'{_view_label(shot, widths)} ({len(grouped.get(shot["id"], []))})'
               for shot in doc.get("shots") or []]
    contents = (f'<p class="contents"><b>Views</b> {" · ".join(entries)}</p>'
                if entries else "")

    blocks = []
    for shot in doc.get("shots") or []:
        comments = grouped.get(shot["id"], [])
        src = images.get(shot["id"], "")
        vp = shot.get("viewport") or {}
        sub = " · ".join(x for x in [
            esc(source_line(shot)),
            f'{vp.get("w")}x{vp.get("h")}' if vp.get("w") else "",
            esc(str(shot.get("focus", ""))),
        ] if x)

        bands = plan_bands(shot, comments)
        for pos, band in enumerate(bands, 1):
            rows = "".join(
                f'<li><b style="background:{_colour(rubric_id, c.get("tag", ""))}">'
                f'{int(c.get("n", 0))}</b>'
                f'<div><strong>{(esc(rp) + " — ") if rp else ""}'
                f'{esc(str(c.get("title", "")))}</strong>'
                f'<em>{esc((tag_meta(rubric_id, c.get("tag", "")) or {}).get("label", ""))}'
                f'{"" if c.get("status") == "open" else " · " + esc(str(c.get("status")))}'
                f'{"" if isinstance(c.get("x"), (int, float)) and not isinstance(c.get("x"), bool) else " · no pin"}'
                f'</em><p>{esc(str(c.get("body", "")))}</p></div></li>'
                for c, rp in ((c, ref_prefix(c)) for c in band["comments"])
            ) or "<li class='none'>No comments on this part of the view.</li>"

            if src:
                pins = "".join(
                    f'<b class="pin" style="left:{c["x"] * 100:.3f}%;top:{c["y"] * 100:.3f}%;'
                    f'background:{_colour(rubric_id, c.get("tag", ""))}">{int(c.get("n", 0))}</b>'
                    for c in comments
                    if isinstance(c.get("x"), (int, float)) and not isinstance(c.get("x"), bool)
                    and isinstance(c.get("y"), (int, float)) and not isinstance(c.get("y"), bool)
                )
                # A band is a clipping window over the WHOLE image shifted up by
                # the band's offset. Pins keep their percent positions relative
                # to that image and travel with it, so band geometry needs no
                # second coordinate system — and nothing rasterises anything.
                # Scale-free: the window is an aspect ratio and the shift is a
                # percentage of the image's own height, so a band shows the same
                # slice whether it is printed at 182mm or previewed at 746px.
                style = (f'aspect-ratio:{band["aspect"]:.5f}'
                         if band["aspect"] else "")
                shift = (f'transform:translateY(-{band["top_pct"]:.5f}%)'
                         if band["top_pct"] else "")
                figure = (f'<div class="win" style="{style}">'
                          f'<div class="shot" style="{shift}">'
                          f'<img src="{src}" alt="">{pins}</div></div>')
            else:
                figure = '<p class="missing">Screenshot unavailable.</p>'

            # By printed position, not band index: when band 0 was dropped the
            # first page a reader sees must not announce itself as a continuation.
            # The heading IS the bookmark (Chromium builds the outline from
            # h1..h6), so it carries only the view's name: the first printed
            # band is an h2, later bands are h3 so they nest under it in the
            # sidebar, and the "part n of m" counter sits in the sub-line,
            # where it does not get glued onto the bookmark text.
            tag = "h2" if pos == 1 else "h3"
            cont = " (continued)" if pos > 1 else ""
            more = (f' <span class="of">part {pos} of {len(bands)}</span>'
                    if len(bands) > 1 else "")
            blocks.append(
                f'<section><{tag}>{_view_label(shot, widths)}{cont}</{tag}>'
                f'<p class="sub">{sub}{more}</p>{figure}<ol class="notes">{rows}</ol></section>'
            )

    margin_mm = PAGE_MARGIN_MM
    return f"""<!doctype html><meta charset="utf-8"><title>{esc(str(doc.get('title', 'Review')))}</title>
<style>
 @page {{ size: A4; margin: {margin_mm:g}mm; }}
 body {{ font: 11pt/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; color: #1a1a1a; }}
 h1 {{ font-size: 22pt; margin: 0 0 4pt; }}
 h2 {{ font-size: 14pt; margin: 0 0 2pt; }}
 section:not(.summary) > h3 {{ font-size: 12pt; margin: 0 0 2pt; color: #555; }}
 .meta {{ color: #555; margin: 0 0 10pt; }}
 .legend {{ margin: 0 0 14pt; }}
 .lg {{ display: inline-block; margin: 0 10pt 4pt 0; font-size: 9pt; }}
 .lg i {{ display: inline-block; width: 9pt; height: 9pt; border-radius: 50%;
          margin-right: 4pt; vertical-align: -1pt; }}
 section {{ break-before: page; }}
 section:first-of-type {{ break-before: auto; }}
 .contents {{ margin: 0 0 14pt; color: #555; font-size: 10pt; }}
 .contents b {{ color: #1a1a1a; margin-right: 4pt; }}
 .summary {{ margin: 0 0 14pt; }}
 .summary p, .summary li {{ margin: 0 0 6pt; }}
 .summary h3 {{ font-size: 12pt; margin: 10pt 0 2pt; }}
 .sub {{ color: #666; font-size: 9pt; margin: 0 0 8pt; overflow: hidden; }}
 .win {{ position: relative; overflow: hidden; border: 1px solid #ddd;
         width: 100%; }}
 .shot {{ position: relative; width: 100%; }}
 .shot img {{ display: block; width: 100%; }}
 .of {{ float: right; font-size: 9pt; font-weight: 400; color: #777; }}
 .pin {{ position: absolute; transform: translate(-50%, -50%); min-width: 15pt;
         height: 15pt; padding: 0 2pt; border-radius: 9pt; color: #fff;
         font: 700 8pt/15pt system-ui, sans-serif; text-align: center;
         border: 1.2pt solid #fff; box-sizing: border-box; }}
 .notes {{ list-style: none; padding: 0; margin: 10pt 0 0; }}
 .notes li {{ display: flex; gap: 7pt; break-inside: avoid; margin-bottom: 7pt; }}
 .notes li b {{ flex: 0 0 auto; width: 15pt; height: 15pt; border-radius: 9pt;
                color: #fff; font: 700 8pt/15pt system-ui, sans-serif;
                text-align: center; }}
 .notes em {{ display: block; font-style: normal; color: #777; font-size: 8.5pt;
              text-transform: uppercase; letter-spacing: .04em; }}
 .notes p {{ margin: 3pt 0 0; }}
 .none, .missing {{ color: #888; font-style: italic; }}
</style>
<h1>{esc(str(doc.get('title', 'Visual review')))}</h1>
<p class="meta">{esc(str(doc.get('source', '')))}
 {('&middot; ' + esc(str(doc.get('created')))) if doc.get('created') else ''}</p>
{summary_block}
<div class="legend">{legend}</div>
{contents}
{''.join(blocks)}
"""


# ── bound methods ────────────────────────────────────────────────────

def _out_path(self, rid: str, ext: str) -> Path:
    """Where a deliverable lands: the vault when one is mounted, else beside the
    review. ``outputs/`` per .claude/rules/authorship-boundary.md — the app
    writes the file; its frontmatter ``author`` says whether a person shaped
    the comments in it (``both``) or not (``ai``)."""
    doc_name = f"{slugify(rid, fallback='review')}.{ext}"
    root = getattr(self, "vault_root", None)
    if root:
        p = Path(root) / VAULT_DIR / doc_name
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    return (self.review_dir(rid) or Path(".")) / doc_name


def _vault_rel(self, out: Path) -> str:
    """`out` relative to the vault, or "" when it was written beside the review
    (no vault mounted) — the page links a vault path and prints anything else.
    BaseApp.vault_rel does the formatting; it cannot be asked with no vault."""
    if not getattr(self, "vault_root", None):
        return ""
    return self.vault_rel(out)


async def export_markdown(self, rid: str) -> dict:
    doc = self.read_review(rid)
    if not doc:
        return {"error": f"no review with id {rid!r}"}
    out = _out_path(self, rid, "md")
    # Through the capability, not Path.write_text — CLAUDE.md rule 1. The PDF
    # below cannot follow suit: render_html_pdf owns its own Playwright file
    # sink, the same exemption other report-producing apps rely on.
    updated = date.today().isoformat()
    await self.write(str(out), render_export_frontmatter(doc, updated) + render_export_markdown(doc))
    await self.emit("markitup:exported", {"id": rid, "format": "markdown", "path": str(out)})
    return {"ok": True, "path": str(out), "format": "markdown", "vault_path": _vault_rel(self, out)}


async def export_pdf(self, rid: str) -> dict:
    """Print the review to PDF, screenshots inlined."""
    from emptyos.sdk.pdf import render_html_pdf

    doc = self.read_review(rid)
    if not doc:
        return {"error": f"no review with id {rid!r}"}
    d = self.review_dir(rid)
    images = {}
    for shot in doc.get("shots") or []:
        png = (d / "shots" / shot.get("image", "")) if d else None
        if png and png.exists():
            images[shot["id"]] = data_uri(png.read_bytes())

    html_doc = render_export_html(doc, images)
    out = _out_path(self, rid, "pdf")
    margin = {side: f"{PAGE_MARGIN_MM:g}mm"
              for side in ("top", "bottom", "left", "right")}
    try:
        # Sync + Playwright sync API — must not run on the event loop.
        # Bookmarks from the headings (one per view with its continuation
        # pages nested, plus the general comments and any headings inside
        # them) and "n / total" page numbers: a review is read while
        # switching between it and the site, so the reader needs to jump, and
        # a comment needs to be nameable by page in a message.
        await asyncio.to_thread(render_html_pdf, html_doc, out, margin=margin,
                                outline=True, page_numbers=True)
    except Exception as e:  # noqa: BLE001 — an export failure is an answer,
        # not a 500. RuntimeError alone let every other Playwright fault through.
        return {"error": f"PDF render failed: {e or type(e).__name__}"}
    await self.emit("markitup:exported", {"id": rid, "format": "pdf", "path": str(out)})
    return {
        "ok": True, "path": str(out), "format": "pdf", "vault_path": _vault_rel(self, out),
        "shots": len(doc.get("shots") or []),
        "images": len(images),
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
