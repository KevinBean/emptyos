"""KB — locate + open the physical source PDF for a standards note.

Bridges a KB `reference`/`clause`/`case` note (which cites a standard textually,
e.g. "IEC 60853-1:1985 §2.3") to the actual PDF on disk via the `files` search
domain (voidtools Everything / mdfind / fd). First consumer of
``self.search(q, domain="files")`` — the "local discovery layer" that finds
files outside the vault (Downloads standards corpus, OneDrive) that grep/semble
never see.

Locate-on-demand — nothing is persisted, so a moved/renamed PDF is always
re-found fresh (the index is the source of truth, not a stored path).

The scope/open/roots plumbing lives on ``BaseApp`` (``files_domain_active``,
``open_local_file``) — shared with cable-library; this module keeps only the
KB-specific query derivation + the two routes.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``self.get_note`` (notes.py).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

from emptyos.sdk import web_route

if TYPE_CHECKING:
    from .app import KBApp  # noqa: F401 — for type hints only


# ─── Bind to KBApp class as ──────────────────────────────────────────
#   api_source_pdf  = _source_pdf.api_source_pdf
#   api_open_local  = _source_pdf.api_open_local
#   _standard_query = _source_pdf._standard_query
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────


# A standards number ("60853", "60287") is the stable token — filenames vary
# wildly in spacing/dashes ("IEC 60853-1 1985.pdf", "iec608533ed_..."), but the
# bare number substring-matches them all.
_NUM_RE = re.compile(r"\b(\d{4,6})\b")


def _standard_query(self, props: dict) -> str:
    """Derive a filename-search term from a standards note's frontmatter."""
    for key in ("standard_id", "standard", "title"):
        v = str(props.get(key) or "").strip()
        if not v:
            continue
        m = _NUM_RE.search(v)
        if m:
            return m.group(1)
        return v
    return ""


@web_route("GET", "/api/source-pdf/{slug}", operator=True)
async def api_source_pdf(self, request):
    """Locate physical PDF(s) for a standards note via the files search domain."""
    slug = request.path_params.get("slug", "")
    note = await self.get_note(slug)
    if not isinstance(note, dict) or note.get("error"):
        return {"error": "note not found", "slug": slug}
    props = note.get("properties") or {}
    query = self._standard_query(props)
    active = self.files_domain_active()
    if not query:
        return {"slug": slug, "query": "", "results": [], "files_domain": active,
                "reason": "no standard identifier on this note"}
    try:
        hits = await self.search(query, domain="files", type="pdf", limit=25)
    except Exception as e:  # noqa: BLE001 — surface any backend error to the UI
        return {"slug": slug, "query": query, "results": [], "files_domain": active,
                "reason": str(e)}
    results = [
        {"path": h.get("path", ""), "name": os.path.basename(h.get("path", ""))}
        for h in (hits or []) if h.get("path")
    ]
    return {"slug": slug, "query": query, "results": results, "files_domain": active}


@web_route("POST", "/api/open-local", operator=True)
async def api_open_local(self, request):
    """Open a located file in the user's default app (guards live in BaseApp)."""
    body = await request.json()
    return await self.open_local_file(str((body or {}).get("path") or ""))
