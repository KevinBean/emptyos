"""Assemble a full report as a single HTML document, ready for Playwright PDF or browser preview.

Pipeline:
  1. Load meta + outline + sections from the vault dir.
  2. For each section, preprocess custom tokens ({{table:x}}, figure captions, REQ refs).
  3. Render each section body through the shared `emptyos.sdk.markdown_render.render_markdown`.
  4. Wrap the whole thing in title page + TOC + sections + signoff + CSS.

The output HTML is self-contained — figures are referenced as `file://` absolute paths
so Playwright can load them when rendering to PDF.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from emptyos.sdk.utils import parse_frontmatter, strip_frontmatter

from . import tables as tables_mod

# Public so the rest of the package can inspect in tests.
FIGURE_RE = re.compile(
    r"!\[\[([^\]|]+\.(png|jpg|jpeg|gif|svg|webp))(?:\|([^\]]+))?\]\]", re.IGNORECASE
)
TABLE_TOKEN_RE = re.compile(r"\{\{table:([a-z_][a-z0-9_-]*)\}\}", re.IGNORECASE)
REQ_LINK_RE = re.compile(r"\{\{(req|risk):([A-Z]+-\d+)\}\}", re.IGNORECASE)
WIKILINK_REQ_RE = re.compile(r"\[\[((?:REQ|RISK)-\d+)\]\]", re.IGNORECASE)

# `<!-- file: path/to/thing.py -->` immediately before a fenced code block turns
# that block into a framed figure with a filename badge, Copy button, and
# Download button. The match covers both codehilite (`<div class="highlight">
# <pre>…</pre></div>`) and plain (`<pre><code>…</code></pre>`) output shapes.
CODE_BLOCK_RE = re.compile(
    r"<!--\s*file:\s*([^\n>]+?)\s*-->\s*"
    r'(<div class="highlight">.*?</div>|<pre><code[^>]*>.*?</code></pre>)',
    re.DOTALL,
)

_EXT_LANG = {
    "py": "python",
    "html": "html",
    "css": "css",
    "js": "javascript",
    "ts": "typescript",
    "toml": "toml",
    "json": "json",
    "md": "markdown",
    "yaml": "yaml",
    "yml": "yaml",
    "sh": "bash",
    "bash": "bash",
    "rs": "rust",
    "go": "go",
    "java": "java",
    "c": "c",
    "cpp": "cpp",
}

_CODE_BLOCK_CSS = """
.code-block { margin: 1.2em 0; border: 1px solid var(--border, #e5e7eb); border-radius: 10px; overflow: hidden; background: #f6f7f9; }
.code-block-head { display: flex; align-items: center; gap: 8px; padding: 6px 12px; background: #1e2330; color: #d6dce6; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; border-bottom: 1px solid #0d1017; }
.code-block-file { font-weight: 600; color: #cfe6ff; }
.code-block-lang { opacity: 0.6; text-transform: uppercase; font-size: 10px; letter-spacing: 0.06em; }
.code-block-spacer { flex: 1; }
.code-block-btn { background: transparent; border: 1px solid #3a4253; color: #d6dce6; padding: 3px 10px; border-radius: 6px; font: inherit; font-size: 11px; cursor: pointer; transition: background 0.12s, border-color 0.12s; }
.code-block-btn:hover { background: #2a3142; border-color: #556173; }
.code-block-btn[data-state="done"] { color: #9ce28c; border-color: #3e6a33; }
.code-block > .highlight, .code-block > pre { margin: 0; border-radius: 0; max-height: 28em; overflow: auto; }
.code-block > .highlight pre, .code-block > pre { padding: 12px 14px; font-size: 12.5px; line-height: 1.55; }
"""

_CODE_BLOCK_JS = r"""
document.addEventListener('click', function (e) {
    var btn = e.target.closest && e.target.closest('.code-block-btn');
    if (!btn) return;
    var fig = btn.closest('.code-block');
    if (!fig) return;
    var code = fig.querySelector('pre');
    if (!code) return;
    var text = code.innerText;
    var action = btn.getAttribute('data-action');
    if (action === 'copy') {
        var done = function () { btn.textContent = 'Copied'; btn.setAttribute('data-state', 'done'); setTimeout(function () { btn.textContent = 'Copy'; btn.removeAttribute('data-state'); }, 1400); };
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text); done(); });
        } else {
            fallbackCopy(text); done();
        }
    } else if (action === 'download') {
        var filename = fig.getAttribute('data-filename') || 'snippet.txt';
        var safe = filename.split(/[\\\\/]/).pop();
        var blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url; a.download = safe; document.body.appendChild(a); a.click();
        setTimeout(function () { document.body.removeChild(a); URL.revokeObjectURL(url); }, 0);
        btn.textContent = 'Saved'; btn.setAttribute('data-state', 'done');
        setTimeout(function () { btn.textContent = 'Download'; btn.removeAttribute('data-state'); }, 1400);
    }
});
function fallbackCopy(text) {
    var ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); } catch (_) {}
    document.body.removeChild(ta);
}
"""


def _wrap_code_blocks(html: str) -> str:
    """Decorate `<!-- file: X -->` marked code blocks with a filename header + actions."""

    def _sub(m: re.Match) -> str:
        filename = m.group(1).strip()
        body = m.group(2)
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        lang = _EXT_LANG.get(ext, ext or "text")
        safe_name = _html_escape(filename)
        return (
            f'<figure class="code-block" data-filename="{safe_name}" data-lang="{lang}">'
            f'<figcaption class="code-block-head">'
            f'<span class="code-block-file">{safe_name}</span>'
            f'<span class="code-block-lang">{lang}</span>'
            f'<span class="code-block-spacer"></span>'
            f'<button type="button" class="code-block-btn" data-action="copy">Copy</button>'
            f'<button type="button" class="code-block-btn" data-action="download">Download</button>'
            f"</figcaption>"
            f"{body}"
            f"</figure>"
        )

    return CODE_BLOCK_RE.sub(_sub, html)


def assemble_html(
    report_dir: Path,
    *,
    stylesheet_inline: str = "",
    assets_as_file_urls: bool = True,
    figure_url_prefix: str = "",
) -> str:
    """Build the full HTML document for a report.

    Args:
        report_dir: The directory containing `_meta.md`, `_outline.md`, `sections/`, `tables/`, `figures/`.
        stylesheet_inline: CSS text to embed in a <style> tag. Caller decides (print or screen).
        assets_as_file_urls: When True, figure paths become `file://...` so Playwright PDF can load them
                             from disk. When False, `figure_url_prefix` is used instead.
        figure_url_prefix: When `assets_as_file_urls` is False, figure `src` becomes
                           `{figure_url_prefix}{filename}`. Use this to point a browser
                           preview at a served API endpoint. Ignored if `assets_as_file_urls` is True.
    """
    meta_path = report_dir / "_meta.md"
    outline_path = report_dir / "_outline.md"
    sections_dir = report_dir / "sections"
    tables_dir = report_dir / "tables"
    figures_dir = report_dir / "figures"

    meta = _read_meta(meta_path)
    outline = _read_outline(outline_path)

    # Reset figure counter per render
    figure_counter = {"n": 0}

    body_parts: list[str] = []
    toc_entries: list[tuple[str, str]] = []

    for section in outline:
        slug = section["slug"]
        title = section["title"]
        render_directive = section.get("render") or ""

        section_file = sections_dir / f"{slug}.md"
        raw = section_file.read_text(encoding="utf-8") if section_file.exists() else ""
        sfm = parse_frontmatter(raw) if raw else {}
        body_md = strip_frontmatter(raw) if raw else ""

        status = sfm.get("status") or section.get("status") or ""

        # Custom token preprocessing ---
        body_md = _expand_table_tokens(body_md, tables_dir)
        body_md, figure_counter = _expand_figures(
            body_md,
            figures_dir,
            figure_counter,
            assets_as_file_urls,
            figure_url_prefix,
        )
        body_md = _expand_req_links(body_md)

        # Render markdown → HTML
        html_body, _toc = _render_md(body_md)

        # Directive-based rendering (in addition to body prose)
        directive_html = ""
        if render_directive.startswith("table:"):
            table_name = render_directive.split(":", 1)[1]
            rows = tables_mod.load_table(tables_dir / f"{table_name}.yaml")
            directive_html = tables_mod.render_table_html(table_name, rows)
        elif render_directive == "signoff":
            directive_html = _render_signoff_block(meta)

        section_id = f"sec-{slug}"
        toc_entries.append((title, section_id))
        status_badge = (
            f'<span class="section-status status-{_safe_class(status)}">{_html_escape(status)}</span>'
            if status
            else ""
        )
        body_parts.append(
            f'<section class="report-section" id="{section_id}">'
            f"<h1>{_html_escape(title)} {status_badge}</h1>"
            f"{html_body}"
            f"{directive_html}"
            f"</section>"
        )

    toc_html = _render_toc(toc_entries)
    title_page = _render_title_page(meta)
    styles = f"<style>{stylesheet_inline}</style>" if stylesheet_inline else ""

    body_html = f"{title_page}{toc_html}{''.join(body_parts)}"
    body_html = _wrap_code_blocks(body_html)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{_html_escape(meta.get("title") or "Report")}</title>
{styles}
<style>{_CODE_BLOCK_CSS}</style>
</head>
<body class="report-doc report-type-{_safe_class(meta.get("type") or "report")}">
{body_html}
<script>{_CODE_BLOCK_JS}</script>
</body>
</html>
"""


# --- Helpers -----------------------------------------------------------------


def _read_meta(meta_path: Path) -> dict:
    if not meta_path.exists():
        return {}
    content = meta_path.read_text(encoding="utf-8")
    fm = parse_frontmatter(content) or {}
    body = strip_frontmatter(content)
    fm["_body"] = body
    return fm


def _read_outline(outline_path: Path) -> list[dict]:
    """Parse _outline.md — a simple ordered list of sections.

    Format (one section per line, optional frontmatter for render directive):

        # Outline
        - slug: scope
          title: 1. Scope & Objectives
          render:
        - slug: requirements
          title: 2. Requirements
          render: table:requirements
    """
    if not outline_path.exists():
        return []
    text = outline_path.read_text(encoding="utf-8")
    # Outline is stored as YAML inside the body after a "# Outline" heading.
    # Extract everything after the first `---` / frontmatter and parse.
    try:
        import yaml
    except ImportError:
        return []
    body = strip_frontmatter(text)
    # Allow a leading "# Outline" heading
    body = re.sub(r"^#\s+Outline\s*\n", "", body, count=1)
    try:
        raw = yaml.safe_load(body)
    except Exception:
        return []
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict) and "slug" in r]
    return []


