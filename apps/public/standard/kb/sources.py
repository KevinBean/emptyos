"""kb — source documents: a project's own standards and client specifications.

Extracted as its own helper module (multi-module pattern, CLAUDE.md rule 4).
Owns: registering a document a client supplied (a specification, a scope, a
licensed standard) as a **project-scoped** ``reference`` note plus one ``clause``
note per numbered section — the same shape the global KB uses for a published
standard, so the citation index, the fulltext reader and the requirements app's
``references:`` resolution all work unchanged. The scope is metadata
(``project: <id>`` in frontmatter, found by tag); the folder
``10_Projects/<project>/docs/sources/`` is only the default creation location.

Why project-scoped and not the global ``kb/sources/`` pile: the KB is global
reusable knowledge; a client's document is confidential to one job. A client
clause must never answer a global citation, never appear in the default KB list
and never be published — so these notes carry ``tags: [kb-source]``
(``shared.SOURCE_TAG``), not ``kb``: every ``vault_query(tags=["kb"])`` consumer
inside and outside this app is blind to them by construction, and only the
slug-addressed lookups and the citation index (``_all_notes(include_project=
True)``) read both tags. ``indexes._build_ref_index`` files them under the
project.

Nothing here calls an LLM. Extraction of requirements from these clauses is the
requirements app's job; this module only makes the text citable.

Reaches into other modules: ``self._all_notes`` (notes), ``self._note_by_slug``
(fulltext), ``self._write_clause_specs`` (compose), ``self._build_ref_index``
(indexes). Do not import from ``.app`` (it imports us).
"""

from __future__ import annotations

import asyncio
import io
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import web_route
from emptyos.sdk.doc_slice import parse_sections
from emptyos.sdk.standard_atomize import plan_atomization
from emptyos.sdk.utils import contained_path, safe_path_segment, slugify

from .shared import SOURCE_TAG, _clause_sort_key, _slug_of

if TYPE_CHECKING:
    from .app import KBApp  # noqa: F401 — for type hints only


# ─── Bind to KBApp class as ────────────────────────────────
#   list_sources        = _sources.list_sources
#   source_clauses      = _sources.source_clauses
#   ingest_source       = _sources.ingest_source
#   api_sources         = _sources.api_sources
#   api_source_clauses  = _sources.api_source_clauses
#   api_ingest_source   = _sources.api_ingest_source
# Adding a method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────

# A client specification is never 20 MB of text; a scanned drawing set is not
# what this path is for (the ocr plugin serves those through `read`).
MAX_SOURCE_BYTES = 20 * 1024 * 1024
SOURCE_KIND = "client-document"
_TEXT_SUFFIXES = {".md", ".txt", ".markdown"}


def _source_dir(self, project: str) -> str:
    return f"{self.vault_projects_root()}/{safe_path_segment(project)}/docs/sources"


def _pdf_pages_text(data: bytes) -> tuple[str, int]:
    """Born-digital PDF bytes → page-marked text (``<!-- Page N of M -->``), the
    canonical fulltext layout ``doc_slice`` and the fulltext reader understand.

    Raises ImportError when pypdf is absent (surfaced in-band by the caller).
    """
    from pypdf import PdfReader  # type: ignore

    reader = PdfReader(io.BytesIO(data))
    total = len(reader.pages)
    parts: list[str] = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001 — one bad page must not sink the document
            text = ""
        parts.append(f"<!-- Page {i} of {total} -->\n{text.strip()}\n")
    return "\n".join(parts), total


def _vault_abs(self, rel: str) -> Path:
    """Absolute path for a vault-relative path, refusing anything that escapes."""
    p = contained_path(self.vault_root, self.vault_root / str(rel))
    if p is None:
        raise ValueError("path escapes the vault")
    return p


