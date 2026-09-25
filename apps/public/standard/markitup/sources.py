"""markitup — source producers: how a source becomes shots.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: the producer registry keyed by source kind, the document root
allow-list, path resolution for file-backed sources, the rendered-document
route, and the per-kind producers. The capture stage calls ``produce_shots``
and never learns what kind it was given; the review pass, pins, statuses and
exports read shots and sidecars exactly as before.

Two producers are live. ``web`` is the original browser capture. ``document``
renders a markdown file from an allow-listed root to HTML, serves it at an
own-daemon URL, and then *reuses* the browser capture on that URL — so the
reviewer reads the very page the capture measured, the own-daemon gate and the
deep-link sign-in apply unchanged, and no new browse operation is needed
(``browse`` cannot load raw HTML). ``pdf`` and ``image`` are registered so the
vocabulary is complete, and refuse with a reason until their producers land.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``self.capture_view`` (capture.py) through the
bound method, never by import. Do not import from ``.app`` (it imports us,
which would cycle).
"""

from __future__ import annotations

import hashlib
import re
import time
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import web_route

from .shared import (
    DOCUMENT_ROOT_KEYS,
    SOURCE_KINDS,
    confine_path,
    document_title,
    extract_mermaid,
    own_daemon_url,
    split_document_ref,
    stamp_ids,
)

if TYPE_CHECKING:
    from .app import MarkitupApp  # noqa: F401 — for type hints only


# ─── Bind to MarkitupApp class as ────────────────────────────────────
#   document_roots   = _sources.document_roots
#   resolve_document = _sources.resolve_document
#   render_document  = _sources.render_document
#   api_render       = _sources.api_render
#   source_refusal   = _sources.source_refusal
#   produce_shots    = _sources.produce_shots
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────

#: Default capture width for a rendered document. One width, not the page
#: sweep: a document has no mobile layout worth a second review pass, and two
#: widths would double the comment budget for nothing.
DEFAULT_DOCUMENT_VIEWPORT = "1200x900"

#: Row-level anchors for a document. ``td``/``th``/``table``/``div``/``span``
#: are dropped because a comment is about a row or a paragraph, never a cell,
#: and keeping them tripled the menu on a requirements table.
DOCUMENT_ANCHOR_TAGS: frozenset[str] = frozenset({
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "tr", "blockquote", "img",
    "figure", "figcaption", "pre",
})

#: Measured 2026-09-10 on the Engineering Workbench spec pack at 1200 px with
#: row anchors on: the 185-line system-requirements document yielded 132
#: anchors (85 of them table rows); the 420-line calculation specification is
#: roughly three times that. The page default of 300 would drop the tail of
#: every document past the first, in document order, so the ceiling is set
#: with headroom over the largest document in the pack.
DOCUMENT_MAX_ANCHORS = 1200

#: Loaded only when a document actually contains a diagram. Same build the
#: shared deck renderer uses.
MERMAID_SRC = "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"


class SourceUnavailable(ValueError):
    """A source of a known kind that this deployment cannot produce shots for.

    Distinct from a refusal of the *source* (bad path, unknown kind): the source
    is fine, the producer is not here. The capture stage records it as a
    per-source failure with this message, so the reviewer reads "PDF producer
    not available" rather than a stack trace.
    """


# ── roots and resolution ─────────────────────────────────────────────

def document_roots(self) -> dict[str, Path]:
    """The allow-listed roots a document source may name, by key.

    Fixed set, resolved from the running app — not from free-text config, so a
    typo in a settings field cannot widen it. ``uploads`` (this app's own
    ``data/apps/markitup/uploads/``) is created here so a path under it can be
    resolved before anything has been uploaded; the other two are read-only
    locations the deployment already has.
    """
    return {
        "docs": self.repo_root / "docs",
        "vault": self.vault_root / "30_Resources",
        "uploads": self.data_subdir("uploads"),
    }


def resolve_document(self, ref: str) -> tuple[str, str, Path] | None:
    """``(root_key, rel, absolute path)`` for a document ref, or None when the
    root is not allow-listed, a segment is unsafe, the path escapes the root,
    the suffix is not a document, or the file does not exist."""
    split = split_document_ref(ref)
    if not split:
        return None
    root_key, rel = split
    root = document_roots(self).get(root_key)
    if root is None:
        return None
    path = confine_path(root, rel)
    if path is None or not path.is_file():
        return None
    return root_key, rel, path


