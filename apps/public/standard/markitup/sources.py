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
(``browse`` cannot load raw HTML). ``pdf`` rasterises each page with pypdfium2
(BSD-3/Apache-2.0 — chosen over AGPL PyMuPDF for commercial use, 2026-10-03)
and anchors each text line (``measured_by: text``); ``image`` shows a file as it
is, with no anchors. Both need the optional ``emptyos[pdf]`` extra and are
refused with the install hint when it is absent.

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
    FILE_SUFFIXES,
    SOURCE_KINDS,
    confine_path,
    document_title,
    extract_mermaid,
    file_kind,
    own_daemon_url,
    slugify,
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
#   list_documents   = _sources.list_documents
#   api_documents    = _sources.api_documents
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

def _operator_posture(self) -> bool:
    """Whether the browser user is this machine's operator (``docs/AUTH.md``
    § Operator vs user). Absent config reads as operator — the local default."""
    cfg = getattr(getattr(self, "kernel", None), "config", None)
    flag = getattr(cfg, "web_is_operator", True)
    return bool(flag() if callable(flag) else flag)


def document_roots(self) -> dict[str, Path]:
    """The allow-listed roots a document source may name, by key.

    Fixed set, resolved from the running app — not from free-text config, so a
    typo in a settings field cannot widen it. ``uploads`` (this app's own
    ``data/apps/markitup/uploads/``) is created here so a path under it can be
    resolved before anything has been uploaded; the other two are read-only
    locations the deployment already has. ``docs`` is the host repo, not user
    data, so in ``user`` posture it is not a root at all — the listing, the
    resolver and the render route all narrow together.
    """
    roots = {
        "docs": self.repo_root / "docs",
        "vault": self.vault_root / "30_Resources",
        "uploads": self.data_subdir("uploads"),
    }
    if not _operator_posture(self):
        roots.pop("docs")
    return roots


def resolve_document(self, ref: str, *, kind: str = "document") -> tuple[str, str, Path] | None:
    """``(root_key, rel, absolute path)`` for a file ref of ``kind``, or None
    when the root is not allow-listed, a segment is unsafe, the path escapes
    the root, the suffix is not one that kind accepts, or the file does not
    exist."""
    split = split_document_ref(ref)
    if not split or kind not in FILE_SUFFIXES:
        return None
    root_key, rel = split
    root = document_roots(self).get(root_key)
    if root is None:
        return None
    path = confine_path(root, rel, suffixes=FILE_SUFFIXES[kind])
    if path is None or not path.is_file():
        return None
    return root_key, rel, path


#: What each file kind needs beyond the standard library, and how to get it.
PDF_EXTRA_HINT = "pip install 'emptyos[pdf]' (pypdfium2 + Pillow)"