async def _text_from_bytes(self, rel: str, data: bytes) -> tuple[str, str]:
    """``(text, error)`` for a document already stored at vault-relative ``rel``.

    PDF → page-marked text via pypdf; ``.md``/``.txt`` → decoded as-is; any
    other format (DOCX, DOC, RTF, …) goes through the ``read`` capability, so
    whichever converter plugin the machine has (markitdown, legacy-doc, ocr)
    does the work.
    """
    suffix = Path(rel).suffix.lower()
    if suffix == ".pdf":
        try:
            text, _pages = await asyncio.to_thread(_pdf_pages_text, data)
        except ImportError:
            return "", "pypdf is not installed — cannot read a PDF"
        except Exception as e:  # noqa: BLE001 — a corrupt PDF is a user error, not a 500
            return "", f"could not read the PDF: {e}"
        return text, ""
    if suffix in _TEXT_SUFFIXES:
        return data.decode("utf-8", errors="replace"), ""
    try:
        text = await self.read(rel)
    except Exception as e:  # noqa: BLE001 — no converter for this format on this machine
        return "", f"no reader for {suffix or 'this file'}: {e}"
    if not isinstance(text, str) or not text.strip():
        return "", f"the {suffix or 'file'} reader returned no text"
    return text, ""


def _asset_name(filename: str) -> str:
    """A vault-safe file name that KEEPS the extension: ``"Client Spec (final).pdf"``
    → ``client-spec-final.pdf``. (``safe_path_segment`` is a rejecting guard
    and would turn that name into ``""``, losing the ``.pdf`` the reader keys on.)"""
    p = Path(filename or "document")
    stem = slugify(p.stem, max_len=80) or "document"
    suffix = p.suffix.lower() if re.fullmatch(r"\.[a-z0-9]{1,8}", p.suffix.lower() or "") else ""
    return stem + suffix


async def _store_upload(self, project: str, filename: str, data: bytes) -> tuple[str, str]:
    """Keep the client's original under the project's ``assets/`` → ``(rel, error)``.

    Bytes, not text, so this is direct file I/O rather than the ``write``
    capability (which carries ``str`` only) — the same shape the library app
    uses for a PDF, confined to the vault by ``_vault_abs``. A name already
    taken by DIFFERENT bytes gets a numeric suffix; identical bytes reuse the
    file, so re-registering the same document never duplicates or clobbers.
    """
    name = _asset_name(filename)
    base_rel = f"{self.vault_projects_root()}/{safe_path_segment(project)}/assets"
    stem, suffix = Path(name).stem, Path(name).suffix
    for i in range(0, 100):
        candidate = name if i == 0 else f"{stem}-{i + 1}{suffix}"
        assets_rel = f"{base_rel}/{candidate}"
        try:
            target = _vault_abs(self, assets_rel)
        except ValueError as e:
            return "", str(e)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(target.write_bytes, data)
            return assets_rel, ""
        if await asyncio.to_thread(target.read_bytes) == data:
            return assets_rel, ""
    return "", "too many files with this name under assets/"


async def _remove_vault_file(self, rel: str) -> None:
    """Undo a scratch write (a dry-run conversion of a non-PDF upload)."""
    try:
        target = _vault_abs(self, rel)
        if target.is_file():
            await asyncio.to_thread(target.unlink)
    except Exception:  # noqa: BLE001 — best-effort cleanup
        pass


def _reference_body(title: str, standard_id: str, edition: str, project: str,
                    fulltext_rel: str, sections: list[dict]) -> str:
    cite = f"{standard_id} {edition}".strip()
    lines = [f"# {title}", "",
             f"Client-supplied document registered for project `{project}`. "
             f"Cite it as `{cite} §<clause>` in a requirement's *Traces to* field.", "",
             f"Full extracted text: `[[{fulltext_rel}]]`.", ""]
    heads = [s for s in sections if s.get("level") == 1]
    if heads:
        lines += ["## Structure", ""]
        for s in heads:
            lines.append(f"- {s.get('no')} {s.get('title') or ''}".rstrip())
        lines.append("")
    return "\n".join(lines)