def source_refusal(self, source: dict) -> str | None:
    """``None`` if this source may be produced, else a human reason.

    Checked at ``start_review`` so a bad source is an in-band error before a
    run is created, not a per-source failure discovered after the browser has
    been paid for.
    """
    kind = str(source.get("kind") or "")
    ref = str(source.get("ref") or "")
    if kind not in SOURCE_KINDS:
        return f"unknown source kind {kind!r} — one of {', '.join(SOURCE_KINDS)}"
    if kind == "web":
        return None  # capture_view applies the URL gate itself
    if kind == "document":
        if not split_document_ref(ref):
            return (f"a document ref is <root>/<path> with root one of "
                    f"{', '.join(DOCUMENT_ROOT_KEYS)} — got {ref!r}")
        if resolve_document(self, ref) is None:
            return (f"document {ref!r} was refused: it must be an existing markdown "
                    "file under its root, with plain path segments (no '..', no "
                    "hidden names)")
        return None
    return f"{kind} sources are not available in this deployment yet"


# ── rendering ────────────────────────────────────────────────────────

_HEADER_LINK_RE = re.compile(r'<a[^>]*class="header-link"[^>]*>.*?</a>', re.S)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def render_document(self, ref: str) -> dict | None:
    """Render a document source to a self-describing HTML page.

    Returns ``{title, html, sha256, root, rel}`` or None when the ref is
    refused. The page carries the source's identity (root/rel, content hash,
    modified time) in a header so every screenshot of it is version-bound
    without any page-side script.
    """
    resolved = resolve_document(self, ref)
    if resolved is None:
        return None
    root_key, rel, path = resolved
    try:
        raw = path.read_bytes()
        mtime = path.stat().st_mtime
    except OSError:
        # Deleted or made unreadable between the gate and the read: an in-band
        # "no such document", not a 500 (.claude/rules/dev-gotchas.md).
        return None
    # One read serves both the hash and the render, so the hash in the header
    # is the hash of the bytes that were rendered — not of a later revision.
    sha = hashlib.sha256(raw).hexdigest()
    # Bytes are read (the hash must cover the file exactly), so newlines are
    # normalised by hand — a CRLF note, common on Windows, otherwise defeats
    # the line-anchored fence and front-matter scans.
    md = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    title = document_title(md, path.stem)

    from emptyos.frontmatter import strip_frontmatter
    from emptyos.sdk.markdown_render import render_markdown

    # Front matter is metadata, not content. Every vault note carries one, and
    # rendered verbatim it became the first anchorable paragraph of every shot.
    md_body, n_mermaid = extract_mermaid(strip_frontmatter(md))
    body, _toc = render_markdown(md_body, published_slugs=None, assets_prefix="")
    # The renderer's heading permalinks (`¶`) are web chrome; here they would
    # land in every heading's anchor text and so in the review prompt and the
    # pin label. Same strip the PDF renderer applies.
    body = _HEADER_LINK_RE.sub("", body)
    body = stamp_ids(body)

    modified = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
    meta = f"{root_key}/{rel} · sha256 {sha[:12]} · modified {modified}"
    mermaid = ""
    if n_mermaid:
        mermaid = (f'<script src="{MERMAID_SRC}"></script>'
                   '<script>window.mermaid && mermaid.initialize({startOnLoad: true});</script>')
    # `theme-eos` on <html>: theme.css defines --border/--accent/--surface only
    # under a .theme-* class, and the platform's theme bootstrap is injected on
    # `pages/` responses only — without the class the captured table has no
    # cell borders and no header fill. The default theme is fixed here rather
    # than read from the viewer, because the capture has no viewer.
    html = (
        "<!doctype html><html class=\"theme-eos\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{escape(title)}</title>"
        "<link rel=\"stylesheet\" href=\"/static/theme.css\">"
        "<link rel=\"stylesheet\" href=\"/static/eos-components.css\">"
        "<style>"
        ".mk-doc{max-width:960px;margin:0 auto;padding:24px 28px 64px;line-height:1.5}"
        ".mk-doc table{border-collapse:collapse;width:100%;margin:12px 0;font-size:.92em}"
        ".mk-doc td,.mk-doc th{border:1px solid var(--border);padding:4px 8px;vertical-align:top;text-align:left}"
        ".mk-doc th{background:var(--surface-2,var(--surface))}"
        ".mk-doc pre{overflow-x:auto;padding:8px 12px;background:var(--surface-2,var(--surface));border-radius:6px}"
        ".mk-doc .mermaid{margin:12px 0}"
        ".mk-doc-meta{font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--text-muted);margin-bottom:16px}"
        ".mk-doc tr:target,.mk-doc a[id]:target+h1,.mk-doc a[id]:target+h2,.mk-doc a[id]:target+h3,.mk-doc a[id]:target+h4{outline:2px solid var(--accent);outline-offset:2px}"
        "</style></head><body><main class=\"mk-doc\">"
        f"<header><div class=\"mk-doc-meta\">{escape(meta)}</div></header>"
        f"{body}</main>{mermaid}</body></html>"
    )
    return {"title": title, "html": html, "sha256": sha, "root": root_key, "rel": rel}


