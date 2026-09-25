"""Library — reference manager: papers and PDFs with citation metadata,
full-text search, annotation, and BibTeX/CSL export.

KB's `reference`/`clause` note kinds already cover engineering standards
(IEC/IEEE-style numbered citations) — this app owns a *separate* citation
grammar (author/year/DOI) for a general paper library, and bridges into KB
via an explicit action (`api_send_to_kb`) rather than folding the two
corpora together. See `apps/public/standard/library/INTENT.md`.
"""

from __future__ import annotations

import logging
import re

from emptyos.sdk import BaseApp, VaultLibrary, cli_command, web_route
from emptyos.sdk.utils import path_segment_error

from . import bibtex as _bibtex
from . import highlights as _highlights
from . import metadata as _metadata
from . import storage as _storage
from .shared import DEFAULT_PAPERS_DIR, PAPER_TAG

log = logging.getLogger("emptyos.library")

# `url` becomes an `<a href>` and `rating` an attribute value in the UI
# (pages/index.html) — a non-http(s) scheme or a non-numeric rating would
# either render a javascript: link or break attribute escaping.
_SAFE_URL_SCHEME = re.compile(r"^https?://", re.IGNORECASE)


class ReferenceLibrary(VaultLibrary):
    tag = PAPER_TAG
    fields = {
        "title": str, "authors": list, "year": str,
        "journal": str, "venue": str, "doi": str, "url": str,
        "abstract": str, "citekey": str, "status": str,
        "rating": float, "topics": list,
        "local_pdf": str, "source": str, "related": list,
    }
    # `topics` (not `tags`) deliberately — VaultIndex pops the note's own
    # `tags:` frontmatter (the type tag "paper") out into a separate field
    # at index time, so a VaultLibrary field literally named `tags` never
    # reads back via the index path (only the directory-scan fallback sees
    # it). `topics` sidesteps the collision entirely.
    aliases = {"authors": ["author"]}
    sort_key = "created"
    sort_reverse = True
    search_fields = ["title", "authors", "abstract", "journal", "topics"]

    @property
    def fallback_folder(self) -> str:
        """Resolved per call, so reads always land where writes go.

        The base class declares this as a plain string, which would freeze
        the literal default while `papers_dir()` resolved something else —
        splitting the library in half (writes to the configured folder,
        directory-scan reads from the old one). A property rather than a
        lifecycle assignment because it then cannot drift: there is no hook
        to forget, and no ordering to get wrong.
        """
        return self.app.papers_dir()