def list_sources(self, project: str = "", include_global: bool = True) -> dict:
    """Reference notes usable as requirement sources.

    With ``project``: that project's own ingested documents first (``scope:
    "project"``), then — when ``include_global`` — the global KB's references
    (``scope: "global"``), so a project can cite both its client's spec and the
    published standards. Without ``project``: the global references only.
    """
    project = (project or "").strip()
    notes = self._all_notes(include_project=True)
    clause_counts: dict[tuple[str, str], int] = {}
    for n in notes:
        p = n.get("properties", {}) or {}
        if p.get("kind") != "clause":
            continue
        key = (str(p.get("standard_id") or "").strip().lower(), str(p.get("project") or "").strip())
        clause_counts[key] = clause_counts.get(key, 0) + 1
    rows: list[dict] = []
    for n in notes:
        p = n.get("properties", {}) or {}
        if p.get("kind") != "reference":
            continue
        scope_project = str(p.get("project") or "").strip()
        if scope_project:
            if scope_project != project:
                continue
            scope = "project"
        else:
            if not include_global:
                continue
            scope = "global"
        sid = str(p.get("standard_id") or "").strip()
        rows.append({
            "slug": _slug_of(n.get("path", "")),
            "title": p.get("title") or n.get("name") or "",
            "standard_id": sid,
            "standard": p.get("standard") or sid,
            "edition": str(p.get("edition") or ""),
            "project": scope_project,
            "scope": scope,
            # No id → no join: an empty key would claim every clause note that
            # also lacks one (624 orphans on the real vault, 2026-10-01).
            "clause_count": clause_counts.get((sid.lower(), scope_project), 0) if sid else 0,
            "path": n.get("path", ""),
            "source_file": p.get("local_text") or p.get("source_file") or "",
        })
    rows.sort(key=lambda r: (r["scope"] != "project", str(r["title"]).lower()))
    return {"sources": rows, "count": len(rows), "project": project}


def source_clauses(self, reference_slug: str, bodies: bool = True) -> dict:
    """The clause notes of one source, in clause order, with bodies.

    Membership is by ``standard_id`` (case-insensitive) within the SAME scope as
    the reference (its ``project``, or global) and, when the reference declares
    an edition, that edition. This is what the requirements app extracts from.
    """
    ref = self._note_by_slug(reference_slug)
    if not ref:
        return {"error": "source not found", "slug": reference_slug}
    props = ref.get("properties", {}) or {}
    if props.get("kind") != "reference":
        return {"error": "not a reference note", "slug": reference_slug}
    sid = str(props.get("standard_id") or "").strip().lower()
    if not sid:
        # Same empty-key trap as list_sources: without an id the join would
        # return every clause note that also has none.
        return {"error": "source has no standard_id — nothing can be joined to it", "slug": reference_slug}
    project = str(props.get("project") or "").strip()
    edition = str(props.get("edition") or "").strip()
    rows: list[dict] = []
    for n in self._all_notes(include_project=True):
        p = n.get("properties", {}) or {}
        if p.get("kind") != "clause":
            continue
        if str(p.get("standard_id") or "").strip().lower() != sid:
            continue
        if str(p.get("project") or "").strip() != project:
            continue
        if edition and str(p.get("edition") or "").strip() != edition:
            continue
        clause = str(p.get("clause") or "")
        row = {
            "slug": _slug_of(n.get("path", "")),
            "clause": clause,
            "clause_title": p.get("clause_title") or "",
            "title": p.get("title") or "",
            "standard": p.get("standard") or props.get("standard") or "",
            "standard_id": props.get("standard_id") or "",
            "edition": str(p.get("edition") or ""),
            "project": project,
            "path": n.get("path", ""),
        }
        if bodies:
            row["body"] = self.vault_read_body(n.get("path", "")) or ""
        rows.append(row)
    rows.sort(key=lambda r: _clause_sort_key(r["clause"]))
    return {"ok": True, "slug": reference_slug, "standard_id": props.get("standard_id") or "",
            "standard": props.get("standard") or "", "edition": edition, "project": project,
            "title": props.get("title") or reference_slug, "clauses": rows, "count": len(rows)}