def _render_md(md_text: str) -> tuple[str, str]:
    """Shared markdown renderer — wikilinks, callouts, tables, fenced code."""
    try:
        from emptyos.sdk.markdown_render import render_markdown
    except Exception:
        return f"<pre>{_html_escape(md_text)}</pre>", ""
    try:
        return render_markdown(
            md_text, published_slugs={}, assets_prefix="figures/", link_prefix=""
        )
    except Exception:
        return f"<pre>{_html_escape(md_text)}</pre>", ""


def _expand_table_tokens(body: str, tables_dir: Path) -> str:
    """Replace {{table:requirements}} tokens with rendered HTML tables."""

    def _sub(m: re.Match) -> str:
        name = m.group(1).lower()
        rows = tables_mod.load_table(tables_dir / f"{name}.yaml")
        return tables_mod.render_table_html(name, rows)

    return TABLE_TOKEN_RE.sub(_sub, body)


def _expand_figures(
    body: str,
    figures_dir: Path,
    counter: dict,
    as_file_url: bool,
    figure_url_prefix: str = "",
) -> tuple[str, dict]:
    """Replace ![[name.png|caption]] with numbered <figure> tags.

    Figures are numbered sequentially across the whole document.
    """

    def _sub(m: re.Match) -> str:
        name = m.group(1).strip()
        caption = (m.group(3) or "").strip()
        counter["n"] += 1
        n = counter["n"]
        src_path = figures_dir / name
        if as_file_url and src_path.exists():
            src = src_path.resolve().as_uri()
        elif figure_url_prefix:
            src = figure_url_prefix + name
        else:
            src = f"figures/{name}"
        cap_text = f"Figure {n}: {caption}" if caption else f"Figure {n}"
        return (
            f'<figure class="report-figure">'
            f'<img src="{_html_escape(src)}" alt="{_html_escape(caption or name)}">'
            f"<figcaption>{_html_escape(cap_text)}</figcaption>"
            f"</figure>"
        )

    return FIGURE_RE.sub(_sub, body), counter


