#!/usr/bin/env python3
"""Render an EmptyOS insights markdown report into a user-friendly HTML page.

Consumed by the /eos-insights and /eos-life-insights skills. The markdown
report (author: ai, in 30_Resources/EmptyOS/insights/outputs/) stays the
vault-native source of truth; this produces a styled .html sibling for
viewing in a browser — the EmptyOS analogue of Claude Code's /insights HTML.

Pure stdlib (no kernel boot, no third-party markdown dep). Section cards are
themed by keyword in the `##` heading: friction → red, dark-flag/dormant →
amber, proposal/next-step/suggested → blue, headline → amber hero, kb/velocity
/growing → green, else neutral. Supports the markdown subset the skills emit:
tables (2-col `label | value` tables auto-render a bar chart), lists, bold,
italic, inline code, links, fenced code blocks (copyable artifacts), `###`/`####`
sub-headings, and a `## Stats` section that becomes the stat-tile row.

Usage:
    python scripts/render_insights_html.py <path-to-report.md> [--open]

Writes <path-to-report>.html next to the input and prints the output path.
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

# ── inline markdown ─────────────────────────────────────────────────────────

_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\s)([^*\n]+?)\*(?!\*)")
_CODE_RE = re.compile(r"`([^`]+)`")


def _plain(text: str) -> str:
    """Strip markdown emphasis/links to a clipboard-friendly plain string."""
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    t = re.sub(r"\*([^*]+)\*", r"\1", t)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    t = _LINK_RE.sub(r"\1", t)
    return t.strip()


def _first_int(cell: str) -> int | None:
    """First integer in a cell (commas stripped), or None if there's no number."""
    m = re.search(r"-?\d[\d,]*", cell.replace(" ", ""))
    if not m:
        return None
    try:
        return int(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _safe_href(raw_escaped_url: str) -> str:
    """Validate a markdown link target before it lands in an href attribute.

    The URL arrives html.escape()'d (from `_inline`). Reject anything that isn't a
    benign scheme (blocks javascript:/data: XSS), then re-escape for the attribute
    context so a `"` in the URL can't break out. Unsafe → '#'.
    """
    url = html.unescape(raw_escaped_url).strip()
    if not url.lower().startswith(("http://", "https://", "mailto:", "#", "/", "./", "../")):
        return "#"
    return html.escape(url, quote=True)


def _inline(text: str) -> str:
    """Escape, then apply code spans, bold, and links (in a safe order)."""
    out = html.escape(text)
    # code spans first — protect their contents from further markup
    codes: list[str] = []

    def _stash(m: re.Match) -> str:
        codes.append(m.group(1))
        return f"\x00CODE{len(codes) - 1}\x00"

    out = _CODE_RE.sub(_stash, out)
    out = _BOLD_RE.sub(r"<strong>\1</strong>", out)
    out = _ITALIC_RE.sub(r"<em>\1</em>", out)
    # links: [text](url) — validate + re-escape the url for the href attribute
    out = _LINK_RE.sub(
        lambda m: f'<a href="{_safe_href(m.group(2))}" target="_blank" rel="noopener">{m.group(1)}</a>',
        out,
    )
    for i, c in enumerate(codes):
        out = out.replace(f"\x00CODE{i}\x00", f"<code>{c}</code>")
    return out


# ── block markdown ──────────────────────────────────────────────────────────


_BAR_COLORS = ["#2563eb", "#0891b2", "#7c3aed", "#16a34a", "#d97706", "#dc2626"]


def _maybe_bars(body: list[list[str]]) -> str:
    """If a table has a label column (col 0, non-numeric) and a numeric column,
    render a bar chart from (label, first-numeric-value) above the table."""
    if len(body) < 2 or not body[0]:
        return ""
    # Chart ONLY a canonical 2-column `label | value` table. A 3-col comparison
    # table (e.g. Metric | Prior | This) is heterogeneous/multi-series and a
    # single bar series would misrepresent it — leave those as plain tables.
    if len(body[0]) != 2:
        return ""
    # col 0 must be a label (reject only if it's a PURE numeric series, so
    # labels like "W23"/"Q1"/"2026-06" that contain digits still count as labels)
    if all(re.fullmatch(r"-?[\d,]+%?", r[0].strip()) for r in body if r and r[0].strip()):
        return ""
    if not all(_first_int(r[1]) is not None for r in body if len(r) > 1):
        return ""
    val_col = 1
    pairs = [(r[0], _first_int(r[val_col])) for r in body if len(r) > val_col]
    mx = max((v for _, v in pairs if v is not None), default=0) or 1
    rows = []
    for i, (label, v) in enumerate(pairs):
        pct = max(2, round(100 * (v or 0) / mx))
        color = _BAR_COLORS[i % len(_BAR_COLORS)]
        rows.append(
            f'<div class="bar-row"><div class="bar-label" title="{html.escape(_plain(label))}">{_inline(label)}</div>'
            f'<div class="bar-track"><div class="bar-fill" style="width:{pct}%;background:{color}"></div></div>'
            f'<div class="bar-value">{v}</div></div>'
        )
    return '<div class="bars">' + "".join(rows) + "</div>"


def _render_table(rows: list[str]) -> str:
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    if len(cells) >= 2 and all(set(c) <= set("-: ") for c in cells[1]):
        header, body = cells[0], cells[2:]
    else:
        header, body = None, cells
    bars = _maybe_bars(body)
    out = [bars, '<details class="table-wrap"><summary>data table</summary>'] if bars else []
    out.append('<table class="md-table">')
    if header:
        out.append("<thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in header) + "</tr></thead>")
    out.append("<tbody>")
    for row in body:
        out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>")
    out.append("</tbody></table>")
    if bars:
        out.append("</details>")
    return "".join(out)


def _render_blocks(lines: list[str], *, copyable: bool = False) -> str:
    """Render a list of markdown body lines (no headings) to HTML.

    copyable=True adds a per-item copy-to-clipboard button on list items (used
    for proposal cards — the yahav10 'copy the rule/skill' affordance)."""
    html_parts: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        # fenced code block ``` … ``` → copyable artifact
        if stripped.startswith("```"):
            i += 1
            code: list[str] = []
            while i < n and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1  # closing fence
            raw = "\n".join(code)
            payload = html.escape(raw, quote=True)
            html_parts.append(
                '<div class="codeblock">'
                f'<button class="copy-btn code-copy" data-copy="{payload}" onclick="cpBtn(this)" title="Copy">⧉ copy</button>'
                f"<pre><code>{html.escape(raw)}</code></pre></div>"
            )
            continue
        # sub-headings (chart titles etc.)
        if stripped.startswith("#### "):
            html_parts.append(f'<h4 class="sub">{_inline(stripped[5:])}</h4>')
            i += 1
            continue
        if stripped.startswith("### "):
            html_parts.append(f'<h3 class="sub">{_inline(stripped[4:])}</h3>')
            i += 1
            continue
        # table
        if stripped.startswith("|") and "|" in stripped[1:]:
            tbl: list[str] = []
            while i < n and lines[i].strip().startswith("|"):
                tbl.append(lines[i])
                i += 1
            html_parts.append(_render_table(tbl))
            continue
        # ordered or unordered list
        if re.match(r"^[-*]\s+", stripped) or re.match(r"^\d+\.\s+", stripped):
            ordered = bool(re.match(r"^\d+\.\s+", stripped))
            tag = "ol" if ordered else "ul"
            items: list[str] = []
            while i < n and (re.match(r"^[-*]\s+", lines[i].strip()) or re.match(r"^\d+\.\s+", lines[i].strip())):
                item = re.sub(r"^([-*]|\d+\.)\s+", "", lines[i].strip())
                if copyable:
                    payload = html.escape(_plain(item), quote=True)
                    items.append(
                        f'<li class="copy-li"><span class="li-body">{_inline(item)}</span>'
                        f'<button class="copy-btn" data-copy="{payload}" onclick="cpBtn(this)" title="Copy">⧉</button></li>'
                    )
                else:
                    items.append(f"<li>{_inline(item)}</li>")
                i += 1
            html_parts.append(f'<{tag} class="md-list">' + "".join(items) + f"</{tag}>")
            continue
        # horizontal rule
        if stripped == "---":
            html_parts.append("<hr>")
            i += 1
            continue
        # blockquote
        if stripped.startswith(">"):
            quote: list[str] = []
            while i < n and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip().lstrip(">").strip())
                i += 1
            html_parts.append(f'<blockquote>{_inline(" ".join(quote))}</blockquote>')
            continue
        # paragraph (gather until blank / block start)
        para: list[str] = []
        while i < n and lines[i].strip() and not lines[i].strip().startswith(("|", "#", ">", "---")) \
                and not re.match(r"^[-*]\s+", lines[i].strip()) and not re.match(r"^\d+\.\s+", lines[i].strip()):
            para.append(lines[i].strip())
            i += 1
        html_parts.append(f"<p>{_inline(' '.join(para))}</p>")
    return "\n".join(html_parts)


# ── section theming ─────────────────────────────────────────────────────────

_THEMES = [
    (("friction", "error", "bug", "broken"), "bad"),
    (("dark-flag", "dark flag", "dormant", "stale", "backlog"), "warn"),
    (("proposal", "next step", "suggested", "recommend", "action"), "info"),
    (("kb", "velocity", "growing", "win", "shipped", "growth", "health"), "good"),
    (("horizon", "ambitious", "future"), "horizon"),
]


def _theme_for(title: str) -> str:
    t = title.lower()
    for keys, theme in _THEMES:
        if any(k in t for k in keys):
            return theme
    return "neutral"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "section"


# ── frontmatter + sections ──────────────────────────────────────────────────


def _split_frontmatter(text: str) -> tuple[dict, str]:
    fm: dict[str, str] = {}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            block = text[3:end].strip()
            body = text[end + 4 :].lstrip("\n")
            for ln in block.splitlines():
                if ":" in ln and not ln.strip().startswith("-"):
                    k, _, v = ln.partition(":")
                    fm[k.strip()] = v.strip()
            return fm, body
    return fm, text


def _parse(body: str) -> tuple[str, str, list[tuple[str, list[str]]]]:
    """Return (h1_title, lead_text, [(section_title, section_lines), ...])."""
    lines = body.splitlines()
    h1 = ""
    lead: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    cur_title: str | None = None
    cur_lines: list[str] = []
    for ln in lines:
        if ln.startswith("# ") and not h1:
            h1 = ln[2:].strip()
            continue
        if ln.startswith("## "):
            if cur_title is not None:
                sections.append((cur_title, cur_lines))
            cur_title = ln[3:].strip()
            cur_lines = []
            continue
        if cur_title is None:
            lead.append(ln)
        else:
            cur_lines.append(ln)
    if cur_title is not None:
        sections.append((cur_title, cur_lines))
    return h1, "\n".join(lead).strip(), sections


def _parse_stat_pairs(lines: list[str]) -> list[tuple[str, str]]:
    """Pull (label, value) tiles from a Stats section — a 2-col `Metric | Value`
    table (preferred) or `Label: value` lines. Value is shown big, label small."""
    pairs: list[tuple[str, str]] = []
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if len(cells) >= 2 and not all(set(c) <= set("-: ") for c in cells):  # skip sep row
                if cells[0].lower() not in ("metric", "stat", "label"):  # skip header
                    pairs.append((cells[0], cells[1]))
        elif ":" in s:
            k, _, v = s.lstrip("-* ").partition(":")
            if k.strip() and v.strip():
                pairs.append((k.strip(), v.strip()))
    return pairs


# ── HTML template ───────────────────────────────────────────────────────────

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; background: #f8fafc; color: #334155; line-height: 1.65; padding: 48px 24px; }
.container { max-width: 820px; margin: 0 auto; }
h1 { font-size: 32px; font-weight: 700; color: #0f172a; margin-bottom: 6px; }
.subtitle { color: #64748b; font-size: 15px; margin-bottom: 28px; }
h2 { font-size: 20px; font-weight: 600; color: #0f172a; margin: 12px 0 14px 0; }
.lens-chip { display: inline-block; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .04em; padding: 3px 10px; border-radius: 999px; background: #ede9fe; color: #5b21b6; vertical-align: middle; margin-left: 10px; }
.hero { background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%); border: 1px solid #f59e0b; border-radius: 12px; padding: 20px 24px; margin-bottom: 28px; }
.hero-title { font-size: 13px; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; color: #92400e; margin-bottom: 12px; }
.hero p { font-size: 15px; color: #78350f; line-height: 1.6; margin-bottom: 8px; }
.hero strong { color: #92400e; }
.hero p:last-child { margin-bottom: 0; }
.nav-toc { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 28px 0; padding: 14px; background: #fff; border-radius: 8px; border: 1px solid #e2e8f0; }
.nav-toc a { font-size: 12px; color: #64748b; text-decoration: none; padding: 6px 12px; border-radius: 6px; background: #f1f5f9; transition: all .15s; }
.nav-toc a:hover { background: #e2e8f0; color: #334155; }
.stats-row { display: flex; gap: 28px; margin-bottom: 36px; padding: 18px 0; border-top: 1px solid #e2e8f0; border-bottom: 1px solid #e2e8f0; flex-wrap: wrap; }
.stat { text-align: center; }
.stat-value { font-size: 22px; font-weight: 700; color: #0f172a; }
.stat-label { font-size: 11px; color: #64748b; text-transform: uppercase; letter-spacing: .03em; }
.card { border-radius: 10px; padding: 18px 20px; margin-bottom: 14px; border: 1px solid #e2e8f0; background: #fff; }
.card.neutral { background:#fff; border-color:#e2e8f0; }
.card.good { background:#f0fdf4; border-color:#bbf7d0; }
.card.bad  { background:#fef2f2; border-color:#fca5a5; }
.card.warn { background:#fffbeb; border-color:#fcd34d; }
.card.info { background:#eff6ff; border-color:#bfdbfe; }
.card.horizon { background: linear-gradient(135deg,#faf5ff 0%,#f5f3ff 100%); border-color:#c4b5fd; }
.card h2 { margin-top: 0; }
.card p { font-size: 14px; color: #475569; margin: 0 0 10px 0; line-height: 1.65; }
.card p:last-child { margin-bottom: 0; }
.card.good h2 { color:#166534; } .card.bad h2 { color:#991b1b; } .card.warn h2 { color:#92400e; }
.card.info h2 { color:#1e40af; } .card.horizon h2 { color:#5b21b6; }
.md-list { margin: 4px 0 8px 22px; font-size: 14px; color: #334155; }
.md-list li { margin-bottom: 6px; line-height: 1.55; }
.md-table { width: 100%; border-collapse: collapse; margin: 6px 0 10px 0; font-size: 13px; }
.md-table th { text-align: left; background: #f1f5f9; color: #334155; font-weight: 600; padding: 8px 10px; border-bottom: 2px solid #e2e8f0; }
.md-table td { padding: 7px 10px; border-bottom: 1px solid #eef2f6; color: #475569; vertical-align: top; }
.md-table tr:last-child td { border-bottom: none; }
blockquote { border-left: 3px solid #cbd5e1; padding: 4px 14px; margin: 8px 0; color: #64748b; font-size: 14px; font-style: italic; }
hr { border: none; border-top: 1px solid #e2e8f0; margin: 14px 0; }
code { background: #f1f5f9; padding: 1px 6px; border-radius: 4px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; color: #334155; }
a { color: #2563eb; }
.footer { margin-top: 40px; padding-top: 16px; border-top: 1px solid #e2e8f0; font-size: 12px; color: #94a3b8; text-align: center; }
details.card > summary { cursor: pointer; list-style: none; display: flex; align-items: center; gap: 8px; }
details.card > summary::-webkit-details-marker { display: none; }
details.card > summary::before { content: "▸"; color: #94a3b8; font-size: 13px; transition: transform .15s; }
details.card[open] > summary::before { transform: rotate(90deg); }
details.card > summary h2 { display: inline; margin: 0; }
details.table-wrap { margin: 4px 0 10px 0; }
details.table-wrap > summary { cursor: pointer; font-size: 11px; color: #94a3b8; text-transform: uppercase; letter-spacing: .04em; margin-bottom: 4px; }
.li-body { flex: 1; }
.md-list li.copy-li { display: flex; align-items: baseline; gap: 8px; }
.copy-btn { flex-shrink: 0; background: #e2e8f0; border: none; border-radius: 4px; padding: 2px 7px; font-size: 12px; cursor: pointer; color: #475569; transition: all .15s; }
.copy-btn:hover { background: #cbd5e1; }
.copy-btn.copied { background: #16a34a; color: #fff; }
.bars { display: flex; flex-direction: column; gap: 5px; margin: 6px 0 8px 0; }
.bar-row { display: flex; align-items: center; }
.bar-label { width: 160px; font-size: 12px; color: #475569; flex-shrink: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bar-track { flex: 1; height: 8px; background: #f1f5f9; border-radius: 4px; margin: 0 10px; }
.bar-fill { height: 100%; border-radius: 4px; }
.bar-value { width: 48px; font-size: 12px; font-weight: 600; color: #334155; text-align: right; }
h3.sub { font-size: 13px; font-weight: 600; color: #475569; text-transform: uppercase; letter-spacing: .03em; margin: 16px 0 8px 0; }
h4.sub { font-size: 13px; font-weight: 600; color: #64748b; margin: 12px 0 6px 0; }
.codeblock { position: relative; margin: 10px 0; }
.codeblock pre { background: #0f172a; color: #e2e8f0; border-radius: 8px; padding: 14px 16px; overflow-x: auto; font-size: 12px; line-height: 1.5; }
.codeblock pre code { background: none; color: inherit; padding: 0; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.code-copy { position: absolute; top: 8px; right: 8px; }
@media (max-width: 640px) { .stats-row { justify-content: center; gap: 18px; } body { padding: 28px 14px; } .bar-label { width: 110px; } }
"""

_JS = """
function cpBtn(el){
  var t = el.getAttribute('data-copy') || '';
  navigator.clipboard.writeText(t).then(function(){
    var old = el.textContent; el.textContent = '✓'; el.classList.add('copied');
    setTimeout(function(){ el.textContent = old; el.classList.remove('copied'); }, 1200);
  });
}
"""

_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>{css}</style>
</head>
<body>
<div class="container">
<h1>{h1}{lens_chip}</h1>
<p class="subtitle">{subtitle}</p>
{hero}
{toc}
{stats}
{sections}
<div class="footer">EmptyOS Insights · generated from {src} · author: ai</div>
</div>
<script>{js}</script>
</body>
</html>
"""


def render(md_path: Path) -> Path:
    text = md_path.read_text(encoding="utf-8")
    fm, body = _split_frontmatter(text)
    h1, lead, sections = _parse(body)
    h1 = h1 or md_path.stem
    lens = (fm.get("lens") or "").strip()
    period = (fm.get("period") or "").strip()
    as_of = (fm.get("as_of") or "").strip()

    lens_label = {"life": "Life", "eos": "System"}.get(lens, lens.title())
    lens_chip = f'<span class="lens-chip">{html.escape(lens_label)} lens</span>' if lens else ""
    subtitle_bits = [b for b in [f"Window: {period}" if period else "", f"As of {as_of}" if as_of else ""] if b]
    subtitle = " · ".join(subtitle_bits) or "EmptyOS insights report"

    # Hero from a leading "Headline" section if present, else from the lead text.
    hero_html = ""
    body_sections = sections
    if sections and sections[0][0].strip().lower() in ("headline", "at a glance", "summary"):
        htitle, hlines = sections[0]
        hero_html = f'<div class="hero"><div class="hero-title">{html.escape(htitle)}</div>{_render_blocks(hlines)}</div>'
        body_sections = sections[1:]
    elif lead:
        hero_html = f'<div class="hero"><div class="hero-title">At a Glance</div>{_render_blocks(lead.splitlines())}</div>'

    # Stat tiles: prefer a "## Stats" section (real computed metrics) over
    # frontmatter metadata. The skill emits a 2-col `Metric | Value` table.
    stat_pairs: list[tuple[str, str]] = []
    rest_sections = []
    for title, slines in body_sections:
        if title.strip().lower() in ("stats", "numbers", "key numbers", "by the numbers"):
            stat_pairs = _parse_stat_pairs(slines)
        else:
            rest_sections.append((title, slines))
    body_sections = rest_sections
    if not stat_pairs:  # fallback (metadata) when no Stats section authored
        stat_pairs = [(l, v) for l, v in (("Lens", lens_label), ("Window", period), ("As of", as_of)) if v]
    stat_tiles = [
        f'<div class="stat"><div class="stat-value">{_inline(val)}</div><div class="stat-label">{html.escape(label)}</div></div>'
        for label, val in stat_pairs
    ]
    stats_html = f'<div class="stats-row">{"".join(stat_tiles)}</div>' if stat_tiles else ""

    # TOC + section cards
    toc_links = []
    section_html = []
    for title, slines in body_sections:
        sid = _slug(title)
        theme = _theme_for(title)
        copyable = theme == "info"  # proposal cards get per-item copy buttons
        toc_links.append(f'<a href="#{sid}">{html.escape(re.sub(r"^[0-9]+\.\s*", "", title))}</a>')
        section_html.append(
            f'<details class="card {theme}" id="{sid}" open>'
            f'<summary><h2>{_inline(title)}</h2></summary>'
            f"{_render_blocks(slines, copyable=copyable)}</details>"
        )
    toc_html = f'<nav class="nav-toc">{"".join(toc_links)}</nav>' if toc_links else ""

    page = _PAGE.format(
        title=html.escape(h1),
        css=_CSS,
        h1=html.escape(h1),
        lens_chip=lens_chip,
        subtitle=html.escape(subtitle),
        hero=hero_html,
        toc=toc_html,
        stats=stats_html,
        sections="\n".join(section_html),
        src=html.escape(md_path.name),
        js=_JS,
    )
    out_path = md_path.with_suffix(".html")
    out_path.write_text(page, encoding="utf-8")
    return out_path


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        print("usage: python scripts/render_insights_html.py <report.md> [--open]", file=sys.stderr)
        return 2
    md_path = Path(args[0]).resolve()
    if not md_path.exists():
        print(f"no such file: {md_path}", file=sys.stderr)
        return 1
    out = render(md_path)
    print(out)
    if "--open" in argv:
        import webbrowser

        webbrowser.open(out.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