async def ingest_source(
    self, *, project: str, standard_id: str, text: str, title: str = "",
    edition: str = "", domain: str = "", original_file: str = "", dry_run: bool = True,
) -> dict:
    """Register a document's text as a project-scoped reference + clause notes.

    ``dry_run=True`` (the default) plans only — the reply says what would be
    written (reference path, clause count, first clauses, warnings) and nothing
    touches the vault. ``dry_run=False`` writes the fulltext, the reference and
    the clause notes, rebuilds the citation index and emits
    ``kb:source_ingested``. Propose → preview → confirm with one body, two calls.
    """
    project = (project or "").strip()
    standard_id = re.sub(r"\s+", " ", (standard_id or "").strip())
    edition = re.sub(r"\s+", " ", (edition or "").strip())
    text = (text or "").strip()
    if not project or not safe_path_segment(project):
        return {"error": "project is required"}
    if not standard_id:
        return {"error": "standard_id (the document's own id, e.g. XYZ-SPEC-001) is required"}
    if not text:
        return {"error": "no text to ingest"}
    if len(text.encode("utf-8")) > MAX_SOURCE_BYTES:
        return {"error": "document too large"}
    title = (title or "").strip() or (f"{standard_id} — {edition}" if edition else standard_id)

    slug = "-".join(s for s in (slugify(project, max_len=None),
                                slugify(standard_id, max_len=None),
                                slugify(edition, max_len=None)) if s)
    if self._note_by_slug(slug):
        return {"error": "this document is already registered for the project", "slug": slug}

    src_dir = _source_dir(self, project)
    fulltext_rel = f"{src_dir}/_fulltext/{slug}.md"
    # Contents table when the document has one, else its body headings — a
    # Word-exported client spec rarely carries dot-leader TOC rows.
    sections = parse_sections(text)
    today = datetime.now().date().isoformat()
    specs = plan_atomization(
        text,
        reference_slug=slug,
        reference_title=title,
        standard_id=standard_id,
        edition=edition or None,
        domain=domain,
        source_file=fulltext_rel,
        existing_clauses=[],
        # `tags` and `standard` OVERRIDE the planner's defaults (`kb`, the title):
        # the clauses must carry the source tag, and the same `standard` key as
        # the reference note, or the citation index files them apart.
        extra_frontmatter={"tags": [SOURCE_TAG], "standard": standard_id,
                           "project": project, "source_kind": SOURCE_KIND},
        sources_dir=src_dir,
        today=today,
        sections=sections,
    )
    warnings: list[str] = []
    if not specs:
        warnings.append("no numbered §X.Y sections were found — the document is registered "
                        "as a whole; cite it without a clause number")
    plan = {
        "ok": True, "dry_run": dry_run, "slug": slug, "project": project,
        "standard_id": standard_id, "edition": edition, "title": title,
        "reference_path": f"{src_dir}/{slug}.md", "fulltext_path": fulltext_rel,
        "clause_count": len(specs),
        "clauses": [{"clause": s.clause, "title": s.frontmatter.get("clause_title", ""),
                     "slug": s.slug} for s in specs[:200]],
        "chars": len(text), "warnings": warnings,
    }
    if dry_run:
        return plan

    await self.write(fulltext_rel, text)
    fm = {
        "tags": [SOURCE_TAG],
        "kind": "reference",
        "title": title,
        "standard_id": standard_id,
        "standard": standard_id,
        "edition": edition or None,
        "project": project,
        "source_kind": SOURCE_KIND,
        "source_file": fulltext_rel,
        "created": today,
        "updated": today,
    }
    if domain:
        fm["domain"] = domain
    if original_file:
        fm["original_file"] = original_file
    body = _reference_body(title, standard_id, edition, project, fulltext_rel, sections)
    self.vault_create_note(plan["reference_path"], fm, body)
    created, _skipped = self._write_clause_specs(specs, dry_run=False)
    # The index normally refreshes on vault:changed; rebuild now so the very next
    # resolve_reference (the requirements app's, in the same user action) sees it.
    self._build_ref_index()
    await self.emit("kb:source_ingested", {"slug": slug, "project": project,
                                           "standard_id": standard_id, "clauses": len(created)})
    plan["created_clauses"] = created
    return plan