#: Same two headers the proposal route ships, for the same reason: this is
#: markdown from a vault or a repo rendered with raw HTML allowed and served
#: from OUR origin. `sandbox allow-scripts` gives the document an opaque origin
#: — a script inside a note cannot read the session cookie or fetch with the
#: daemon's authority — while the page still renders, screenshots, loads
#: /static/ (auth-exempt) and runs the diagram library. Measured on the
#: proposal route; the same shape here.
RENDER_HEADERS = {
    "Content-Security-Policy": "sandbox allow-scripts",
    "X-Content-Type-Options": "nosniff",
}


@web_route("GET", "/api/render/{root}/{rel:path}")
async def api_render(self, request):
    """Serve a document source as a page — the URL the document producer
    captures, and the one a reviewer opens to read alongside the pins."""
    from starlette.responses import HTMLResponse, JSONResponse

    root = request.path_params.get("root", "")
    rel = request.path_params.get("rel", "")
    rendered = render_document(self, f"{root}/{rel}")
    if rendered is None:
        return JSONResponse(
            {"error": "no such document — the root must be one of "
                      f"{', '.join(DOCUMENT_ROOT_KEYS)} and the path an existing "
                      "markdown file under it"},
            status_code=404,
        )
    return HTMLResponse(rendered["html"], headers=RENDER_HEADERS)


def _document_url(self, root_key: str, rel: str) -> str:
    """The own-daemon address of a rendered document."""
    from urllib.parse import quote

    return own_daemon_url(self.kernel.config, f"markitup/api/render/{root_key}/{quote(rel)}")


# ── producers ────────────────────────────────────────────────────────

async def _produce_web(self, source: dict, viewports: list[str], shots_dir: Path,
                       context_id: str) -> list[dict]:
    shots = []
    for vp in viewports:
        shot = await self.capture_view(
            url=source["ref"], title=source.get("title", ""),
            selector=source.get("selector", ""), viewport=vp,
            focus=source.get("focus", ""), context_id=context_id,
            shots_dir=shots_dir,
        )
        shot["source"] = {"kind": "web", "ref": source["ref"], "sha256": ""}
        shots.append(shot)
    return shots


async def _produce_document(self, source: dict, viewports: list[str], shots_dir: Path,
                            context_id: str) -> list[dict]:
    resolved = resolve_document(self, source["ref"])
    if resolved is None:
        raise ValueError(source_refusal(self, source) or "document refused")
    root_key, rel, path = resolved
    # Hashed here, at capture time, and again by the route when the browser
    # fetches. The shot's hash is the version binding; the page header is a
    # display of the same fact and can only differ if the file changed in the
    # milliseconds between the two reads.
    sha = _sha256(path)
    vp = str(self.setting_or_config("markitup.document_width", DEFAULT_DOCUMENT_VIEWPORT)
             or DEFAULT_DOCUMENT_VIEWPORT).strip()
    shot = await self.capture_view(
        url=_document_url(self, root_key, rel),
        title=source.get("title") or document_title(
            path.read_text(encoding="utf-8", errors="replace"), path.stem),
        selector="", viewport=vp, focus=source.get("focus", ""),
        context_id=context_id, shots_dir=shots_dir,
        anchor_tags=DOCUMENT_ANCHOR_TAGS, max_anchors=DOCUMENT_MAX_ANCHORS,
        anchor_rows=True,
    )
    shot["source"] = {"kind": "document", "ref": f"{root_key}/{rel}", "sha256": sha}
    return [shot]


async def _produce_unavailable(self, source: dict, viewports, shots_dir, context_id):
    raise SourceUnavailable(
        f"{source.get('kind')} producer is not available in this deployment "
        "(plan markitup-source-adapters T5)")


PRODUCERS = {
    "web": _produce_web,
    "document": _produce_document,
    "pdf": _produce_unavailable,
    "image": _produce_unavailable,
}


async def produce_shots(self, source: dict, *, viewports: list[str], shots_dir: Path,
                        context_id: str) -> list[dict]:
    """Shots for one source, whatever its kind.

    The capture stage's only entry point. A source the deployment cannot
    produce raises :class:`SourceUnavailable`; a source that is refused raises
    ``ValueError`` — both are recorded per source by the stage, and one bad
    source never voids the run.
    """
    kind = str(source.get("kind") or "web")
    producer = PRODUCERS.get(kind)
    if producer is None:
        raise ValueError(f"unknown source kind {kind!r}")
    return await producer(self, source, viewports, shots_dir, context_id)
