"""Library — metadata autofill (DOI/URL/title → citation fields) + paper
creation + keyword search + the app's declared verbs.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: resolving citation metadata from a DOI (CrossRef, structured)
or a title/URL (OpenAlex/Semantic Scholar search fallback, coarser), and the
one write path every "add a paper" surface funnels through — the import
modal, the `library.add_paper` verb, and (via `resolve_metadata`) the
PDF-import DOI-sniff in `storage.py`.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``.shared`` (pure helpers only).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from emptyos.sdk import crossref_lookup, openalex_search, semantic_scholar_search, web_route
from emptyos.sdk.utils import path_segment_error

from .shared import PAPER_TAG, citekey_for, find_doi, normalize_str_list

if TYPE_CHECKING:
    from .app import LibraryApp  # noqa: F401 — for type hints only


# ─── Bind to LibraryApp class as ──────────────────────────────────────────
#   resolve_metadata   = _metadata.resolve_metadata
#   create_paper        = _metadata.create_paper
#   api_import_preview  = _metadata.api_import_preview
#   api_add_paper       = _metadata.api_add_paper
#   add_paper           = _metadata.add_paper       (verb: library.add_paper)
#   search               = _metadata.search           (verb: library.search)
#   voice_search         = _metadata.voice_search      (voice intent)
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────────


async def resolve_metadata(self, *, doi: str = "", url: str = "", title: str = "") -> dict:
    """Resolve paper metadata from a DOI, a URL (DOI-sniffed), or a title
    (search fallback). Returns `{}` when nothing resolves — the caller
    always falls back to manual entry; this never raises."""
    mailto = self.setting_or_config("library.crossref_mailto", "", config_key="crossref_mailto") or ""

    doi = (doi or find_doi(url or "")).strip()
    if doi:
        meta = await asyncio.to_thread(crossref_lookup, doi, mailto=mailto)
        if meta:
            meta["source"] = "crossref"
            return meta

    query = (title or url or doi).strip()
    if not query:
        return {}

    # Search fallback: no bare-DOI to resolve, so triangulate a candidate by
    # title/URL. These only return {url, title, note} — coarser than
    # CrossRef's structured record (no authors/journal), which is why
    # CrossRef is tried first whenever a DOI is available.
    for search_fn, source in ((openalex_search, "openalex"), (semantic_scholar_search, "semantic-scholar")):
        try:
            hits = await asyncio.to_thread(search_fn, query, 1)
        except Exception:
            hits = []
        if not hits:
            continue
        hit = hits[0]
        year = ""
        first_bit = (hit.get("note") or "").split(" · ")[0].strip()
        if first_bit.isdigit():
            year = first_bit
        return {
            "title": hit.get("title", ""),
            "authors": [],
            "year": year,
            "journal": "",
            "doi": "",
            "url": hit.get("url", ""),
            "source": source,
        }
    return {}


async def create_paper(self, fields: dict) -> dict:
    """The one write path every "add a paper" surface funnels through —
    used by `api_add_paper` (import modal, post-preview) and by the
    `add_paper` verb (agent/MCP/assistant)."""
    title = (fields.get("title") or "").strip()
    if not title:
        return {"error": "title required"}

    # Same guard `api_update`/`set_field` apply post-creation — a malformed
    # url/rating shouldn't be able to sneak in via the (more common) create
    # path just because it skips the update endpoint.
    err = self._validate_paper_fields(fields)
    if err:
        return {"error": err}

    authors = normalize_str_list(fields.get("authors"))
    year = str(fields.get("year") or "")

    taken = {
        p.get("citekey") or (p.get("file") or "").removesuffix(".md")
        for p in self.papers.list()
    }
    raw_citekey = (fields.get("citekey") or "").strip()
    if raw_citekey:
        err = path_segment_error(raw_citekey, "citekey")
        if err:
            return {"error": err}
        citekey = raw_citekey
    else:
        citekey = citekey_for(authors, year, title, taken=taken)

    try:
        rating = float(fields.get("rating") or 0)
    except (TypeError, ValueError):
        rating = 0.0

    fm = {
        "tags": [PAPER_TAG],
        "title": title,
        "authors": authors,
        "year": year,
        "journal": fields.get("journal", ""),
        "venue": fields.get("venue", ""),
        "doi": fields.get("doi", ""),
        "url": fields.get("url", ""),
        "abstract": fields.get("abstract", ""),
        "citekey": citekey,
        "status": fields.get("status") or "to-read",
        "rating": rating,
        "topics": normalize_str_list(fields.get("topics")),  # comma/list-tolerant, same coercion
        "local_pdf": "",
        "source": fields.get("source") or "manual",
        "related": fields.get("related") or [],
    }
    body = (fields.get("abstract") or "").strip()
    self.vault_create_note(f"{self.papers_dir()}/{citekey}.md", fm, body)
    await self.emit("library:paper_added", {"citekey": citekey, "title": title})
    return {"ok": True, "citekey": citekey, "file": f"{citekey}.md"}


@web_route("POST", "/api/import/preview")
async def api_import_preview(self, request):
    """POST /api/import/preview {doi?, url?, title?} — resolves candidate
    metadata for the user to review/edit before Save. The form IS the
    preview (`.claude/rules/proposed-action.md` "form-driven creates") —
    no formal propose/apply gate needed for a simple create."""
    body = await request.json()
    meta = await self.resolve_metadata(
        doi=body.get("doi", ""), url=body.get("url", ""), title=body.get("title", ""),
    )
    return {"metadata": meta}


@web_route("POST", "/api/papers")
async def api_add_paper(self, request):
    body = await request.json()
    return await self.create_paper(body)


async def add_paper(self, *, doi: str = "", url: str = "", title: str = "") -> dict:
    """`library.add_paper` verb — resolve metadata (if any of doi/url/title
    given) and create the note. Falls back to a title-only stub when
    nothing resolves, so an agent/voice caller always gets a paper it can
    then flesh out, rather than a bare failure."""
    meta = await self.resolve_metadata(doi=doi, url=url, title=title)
    if not meta:
        if not title:
            return {"error": "could not resolve metadata — provide at least a title"}
        meta = {"title": title, "url": url, "doi": doi, "source": "manual"}
    return await self.create_paper(meta)


async def search(self, query: str = "") -> list[dict]:
    """`library.search` verb — keyword search across stored paper metadata
    (title/authors/abstract/journal/topics). Full-text-inside-PDF search is
    the separate `api_search_fulltext` route in `storage.py`."""
    return self.papers.search(query)


async def voice_search(self, query: str = "") -> dict:
    hits = await self.search(query)
    if not hits:
        return {"say": f"No papers found for {query}." if query else "What should I search for?"}
    top = hits[:5]
    return {
        "say": f"Found {len(hits)} paper{'s' if len(hits) != 1 else ''}.",
        "card": {
            "renderer": "task-list",
            "data": [{"text": h.get("title", h.get("citekey", "")), "tag": h.get("year", "")} for h in top],
        },
    }