def _expand_req_links(body: str) -> str:
    """Turn {{req:REQ-001}} and [[REQ-001]] into internal anchor links."""

    def _sub_token(m: re.Match) -> str:
        rid = m.group(2).upper()
        return f'<a class="req-link" href="#row-{_safe_class(rid)}">{_html_escape(rid)}</a>'

    def _sub_wiki(m: re.Match) -> str:
        rid = m.group(1).upper()
        return f'<a class="req-link" href="#row-{_safe_class(rid)}">{_html_escape(rid)}</a>'

    body = REQ_LINK_RE.sub(_sub_token, body)
    body = WIKILINK_REQ_RE.sub(_sub_wiki, body)
    return body


def _render_title_page(meta: dict) -> str:
    title = meta.get("title") or "Untitled Report"
    subtitle = meta.get("subtitle") or ""
    doc_type = (meta.get("type") or "report").upper()
    version = meta.get("version") or "0.1"
    doc_date = meta.get("date") or date.today().isoformat()
    authors = meta.get("authors") or meta.get("author") or ""
    if isinstance(authors, list):
        authors = ", ".join(str(a) for a in authors)
    organisation = meta.get("organisation") or ""
    project_id = meta.get("project_id") or ""

    meta_rows = [
        ("Document Type", doc_type),
        ("Version", str(version)),
        ("Date", str(doc_date)),
        ("Author(s)", str(authors)),
    ]
    if organisation:
        meta_rows.append(("Organisation", str(organisation)))
    if project_id:
        meta_rows.append(("Project", str(project_id)))

    rows_html = "".join(
        f'<tr><th scope="row">{_html_escape(k)}</th><td>{_html_escape(v)}</td></tr>'
        for k, v in meta_rows
    )
    return f"""
<section class="report-title-page">
  <div class="title-block">
    <div class="doc-type">{_html_escape(doc_type)}</div>
    <h1 class="doc-title">{_html_escape(title)}</h1>
    {f'<p class="doc-subtitle">{_html_escape(subtitle)}</p>' if subtitle else ""}
  </div>
  <table class="title-meta">{rows_html}</table>
</section>
"""


