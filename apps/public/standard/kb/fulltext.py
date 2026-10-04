"""kb — full-text reader backend: serve a standard's stored verbatim text + index.

A ``kind: reference`` note stores the complete verbatim standard text via a
``local_text``/``source_file`` path (a personal study aid, never published). This
module exposes it for the 3-column full-text reader:

  - ``GET /api/notes/{slug}/fulltext`` → the DOCUMENT TOC (every section, parsed
    from the standard's own contents via ``doc_slice.parse_contents``) — small.
  - ``GET /api/notes/{slug}/fulltext/section?no=<n>`` → the verbatim text of one
    section or whole chapter (``doc_slice.slice_clause_text``), lazily, so the
    181-page document is never shipped in one response.

Dark-flagged behind ``[apps.kb] feature.fulltext-reader.enabled`` (default off →
both routes 200 with ``{error}``, the tab never appears). Local-only; the verbatim
text is the same artifact already on disk, just rendered for reading.

Bound onto ``KBApp`` (multi-module pattern). Reaches ``self._all_notes`` (notes),
``self.vault_root`` (base). Do not import from ``.app``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from emptyos.sdk import web_route
from emptyos.sdk.doc_slice import parse_contents, slice_clause_text

from .shared import _slug_of

if TYPE_CHECKING:
    from .app import KBApp  # noqa: F401 — for type hints only


# ─── Bind to KBApp class as ────────────────────────────────
#   _fulltext_enabled    = _fulltext._fulltext_enabled
#   _note_by_slug        = _fulltext._note_by_slug
#   _read_fulltext_for   = _fulltext._read_fulltext_for
#   api_fulltext_index   = _fulltext.api_fulltext_index
#   api_fulltext_section = _fulltext.api_fulltext_section
# Adding a method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────


def _fulltext_enabled(self) -> bool:
    """Dark-ship gate (default off → routes 400, no reader tab)."""
    return bool(self.app_config("feature.fulltext-reader.enabled", False))


def _note_by_slug(self, slug: str) -> dict | None:
    # Slug-addressed, so a project-scoped source (client spec) is reachable too.
    return next((n for n in self._all_notes(include_project=True)
                 if _slug_of(n.get("path", "")) == slug), None)


def _clean_fulltext(text: str) -> str:
    """Drop extraction noise so heading detection + reading are clean.

    Removes the repeated 'Official' running header and the page-ruler lines
    ("2 | ____", "1 | | ____") — the latter otherwise look like a chapter
    heading ("7") to the slicer.
    """
    out: list[str] = []
    for line in (text or "").splitlines():
        s = line.strip()
        if s == "Official":
            continue
        if re.match(r"^\d+\s*\|[\s|_]*$", s):  # page ruler: N | …pipes/underscores… EOL
            continue
        out.append(line)
    return "\n".join(out)


def _read_fulltext_for(self, props: dict) -> str:
    """Read + clean a reference note's stored full text (local_text/source_file), '' if none.

    Cleaned at read so both the contents parse and the per-section slice work on
    ruler-free text (the index parse keeps the dot-leader contents rows intact).
    """
    rel = str((props or {}).get("local_text") or (props or {}).get("source_file") or "").strip()
    if not rel:
        return ""
    try:
        raw = (self.vault_root / rel).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    return _clean_fulltext(raw).strip()


@web_route("GET", "/api/notes/{slug}/fulltext")
async def api_fulltext_index(self, request):
    """Return the document TOC (section index) for a reference note's stored full text."""
    if not self._fulltext_enabled():
        return {"error": "fulltext reader disabled"}
    slug = request.path_params.get("slug", "")
    n = self._note_by_slug(slug)
    if not n:
        return {"error": "not found", "slug": slug}
    props = n.get("properties", {}) or {}
    if props.get("kind") != "reference":
        return {"error": "not a reference note", "has_fulltext": False}
    txt = self._read_fulltext_for(props)
    if not txt:
        return {"error": "no full text stored", "has_fulltext": False}
    contents = parse_contents(txt)
    return {
        "ok": True, "slug": slug, "has_fulltext": True,
        "edition": props.get("edition"),
        "title": props.get("title") or slug.replace("-", " "),
        "contents": contents, "section_count": len(contents),
    }


@web_route("GET", "/api/notes/{slug}/fulltext/section")
async def api_fulltext_section(self, request):
    """Return the verbatim text of one section/chapter, sliced from the full text."""
    if not self._fulltext_enabled():
        return {"error": "fulltext reader disabled"}
    slug = request.path_params.get("slug", "")
    no = (request.query_params.get("no") or request.query_params.get("chapter") or "").strip()
    if not no:
        return {"error": "no= (section or chapter number) required"}
    n = self._note_by_slug(slug)
    if not n:
        return {"error": "not found", "slug": slug}
    props = n.get("properties", {}) or {}
    txt = self._read_fulltext_for(props)  # already cleaned
    if not txt:
        return {"error": "no full text stored"}
    text = slice_clause_text(txt, no).strip()
    return {"ok": True, "no": no, "text": text, "empty": not text}