@web_route("GET", "/api/sources")
async def api_sources(self, request):
    """``?project=<id>`` → that project's documents + global references; bare → global only."""
    project = (request.query_params.get("project") or "").strip()
    include_global = (request.query_params.get("global") or "1") != "0"
    return self.list_sources(project=project, include_global=include_global)


@web_route("GET", "/api/sources/{slug}/clauses")
async def api_source_clauses(self, request):
    """One source's clause notes in order; ``?bodies=0`` for the outline only."""
    slug = request.path_params.get("slug", "")
    bodies = (request.query_params.get("bodies") or "1") != "0"
    return self.source_clauses(slug, bodies=bodies)


@web_route("POST", "/api/sources/ingest")
async def api_ingest_source(self, request):
    """Register a client document for a project (propose → confirm).

    Multipart: ``file`` + ``project``, ``standard_id``, ``title?``, ``edition?``,
    ``domain?``, ``dry_run`` (``"0"`` to write; anything else previews).
    JSON: the same fields with ``text`` (pasted) or ``path`` (vault-relative
    file already in the vault) in place of ``file``.

    Reads stay inside the vault; the original upload is kept under the project's
    ``assets/`` on confirm only — a dry run stores nothing (a DOCX/DOC preview
    needs the converter to see a file, so it is written and removed again
    within the call). No LLM is involved — the text is stored, sliced and indexed.
    """
    ctype = (request.headers.get("content-type") or "").lower()
    fields: dict = {}
    text = ""
    original_rel = ""
    if "multipart/form-data" in ctype:
        form = await request.form()
        fields = {k: (v if isinstance(v, str) else "") for k, v in form.items()}
        upload = form.get("file")
        if upload is None or isinstance(upload, str):
            return {"error": "file is required"}
        data = await upload.read()
        if len(data) > MAX_SOURCE_BYTES:
            return {"error": "file too large"}
        project = (fields.get("project") or "").strip()
        if not project or not safe_path_segment(project):
            return {"error": "project is required"}
        dry_flag = str(fields.get("dry_run", "1")).strip().lower()
        dry_run = dry_flag not in ("0", "false", "no")
        name = _asset_name(getattr(upload, "filename", "") or "")
        suffix = Path(name).suffix
        if dry_run and (suffix == ".pdf" or suffix in _TEXT_SUFFIXES):
            text, err = await _text_from_bytes(self, name, data)  # bytes only, nothing stored
        else:
            original_rel, err = await _store_upload(self, project, name, data)
            if err:
                return {"error": err}
            text, err = await _text_from_bytes(self, original_rel, data)
            if dry_run:
                await _remove_vault_file(self, original_rel)
                original_rel = ""
        if err:
            return {"error": err}
    else:
        try:
            fields = await self.read_json(request)
        except Exception:  # noqa: BLE001 — a malformed body is a 200 with an error, not a 500
            return {"error": "invalid JSON body"}
        if not isinstance(fields, dict):
            return {"error": "JSON body must be an object"}
        text = str(fields.get("text") or "")
        rel = str(fields.get("path") or "").strip()
        if not text and rel:
            try:
                target = _vault_abs(self, rel)
            except ValueError as e:
                return {"error": str(e)}
            if not target.is_file():
                return {"error": "path not found in the vault"}
            data = await asyncio.to_thread(target.read_bytes)
            if len(data) > MAX_SOURCE_BYTES:
                return {"error": "file too large"}
            text, err = await _text_from_bytes(self, rel, data)
            if err:
                return {"error": err}
            original_rel = rel
    dry_flag = str(fields.get("dry_run", "1")).strip().lower()
    dry_run = dry_flag not in ("0", "false", "no")
    return await self.ingest_source(
        project=str(fields.get("project") or ""),
        standard_id=str(fields.get("standard_id") or ""),
        text=text if isinstance(text, str) else "",
        title=str(fields.get("title") or ""),
        edition=str(fields.get("edition") or ""),
        domain=str(fields.get("domain") or ""),
        original_file=original_rel,
        dry_run=dry_run,
    )
