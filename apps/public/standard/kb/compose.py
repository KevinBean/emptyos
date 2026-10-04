"""kb — composed-document reader backend.

A ``kind: reference`` standard is read as a **composition of its atomic clause
notes** (one per §X.Y), in clause order — the EmptyOS "everything is a note,
value is in the connections" model. This replaces the flat-`.txt`-slice reader:
the document *emerges from* the layered notes, and the raw archive is just a
source link. Each composed section carries its own slug, so the reader links to
the standalone clause note.

  ``GET /api/notes/{slug}/compose``                      → the outline (clause
       notes by chapter, no bodies — light; feeds the doc-nav + on-this-page).
  ``GET /api/notes/{slug}/compose/chapter?chapter=N``    → that chapter's clause
       notes WITH bodies (lazy markdown), each carrying its slug.

Gated behind ``[apps.kb] feature.composed-document.enabled`` (dark default).
Bound onto ``KBApp``. Reaches ``_note_by_slug`` (fulltext.py), ``_clauses_for_standard``
(indexes.py), ``_slug_to_path`` (docs.py), ``vault_read_body`` (BaseApp). Do not
import from ``.app``.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import web_route
from emptyos.sdk.doc_slice import parse_contents
from emptyos.sdk.standard_atomize import plan_atomization

from .shared import SOURCE_TAG

if TYPE_CHECKING:
    from .app import KBApp  # noqa: F401 — for type hints only


# ─── Bind to KBApp class as ────────────────────────────────
#   _composed_enabled    = _compose._composed_enabled
#   _chapter_titles      = _compose._chapter_titles
#   _compose_clauses     = _compose._compose_clauses
#   api_compose_index    = _compose.api_compose_index
#   api_compose_chapter  = _compose.api_compose_chapter
#   atomize_standard     = _compose.atomize_standard
#   api_atomize_standard = _compose.api_atomize_standard
#   _write_clause_specs  = _compose._write_clause_specs
# Adding a method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────


def _composed_enabled(self) -> bool:
    """Dark-ship gate (default off → routes return ``{error}``)."""
    return bool(self.app_config("feature.composed-document.enabled", False))


def _chapter_titles(self, props: dict) -> dict[str, str]:
    """Chapter-number → title map from a reference's archive TOC.

    NOT gated by ``feature.composed-document.enabled`` — only the compose
    *routes* are dark; the title derivation is free to reuse. Consumed by
    ``api_compose_index`` (the composed reader) AND ``notes.get_note`` (the
    always-on reference-landing TOC rail). Returns ``{}`` when the reference has
    no readable archive → callers fall back to bare ``Chapter N``.
    """
    titles: dict[str, str] = {}
    txt = self._read_fulltext_for(props or {})
    if txt:
        for s in parse_contents(txt):
            if s.get("level") == 1:
                titles[str(s.get("chapter"))] = s.get("title") or ""
    return titles


def _is_atomic_clause(no: str) -> bool:
    """True for a single §X.Y(.Z) clause — not a range ('1.8-1.22') or bare chapter ('5')."""
    return bool(re.match(r"^\d+\.\d+(?:\.\d+)*$", str(no or "").strip()))


def _compose_clauses(self, reference_slug: str):
    """Ordered atomic clause rows for the reference's current edition.

    Returns ``(rows, props)`` where rows = ``[{slug, clause, clause_title,
    edition, title, …}]`` (from ``_clauses_for_standard``, already clause-sorted),
    filtered to this edition + single atomic clauses. ``(None, props|None)`` when
    the slug isn't a reference note with a ``standard_id``.
    """
    ref = self._note_by_slug(reference_slug)
    if not ref:
        return None, None
    props = ref.get("properties", {}) or {}
    if props.get("kind") != "reference" or not props.get("standard_id"):
        return None, props
    edition = str(props.get("edition") or "").strip()
    rows = []
    for r in self._clauses_for_standard(
        str(props["standard_id"]),
        str(props.get("standard") or ""),
        str(props.get("edition") or ""),
    ):
        if edition and str(r.get("edition") or "").strip() != edition:
            continue
        if not _is_atomic_clause(r.get("clause")):
            continue
        rows.append(r)
    return rows, props


def _contents_for(rows: list[dict]) -> list[dict]:
    return [{
        "no": str(r.get("clause") or ""),
        "title": r.get("clause_title") or str(r.get("clause") or ""),
        "slug": r.get("slug"),
        "chapter": str(r.get("clause") or "").split(".")[0],
        "level": 2,
    } for r in rows]


@web_route("GET", "/api/notes/{slug}/compose")
async def api_compose_index(self, request):
    """Document outline: the standard's atomic clause notes, by chapter (no bodies)."""
    if not self._composed_enabled():
        return {"error": "composed document disabled"}
    slug = request.path_params.get("slug", "")
    rows, props = self._compose_clauses(slug)
    if rows is None:
        return {"error": "not a reference note / not found", "has_clauses": False}
    contents = _contents_for(rows)
    # Chapter titles come from the archive's own TOC (clause notes don't carry them).
    titles = self._chapter_titles(props or {})
    chs = sorted({c["chapter"] for c in contents}, key=lambda x: int(x) if str(x).isdigit() else 999)
    chapters = [{"no": ch, "title": titles.get(ch) or ("Chapter " + ch)} for ch in chs]
    return {
        "ok": True, "slug": slug, "has_clauses": bool(contents),
        "edition": (props or {}).get("edition"),
        "title": (props or {}).get("title") or slug.replace("-", " "),
        "archive": (props or {}).get("local_text") or (props or {}).get("source_file") or "",
        "contents": contents, "chapters": chapters, "section_count": len(contents),
    }


