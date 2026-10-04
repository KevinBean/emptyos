"""Library — citation/bibliography export (BibTeX + CSL-JSON).

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: the export route. The actual serialization functions
(`to_bibtex_entry`, `to_csl_json`, `citekey_for`) are pure and live in
`.shared` (no `self`, no I/O) — this module is the thin HTTP-facing wrapper
around them.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``.shared`` (pure helpers only).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from emptyos.sdk import web_route

from .shared import to_bibtex_entry, to_csl_json

if TYPE_CHECKING:
    from .app import LibraryApp  # noqa: F401 — for type hints only


# ─── Bind to LibraryApp class as ──────────────────────────────────────────
#   api_export = _bibtex.api_export
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────────


@web_route("GET", "/api/export")
async def api_export(self, request):
    """GET /api/export?format=bibtex|csl-json&topic=...&citekey=... — a
    downloadable bibliography. `topic` filters to papers carrying that
    `topics:` entry; `citekey` narrows to exactly one paper (the detail
    page's "Copy citation" affordance). Omitting both exports the whole
    library. RIS export is the same shape again — add on request, not built
    for v1."""
    from starlette.responses import PlainTextResponse, JSONResponse

    fmt = (request.query_params.get("format")
           or self.setting_or_config("library.export_format", "bibtex",
                                     config_key="export_format")).strip().lower()
    topic = (request.query_params.get("topic") or "").strip()
    citekey = (request.query_params.get("citekey") or "").strip()

    papers = self.papers.list()
    if topic:
        papers = [p for p in papers if topic in (p.get("topics") or [])]
    if citekey:
        papers = [p for p in papers if (p.get("citekey") or (p.get("file") or "").removesuffix(".md")) == citekey]

    if fmt == "csl-json":
        body = json.dumps([to_csl_json(p) for p in papers], indent=2, ensure_ascii=False)
        return PlainTextResponse(
            body, media_type="application/vnd.citationstyles.csl+json",
            headers={"Content-Disposition": 'attachment; filename="library.json"'},
        )
    if fmt == "bibtex":
        body = "\n\n".join(to_bibtex_entry(p) for p in papers) + "\n"
        return PlainTextResponse(
            body, media_type="application/x-bibtex",
            headers={"Content-Disposition": 'attachment; filename="library.bib"'},
        )
    return JSONResponse({"error": f"unknown format: {fmt!r} (want bibtex or csl-json)"}, status_code=400)