def _render_toc(entries: list[tuple[str, str]]) -> str:
    if not entries:
        return ""
    lis = "\n".join(
        f'<li><a href="#{_html_escape(anchor)}">{_html_escape(title)}</a></li>'
        for title, anchor in entries
    )
    return f"""
<section class="report-toc">
  <h1>Contents</h1>
  <ol class="toc-list">{lis}</ol>
</section>
"""


def _render_signoff_block(meta: dict) -> str:
    approvers = meta.get("approvers") or []
    if isinstance(approvers, str):
        # Comma-separated fallback
        approvers = [a.strip() for a in approvers.split(",") if a.strip()]
    if not approvers:
        return '<p class="signoff-empty"><em>No approvers configured.</em></p>'

    rows = []
    for a in approvers:
        if isinstance(a, dict):
            role = a.get("role", "")
            name = a.get("name", "")
            date_s = a.get("date", "")
            signature = a.get("signature", "")
        else:
            role = str(a)
            name = date_s = signature = ""
        rows.append(
            f"<tr>"
            f'<td class="role">{_html_escape(role)}</td>'
            f'<td class="name">{_html_escape(name)}</td>'
            f'<td class="date">{_html_escape(date_s)}</td>'
            f'<td class="sig">{_html_escape(signature)}</td>'
            f"</tr>"
        )
    return (
        '<table class="signoff-table">'
        "<thead><tr><th>Role</th><th>Name</th><th>Date</th><th>Signature</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def _html_escape(s) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _safe_class(s) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", str(s).lower()).strip("-") or "default"