class LibraryApp(BaseApp):
    # Boards-as-view-layer contract (.claude/rules/boards-as-view-layer.md) —
    # gives a free kanban-by-status board with no further code.
    SETTABLE_FIELDS = {"status", "rating", "topics"}

    def __init__(self, kernel, manifest):
        super().__init__(kernel, manifest)
        self.papers = ReferenceLibrary(self)

    def papers_dir(self) -> str:
        """Vault-relative folder holding paper notes + their attachments.

        Dev Rule 8 — resolved through the vault map (and any settings
        override) rather than hardcoded, so a machine can relocate the
        corpus without a code edit. Returns a relative path because every
        caller composes it into a vault-relative string.
        """
        return self.vault_config("papers_dir", DEFAULT_PAPERS_DIR)

    async def on_start(self):
        # NOTE: dead hook — the loader calls `setup()` (app_loader.py), never
        # `on_start`. Left as-is (11 apps define one) but nothing may depend
        # on it; ReferenceLibrary.fallback_folder resolves lazily instead.
        log.info("library started")

    # ── metadata.py — autofill + create + verbs ──────────────────────
    resolve_metadata = _metadata.resolve_metadata
    create_paper = _metadata.create_paper
    api_import_preview = _metadata.api_import_preview
    api_add_paper = _metadata.api_add_paper
    add_paper = _metadata.add_paper            # verb: library.add_paper
    search = _metadata.search                  # verb: library.search
    voice_search = _metadata.voice_search       # voice intent

    # ── storage.py — PDF binary + serve + full-text ──────────────────
    write_pdf_binary = _storage.write_pdf_binary
    fulltext_path = _storage.fulltext_path
    extract_and_store_fulltext = _storage.extract_and_store_fulltext
    api_pdf_serve = _storage.api_pdf_serve
    api_upload_pdf = _storage.api_upload_pdf
    api_import_pdf_scan = _storage.api_import_pdf_scan
    api_search_fulltext = _storage.api_search_fulltext

    # ── highlights.py — annotation + SRS resurfacing ──────────────────
    api_add_highlight = _highlights.api_add_highlight
    api_review_due = _highlights.api_review_due
    api_review_grade = _highlights.api_review_grade
    api_review_stats = _highlights.api_review_stats
    proactive_source_reviews = _highlights.proactive_source_reviews
    _reviews_path = _highlights._reviews_path
    _load_reviews = _highlights._load_reviews
    _save_reviews = _highlights._save_reviews

    # ── bibtex.py — citation export ───────────────────────────────────
    api_export = _bibtex.api_export

    # ── CRUD (spine — genuinely the app's public surface) ─────────────

    @cli_command("list")
    async def cli_list(self):
        for p in self.papers.list():
            print(f"{p.get('citekey', p.get('file', ''))}\t{p.get('title', '')}")

    @web_route("GET", "/api/papers")
    async def api_list(self, request):
        status = request.query_params.get("status", "")
        topic = request.query_params.get("topic", "")
        q = request.query_params.get("q", "")
        items = self.papers.search(q) if q else self.papers.list()
        if status:
            items = [i for i in items if i.get("status") == status]
        if topic:
            items = [i for i in items if topic in (i.get("topics") or [])]
        return items

    @web_route("GET", "/api/papers/{citekey}")
    async def api_detail(self, request):
        citekey = request.path_params.get("citekey", "")
        err = path_segment_error(citekey, "paper id")
        if err:
            return {"error": err}
        item = self.papers.detail(f"{citekey}.md")
        if not item:
            return {"error": "not found"}
        return item

    @web_route("PUT", "/api/papers/{citekey}")
    async def api_update(self, request):
        citekey = request.path_params.get("citekey", "")
        err = path_segment_error(citekey, "paper id")
        if err:
            return {"error": err}
        data = await request.json()
        err = self._validate_paper_fields(data)
        if err:
            return {"error": err}
        result = self.papers.update(f"{citekey}.md", data)
        if result.get("ok"):
            await self.emit("library:paper_updated", {"citekey": citekey, "updates": list(data.keys())})
        return result

    @web_route("DELETE", "/api/papers/{citekey}")
    async def api_delete(self, request):
        citekey = request.path_params.get("citekey", "")
        err = path_segment_error(citekey, "paper id")
        if err:
            return {"error": err}
        item = self.papers.detail(f"{citekey}.md")
        path = self.papers.find_file(f"{citekey}.md")
        if not path:
            return {"error": "not found"}
        path.unlink()
        self.papers._poke_index(path)
        # Clean up the attached PDF (if any) and the derived fulltext cache
        # so a deleted paper doesn't leave orphaned vault/data artifacts.
        local_pdf = (item or {}).get("local_pdf") or ""
        if local_pdf:
            pdf_full = self.vault_root / local_pdf
            if pdf_full.exists():
                pdf_full.unlink()
        fulltext_txt = self.fulltext_path(citekey)
        if fulltext_txt.exists():
            fulltext_txt.unlink()
        await self.emit("library:paper_deleted", {"citekey": citekey})
        return {"ok": True}

    @web_route("POST", "/api/papers/{citekey}/send-to-kb")
    async def api_send_to_kb(self, request):
        """One call, no new mechanism — `BaseApp.propose_kb_note` already
        exists exactly for this (its own docstring names reader/capture/
        conversation-digest/media as producers; library is another). Lands
        a pending action in the review-gate dashboard; the user applies it
        like any other proposed KB note. v1 is one-way (library → proposed
        KB note) — see INTENT.md § Future for the two-way backlink idea."""
        citekey = request.path_params.get("citekey", "")
        err = path_segment_error(citekey, "paper id")
        if err:
            return {"error": err}
        paper = self.papers.detail(f"{citekey}.md")
        if not paper:
            return {"error": "not found"}
        result = await self.propose_kb_note(
            kind="reference",
            title=paper.get("title", citekey),
            body=paper.get("abstract", ""),
            source=paper.get("doi") or paper.get("url") or "",
            author="ai",
            related=[citekey],
        )
        await self.emit("library:sent_to_kb", {"citekey": citekey})
        return result

    # ── Boards-as-view-layer contract ─────────────────────────────────

    async def list_all(self) -> list[dict]:
        return [
            {
                "id": p.get("citekey") or p.get("file", ""),
                "title": p.get("title", ""),
                "authors": ", ".join(p.get("authors") or []),
                "year": p.get("year", ""),
                "journal": p.get("journal", ""),
                "status": p.get("status", "to-read"),
                "rating": p.get("rating", 0),
                "topics": p.get("topics", []),
            }
            for p in self.papers.list()
        ]

    async def set_field(self, id: str, field: str, value) -> dict:
        if field not in self.SETTABLE_FIELDS:
            return {"error": f"field '{field}' not settable"}
        err = self._validate_paper_fields({field: value})
        if err:
            return {"error": err}
        result = self.papers.update(f"{id}.md", {field: value})
        if result.get("ok"):
            await self.emit("library:paper_updated", {"citekey": id, "field": field, "value": value})
        return result

    @staticmethod
    def _validate_paper_fields(data: dict) -> str | None:
        """Reject unsafe/out-of-range `url`/`rating` values before they hit
        frontmatter — see `_SAFE_URL_SCHEME` for why."""
        if "url" in data:
            url = data["url"] or ""
            if url and not _SAFE_URL_SCHEME.match(url):
                return "url must start with http:// or https://"
        if "rating" in data:
            try:
                rating = float(data["rating"])
            except (TypeError, ValueError):
                return "rating must be a number"
            if not (0 <= rating <= 5):
                return "rating must be between 0 and 5"
        return None
