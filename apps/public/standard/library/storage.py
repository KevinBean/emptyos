"""Library — PDF binary storage, serving, and full-text extraction.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: writing a PDF into the vault under `Library/attachments/`,
serving it back out (mirrors `apps/public/standard/learn/app.py`'s
`api_pdf_serve` route verbatim), and extracting + storing its full text for
keyword search.

There is no existing binary-vault-write primitive in the SDK — the only
precedent (`BaseApp.download_drawn_image`) writes raw bytes outside both the
`write` capability and the atomic-write discipline. This module doesn't
repeat that gap: writes go through `atomic_write_bytes`
(`.claude/rules/atomic-persistence.md`). If a third app needs the same
primitive, that's the CLAUDE.md rule-9 trigger to extract
`BaseApp.write_binary()` — not before.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``.shared`` (pure helpers only).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import atomic_write_bytes, web_route
from emptyos.sdk.pdf import extract_pdf_text
from emptyos.sdk.utils import contained_path, path_segment_error

from .shared import ATTACHMENTS_DIR, find_doi

if TYPE_CHECKING:
    from .app import LibraryApp  # noqa: F401 — for type hints only


# ─── Bind to LibraryApp class as ──────────────────────────────────────────
#   api_pdf_serve              = _storage.api_pdf_serve
#   api_upload_pdf             = _storage.api_upload_pdf
#   api_import_pdf_scan        = _storage.api_import_pdf_scan
#   api_search_fulltext        = _storage.api_search_fulltext
#   write_pdf_binary           = _storage.write_pdf_binary
#   fulltext_path              = _storage.fulltext_path
#   extract_and_store_fulltext = _storage.extract_and_store_fulltext
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────────


def write_pdf_binary(self, rel_path: str, data: bytes) -> Path:
    """Write `data` to `<vault_root>/<rel_path>` atomically, refusing to
    escape the vault root. `rel_path` is server-generated (citekey-derived),
    never taken verbatim from a caller — the containment check is defence
    in depth, not the only guard."""
    vault_root = self.vault_root.resolve()
    target = vault_root / rel_path
    inside = contained_path(vault_root, target)
    if inside is None:
        raise ValueError(f"refusing to write outside vault root: {rel_path}")
    atomic_write_bytes(target.resolve(), data)
    return target


def fulltext_path(self, citekey: str) -> Path:
    return self.kernel.config.data_dir / "apps" / "library" / "fulltext" / f"{citekey}.txt"


async def extract_and_store_fulltext(self, citekey: str, pdf_path: Path) -> str:
    """Extract a PDF's text and cache it under `data/apps/library/fulltext/`
    (a regenerable derived artifact, not authored content — it does not
    belong in the vault). Returns the extracted text (possibly empty on a
    scanned/image-only PDF; OCR is out of scope for v1 — the `ocr` plugin
    covers that path for other apps and can be wired in later)."""
    import asyncio

    try:
        text = await asyncio.to_thread(extract_pdf_text, pdf_path)
    except Exception:
        return ""
    if text:
        path = self.fulltext_path(citekey)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return text


@web_route("GET", "/api/pdf/{vault_rel_path:path}")
async def api_pdf_serve(self, request):
    """Serve a PDF binary from the vault. Path-traversal-safe, .pdf-only —
    mirrors `apps/public/standard/learn/app.py`'s `api_pdf_serve` verbatim."""
    from starlette.responses import FileResponse, JSONResponse

    rel = (request.path_params.get("vault_rel_path") or "").strip()
    if not rel:
        return JSONResponse({"error": "path required"}, status_code=400)
    if not rel.lower().endswith(".pdf"):
        return JSONResponse({"error": "only .pdf paths served"}, status_code=400)
    vault_root = self.vault_root.resolve()
    candidate = contained_path(vault_root, (vault_root / rel))
    if candidate is None:
        return JSONResponse({"error": "invalid path"}, status_code=403)
    candidate = candidate.resolve()
    if not candidate.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(str(candidate), media_type="application/pdf", filename=candidate.name)


@web_route("POST", "/api/papers/{citekey}/pdf")
async def api_upload_pdf(self, request):
    """POST /api/papers/{citekey}/pdf — multipart PDF upload for an existing
    paper. Stores the file, sets `local_pdf:`, extracts full text for
    search."""
    citekey = request.path_params.get("citekey", "")
    err = path_segment_error(citekey, "paper id")
    if err:
        return {"error": err}
    item = self.papers.detail(f"{citekey}.md")
    if not item:
        return {"error": "not found"}

    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        return {"error": "file required"}
    data = await upload.read()
    if not data:
        return {"error": "empty file"}

    rel_path = f"{self.papers_dir()}/{ATTACHMENTS_DIR}/{citekey}.pdf"
    self.write_pdf_binary(rel_path, data)
    self.papers.update(f"{citekey}.md", {"local_pdf": rel_path})
    await self.extract_and_store_fulltext(citekey, self.vault_root / rel_path)
    await self.emit("library:paper_updated", {"citekey": citekey, "field": "local_pdf"})
    return {"ok": True, "local_pdf": rel_path}


@web_route("POST", "/api/import/pdf-scan")
async def api_import_pdf_scan(self, request):
    """POST /api/import/pdf-scan — multipart PDF upload with no paper
    created yet. Extracts text, sniffs a DOI on the first ~page, resolves
    metadata via CrossRef (`self.resolve_metadata`, bound from
    `metadata.py` — reached via `self.X`, not a cross-module import; see
    `.claude/rules/multi-module-apps.md` rule 4). Returns candidate
    metadata for review; the frontend keeps the same File object and
    re-POSTs it to `api_upload_pdf` once the user confirms via
    `api_add_paper` — no server-side temp storage needed."""
    import asyncio
    import io

    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        return {"error": "file required"}
    data = await upload.read()
    if not data:
        return {"error": "empty file"}

    try:
        text = await asyncio.to_thread(extract_pdf_text, io.BytesIO(data))
    except Exception:
        text = ""
    doi = find_doi(text[:4000])
    meta = await self.resolve_metadata(doi=doi) if doi else {}
    return {"metadata": meta, "doi_found": doi}


@web_route("GET", "/api/search/fulltext")
async def api_search_fulltext(self, request):
    """GET /api/search/fulltext?q=... — plain keyword scan across every
    extracted-text cache. No DuckDB/embeddings for v1
    (`.claude/rules/text-first-data.md` reserves those for joins/aggregates,
    not a flat keyword grep over N files); semantic search over the same
    corpus via `emptyos/sdk/embeddings.py` is a clean v2 upgrade."""
    q = (request.query_params.get("q") or "").strip().lower()
    if not q:
        return {"results": []}

    by_citekey = {}
    for p in self.papers.list():
        ck = p.get("citekey") or (p.get("file") or "").removesuffix(".md")
        if ck:
            by_citekey[ck] = p

    results = []
    fulltext_dir = self.kernel.config.data_dir / "apps" / "library" / "fulltext"
    if fulltext_dir.exists():
        for f in fulltext_dir.glob("*.txt"):
            citekey = f.stem
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            idx = text.lower().find(q)
            if idx < 0:
                continue
            start = max(0, idx - 80)
            snippet = text[start:idx + len(q) + 80].strip()
            paper = by_citekey.get(citekey, {})
            results.append({
                "citekey": citekey,
                "title": paper.get("title", citekey),
                "snippet": snippet,
            })
    return {"results": results[:30]}