def _has_module(name: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(name) is not None


def available_file_kinds() -> list[str]:
    """The file-backed kinds this deployment can produce shots for."""
    kinds = ["document"]
    if _has_module("PIL"):
        if _has_module("pypdfium2"):
            kinds.append("pdf")
        kinds.append("image")
    return kinds


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
    if kind in FILE_SUFFIXES:
        if not split_document_ref(ref):
            return (f"a {kind} ref is <root>/<path> with root one of "
                    f"{', '.join(DOCUMENT_ROOT_KEYS)} — got {ref!r}")
        if kind not in available_file_kinds():
            return f"{kind} sources need an optional library: {PDF_EXTRA_HINT}"
        if resolve_document(self, ref, kind=kind) is None:
            wanted = ", ".join(sorted(FILE_SUFFIXES[kind]))
            return (f"{kind} {ref!r} was refused: it must be an existing {wanted} "
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


#: What the picker calls each root. Keyed by the allow-list, so a label cannot
#: exist for a root that the gate would refuse.
DOCUMENT_ROOT_LABELS = {
    "docs": "docs/ (this repo)",
    "vault": "vault 30_Resources/",
    "uploads": "uploads/ (this app)",
}

#: Ceiling on one listing. The vault's 30_Resources can hold thousands of
#: notes; the picker filters as the user types, so a page of matches is what
#: it needs, not the tree.
DOCUMENT_LIST_LIMIT = 300


def _is_link(path: Path) -> bool:
    """A symlink or a Windows directory junction — a way out of the root.

    ``os.walk(followlinks=False)`` prunes symlinks only; it still descends a
    junction, and ``confine_path``'s ``resolve()`` then refuses every path
    under it. Without this the *names* under a junction's target would be
    enumerated and offered as dead options.
    """
    import os

    return path.is_symlink() or os.path.isjunction(path)


def list_documents(self, root_key: str, q: str = "", limit: int = DOCUMENT_LIST_LIMIT) -> dict | None:
    """Reviewable files under one allow-listed root, as ``<rel>`` paths —
    markdown always, PDFs and images when this deployment can produce them.

    ``None`` for a root that is not allow-listed. Every segment passes
    ``safe_path_segment`` — the exact predicate ``confine_path`` applies — so
    the picker never offers a path the gate would then turn down (a name with
    a space, a non-ASCII name, a hidden name, a reserved stem); links out of
    the root are pruned for the same reason. ``q`` is a case-insensitive
    substring filter on the relative path. Walk order — names sorted within
    each directory, a directory's own files before its subdirectories' — so
    the listing is stable between calls; ``truncated`` says the ceiling cut
    it (a further match exists), never merely that the page is full.
    """
    import os

    from emptyos.sdk.utils import safe_path_segment

    root = document_roots(self).get(root_key)
    if root is None:
        return None
    needle = (q or "").strip().casefold()
    limit = max(1, int(limit or DOCUMENT_LIST_LIMIT))
    kinds = set(available_file_kinds())
    found: list[str] = []
    truncated = False
    if root.is_dir():
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames
                                 if safe_path_segment(d) and not _is_link(Path(dirpath, d)))
            for name in sorted(filenames):
                p = Path(dirpath, name)
                if (not safe_path_segment(name) or file_kind(name) not in kinds
                        or _is_link(p)):
                    continue
                rel = Path(dirpath, name).relative_to(root).as_posix()
                if needle and needle not in rel.casefold():
                    continue
                if len(found) >= limit:
                    truncated = True
                    break
                found.append(rel)
            if truncated:
                break
    return {"root": root_key, "files": found, "truncated": truncated}


@web_route("GET", "/api/documents")
async def api_documents(self, request):
    """The source picker's menu: every root, and the files under one of them.

    ``?root=<key>`` lists that root (``?q=`` narrows by substring); without it
    only the roots come back. The walk runs off the event loop — a vault root
    is the user's whole resource tree and one slow disk must not hold the bus.
    """
    import asyncio

    # From document_roots, not the static key list: in user posture ``docs``
    # is not a root, and the menu must not offer what the gate refuses.
    live = list(document_roots(self))
    roots = [{"key": k, "label": DOCUMENT_ROOT_LABELS.get(k, k)} for k in live]
    # Suffix → source kind for EVERY file kind, so the page always sends the
    # kind a path means and the daemon can answer with the install hint when
    # that kind is not producible here; ``file_kinds`` says which are.
    menu = {
        "roots": roots,
        "suffix_kinds": {suf: kind for kind, sufs in FILE_SUFFIXES.items() for suf in sorted(sufs)},
        "file_kinds": available_file_kinds(),
    }
    root_key = str(request.query_params.get("root") or "")
    if not root_key:
        return menu
    if root_key not in live:
        return {"error": f"root must be one of {', '.join(live)}", **menu}
    listed = await asyncio.to_thread(
        list_documents, self, root_key, str(request.query_params.get("q") or ""))
    return {**menu, **(listed or {"root": root_key, "files": [], "truncated": False})}


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


#: Render scale for a PDF page: 2.0 is 144 dpi — the page's own text is
#: readable in the shot at the default viewer width without a zoom.
DEFAULT_PDF_SCALE = 2.0
#: Pages past this are not rasterised. A shot costs a review call each, so a
#: 300-page standard must be reviewed in chosen ranges, not wholesale.
DEFAULT_PDF_MAX_PAGES = 30
#: Text lines anchored per page; a dense standard page runs to ~80.
PDF_MAX_ANCHORS = 400
#: Page text handed to the review pass, per page.
PDF_MAX_TEXT_CHARS = 20_000


def _write_shot_files(shots_dir: Path, shot_id: str, image, anchors: list[dict],
                      origin: dict, measured_by: str) -> None:
    """The PNG and its anchors sidecar — the same pair ``capture_view`` writes."""
    import json

    shots_dir.mkdir(parents=True, exist_ok=True)
    image.save(shots_dir / f"{shot_id}.png", format="PNG")
    (shots_dir / f"{shot_id}.anchors.json").write_text(
        json.dumps({"anchors": anchors, "origin": origin, "measured_by": measured_by},
                   ensure_ascii=False),
        encoding="utf-8",
    )


#: Pixel ceiling for one page or image shot (~40 Mpx: an A0 sheet at 150 dpi).
#: pdfium does not clamp a page size, so a 200-inch MediaBox at the default
#: scale asks for a multi-gigabyte bitmap; the page is rendered smaller instead.
MAX_SHOT_PIXELS = 40_000_000


def _page_anchors(textpage, bbox: tuple, scale: float, width_px: int,
                  height_px: int) -> list[dict]:
    """One anchor per text line, in shot pixels with a top-left origin.

    pdfium's rects are in page user space, bottom-left origin, one per run of
    text on a line — the same granularity a document's row anchor has. The
    rendered bitmap covers the page's visible box, which need not start at
    (0, 0): a MediaBox or CropBox offset is common in printed standards. So x
    is measured from the box's left edge and y down from its top edge (measured
    2026-10-03 against the rendered ink on plain, offset-MediaBox and CropBox
    pages). A rect with no text, or one outside the rendered box (cropped
    away), is not offered — a pin drawn from it would land off the picture.
    """
    box_left, _box_bottom, _box_right, box_top = bbox
    anchors: list[dict] = []
    for k in range(textpage.count_rects()):
        left, bottom, right, top = textpage.get_rect(k)
        text = " ".join(textpage.get_text_bounded(left, bottom, right, top).split())
        if not text:
            continue
        rect = {"x": (left - box_left) * scale, "y": (box_top - top) * scale,
                "w": (right - left) * scale, "h": (top - bottom) * scale}
        if (rect["x"] < 0 or rect["y"] < 0 or rect["x"] + rect["w"] > width_px + 1
                or rect["y"] + rect["h"] > height_px + 1):
            continue
        anchors.append({"el": f"e{len(anchors)}", "tag": "line", "text": text[:80], "rect": rect})
        if len(anchors) >= PDF_MAX_ANCHORS:
            break
    return anchors


def _page_scale(width_pt: float, height_pt: float, scale: float) -> float:
    """``scale``, reduced so the page bitmap stays under ``MAX_SHOT_PIXELS``."""
    area = max(width_pt, 1.0) * max(height_pt, 1.0)
    return min(scale, (MAX_SHOT_PIXELS / area) ** 0.5)


def _rasterise_pdf(path: Path, shots_dir: Path, *, title: str, scale: float,
                   max_pages: int) -> tuple[list[dict], int, str]:
    """Render up to ``max_pages`` pages; returns ``(shots, page_count, sha256)``.

    Blocking (pdfium is synchronous) — call through ``asyncio.to_thread``. The
    file is read once, so the hash and the pixels come from the same bytes.
    A rotated page is rendered but gets no anchors: its text rects are in the
    unrotated page frame. If any page fails, the files already written for
    this source are removed before the error propagates — the source fails as
    a unit and leaves nothing half-made in the run folder.
    """
    import hashlib
    import uuid

    import pypdfium2 as pdfium

    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    shots: list[dict] = []
    written: list[Path] = []
    pdf = pdfium.PdfDocument(data)
    try:
        total = len(pdf)
        for i in range(min(total, max_pages)):
            page = pdf[i]
            try:
                width_pt, height_pt = page.get_size()
                page_scale = _page_scale(width_pt, height_pt, scale)
                image = page.render(scale=page_scale).to_pil()
                w, h = image.size
                textpage = page.get_textpage()
                try:
                    text = textpage.get_text_range()
                    rotated = page.get_rotation() % 360 != 0
                    anchors = [] if rotated else _page_anchors(
                        textpage, page.get_bbox(), page_scale, w, h)
                finally:
                    textpage.close()
            finally:
                page.close()
            origin = {"x": 0, "y": 0, "w": w, "h": h}
            shot_id = f"{slugify(title)}-p{i + 1}-{uuid.uuid4().hex[:6]}"
            written += [shots_dir / f"{shot_id}.png", shots_dir / f"{shot_id}.anchors.json"]
            _write_shot_files(shots_dir, shot_id, image, anchors, origin,
                              "none" if rotated else "text")
            shots.append({
                "id": shot_id,
                "title": f"{title} — page {i + 1}",
                "url": "",
                "selector": "",
                "viewport": {"w": w, "h": h},
                "focus": "",
                "image": f"{shot_id}.png",
                "origin": origin,
                "anchor_count": len(anchors),
                "text": " ".join(text.split())[:PDF_MAX_TEXT_CHARS],
                "page": i + 1,
            })
    except BaseException:
        for f in written:
            f.unlink(missing_ok=True)
        raise
    finally:
        pdf.close()
    return shots, total, sha


def _image_shot(path: Path, shots_dir: Path, *, title: str) -> tuple[dict, str]:
    """An image file as one unmeasured shot; returns ``(shot, sha256)``.

    The size is checked from the header before any pixel is decoded, against
    the same ceiling a PDF page gets — Pillow's own bomb guard only warns below
    twice its limit, after which the full decode and a converted copy would
    both be held.
    """
    import hashlib
    import io
    import uuid

    from PIL import Image

    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    with Image.open(io.BytesIO(data)) as im:
        if im.size[0] * im.size[1] > MAX_SHOT_PIXELS:
            raise ValueError(f"image is {im.size[0]}x{im.size[1]} px; the ceiling is "
                             f"{MAX_SHOT_PIXELS // 1_000_000} Mpx")
        image = im.convert("RGBA" if "A" in im.getbands() else "RGB")
    w, h = image.size
    origin = {"x": 0, "y": 0, "w": w, "h": h}
    shot_id = f"{slugify(title)}-{uuid.uuid4().hex[:6]}"
    _write_shot_files(shots_dir, shot_id, image, [], origin, "none")
    return {
        "id": shot_id, "title": title, "url": "", "selector": "",
        "viewport": {"w": w, "h": h}, "focus": "", "image": f"{shot_id}.png",
        "origin": origin, "anchor_count": 0, "text": "",
    }, sha


def _resolve_file(self, source: dict) -> tuple[str, str, Path]:
    kind = str(source.get("kind") or "")
    if kind not in available_file_kinds():
        raise SourceUnavailable(f"{kind} sources need an optional library: {PDF_EXTRA_HINT}")
    resolved = resolve_document(self, source["ref"], kind=kind)
    if resolved is None:
        raise ValueError(source_refusal(self, source) or f"{kind} refused")
    return resolved


async def _produce_pdf(self, source: dict, viewports, shots_dir: Path, context_id):
    import asyncio

    root_key, rel, path = _resolve_file(self, source)
    scale = float(self.setting_or_config("markitup.pdf_scale", DEFAULT_PDF_SCALE)
                  or DEFAULT_PDF_SCALE)
    max_pages = int(self.setting_or_config("markitup.pdf_max_pages", DEFAULT_PDF_MAX_PAGES)
                    or DEFAULT_PDF_MAX_PAGES)
    title = source.get("title") or path.stem
    shots, total, sha = await asyncio.to_thread(
        _rasterise_pdf, path, shots_dir, title=title,
        scale=max(0.5, min(scale, 4.0)), max_pages=max(1, max_pages))
    if not shots:
        raise ValueError(f"pdf {root_key}/{rel} has no pages")
    for shot in shots:
        shot["source"] = {"kind": "pdf", "ref": f"{root_key}/{rel}", "sha256": sha,
                          "page": shot.pop("page"), "pages": total}
    return shots


async def _produce_image(self, source: dict, viewports, shots_dir: Path, context_id):
    import asyncio

    root_key, rel, path = _resolve_file(self, source)
    shot, sha = await asyncio.to_thread(_image_shot, path, shots_dir,
                                        title=source.get("title") or path.stem)
    shot["source"] = {"kind": "image", "ref": f"{root_key}/{rel}", "sha256": sha}
    return [shot]


PRODUCERS = {
    "web": _produce_web,
    "document": _produce_document,
    "pdf": _produce_pdf,
    "image": _produce_image,
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