@web_route("GET", "/api/notes/{slug}/compose/chapter")
async def api_compose_chapter(self, request):
    """One chapter's atomic clause notes WITH bodies (lazy markdown)."""
    if not self._composed_enabled():
        return {"error": "composed document disabled"}
    slug = request.path_params.get("slug", "")
    chapter = (request.query_params.get("chapter") or "").strip()
    rows, _props = self._compose_clauses(slug)
    if rows is None:
        return {"error": "not a reference note / not found"}
    idx = self._slug_to_path()
    sections = []
    for r in rows:
        ch = str(r.get("clause") or "").split(".")[0]
        if chapter and ch != chapter:
            continue
        path = idx.get(r.get("slug"))
        body = self.vault_read_body(path) if path else ""
        sections.append({
            "slug": r.get("slug"), "clause": r.get("clause"),
            "clause_title": r.get("clause_title") or "", "chapter": ch, "body": body,
        })
    return {"ok": True, "chapter": chapter, "sections": sections}


async def atomize_standard(self, reference_slug: str, *, dry_run: bool = False) -> dict:
    """Generate one atomic ``clause`` note per §X.Y section of a reference's archive.

    Reusable, idempotent (skips a clause that already has a note for this
    edition), generic for any reference with a stored ``local_text``/``source_file``
    archive. The standalone ``scripts/atomize_standard.py`` shares the same pure
    planner; this is the daemon-side path (writes via ``vault_create_note``).
    """
    ref = self._note_by_slug(reference_slug)
    if not ref:
        return {"error": "reference not found", "slug": reference_slug}
    props = ref.get("properties", {}) or {}
    if props.get("kind") != "reference" or not props.get("standard_id"):
        return {"error": "not a reference note with a standard_id"}
    txt = self._read_fulltext_for(props)  # cleaned verbatim archive
    if not txt:
        return {"error": "reference has no readable archive (local_text/source_file)"}
    edition = str(props.get("edition") or "").strip()
    existing = [
        str(r.get("clause")) for r in self._clauses_for_standard(
            str(props["standard_id"]),
            str(props.get("standard") or ""),
            str(props.get("edition") or ""),
        )
        if str(r.get("edition") or "").strip() == edition and r.get("clause")
    ]
    vlevels = props.get("voltage_levels") if isinstance(props.get("voltage_levels"), list) else []
    # A project-scoped source (client spec, `project:` set) keeps its clauses
    # beside itself under the project, never in the global kb/sources pile;
    # every clause inherits the scope so the index files it with the project.
    project = str(props.get("project") or "").strip()
    sources_dir = str(Path(ref.get("path", "")).parent).replace("\\", "/") if project else \
        "30_Resources/EmptyOS/kb/sources"
    extra: dict = {"voltage_levels": vlevels, "project": project}
    if project:
        # Same tag and the same `standard` key as the reference, or the citation
        # index files the clauses under a different standard than the document.
        extra.update({"tags": [SOURCE_TAG], "standard": str(props.get("standard") or props["standard_id"])})
    specs = plan_atomization(
        txt,
        reference_slug=reference_slug,
        reference_title=props.get("title", reference_slug),
        standard_id=str(props["standard_id"]),
        edition=edition,
        domain=props.get("domain", ""),
        topic=props.get("topic", ""),
        source_file=props.get("local_text") or props.get("source_file") or "",
        existing_clauses=existing,
        extra_frontmatter=extra,
        sources_dir=sources_dir,
        today=datetime.now().date().isoformat(),
    )
    created, skipped = self._write_clause_specs(specs, dry_run=dry_run)
    if created and not dry_run:
        await self.emit("kb:standard_atomized", {"slug": reference_slug, "created": len(created)})
    return {"ok": True, "reference": reference_slug, "dry_run": dry_run,
            "created": len(created), "skipped": len(skipped), "slugs": created}


def _write_clause_specs(self, specs, *, dry_run: bool) -> tuple[list[str], list[str]]:
    """Persist planned clause notes → ``(created_slugs, skipped_clauses)``.

    Shared by ``atomize_standard`` (reference already in the vault) and
    ``sources.ingest_source`` (reference written in the same call — the index
    may not have seen it yet, so the specs are planned up front and written
    here rather than re-read through ``_note_by_slug``).
    """
    created, skipped = [], []
    for s in specs:
        if (self.vault_root / s.rel).exists():
            skipped.append(s.clause)
            continue
        if not dry_run:
            self.vault_create_note(s.rel, s.frontmatter, s.body)
        created.append(s.slug)
    return created, skipped


@web_route("POST", "/api/standards/{slug}/atomize")
async def api_atomize_standard(self, request):
    """Atomize a reference's archive into clause notes. Body: ``{dry_run?: bool}``."""
    if not self._composed_enabled():
        return {"error": "composed document disabled"}
    slug = request.path_params.get("slug", "")
    body = await self.read_json(request)
    return await self.atomize_standard(slug, dry_run=bool(body.get("dry_run")))
