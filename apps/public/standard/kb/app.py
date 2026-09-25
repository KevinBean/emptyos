"""Knowledge Base — vault-resident, domain-general note browser.

Every KB note carries `kb` in its `tags:` list and is typed by `kind`:
  concept / formula / reference / clause / case / lesson / guide / doc / moc

The corpus is queried by tag (not folder) and partitioned by `domain`
(power-systems, cable-thermal, ...). Backlinks are computed from frontmatter
`related:` + body `[[wikilinks]]` + parsed `references:` citations. The
`implemented_in:` field links formulas to code paths checked against the repo.

`kind: doc` notes compose other KB notes via a JSON-encoded `paragraphs_json`
frontmatter field — each paragraph has `noteRefs: [slug | slug#section]` that
the renderer resolves at view time. Docs live at `30_Resources/EmptyOS/kb/docs/`
by default (overridable via `[apps.kb] docs_dir` in `emptyos.toml`).

Prescriptive **guidelines** (absorbed from the retired `guideline` app,
2026-07-10) live alongside the corpus in `guidelines.py`. They keep their own
`guideline` tag — not a 10th `kind` — and cite KB notes via `[[kb:slug]]`.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, extract_wikilinks, on_event, parse_llm_json, web_route

from . import digest as _digest
from . import compose as _compose
from . import docs as _docs
from . import embeds as _embeds
from . import engineering_evidence as _engineering_evidence
from . import flipbook as _flipbook
from . import graph as _graph
from . import guidelines as _guidelines
from . import indexes as _indexes
from . import fulltext as _fulltext
from . import boards as _boards
from . import figures as _kbfigures
from . import notes as _notes
from . import revisions as _revisions
from . import reference_coverage as _reference_coverage
from . import source_pdf as _source_pdf

# Flipbook subsystem — ported from apps/explore/ during the kb⇔explore merge.
# Two mixins compose into KBApp so flipbook routes (start / expand / save /
# detail / symbols / page / asset / refine_anchors) and storage live alongside
# the rest of kb's note-corpus API. See flipbook_gen.py and flipbook_io.py.
from .flipbook_gen import GenerationMixin as _FlipbookGenerationMixin
from .flipbook_io import VaultIOMixin as _FlipbookIOMixin

from .shared import (
    KINDS,
    DEFAULT_DOCS_DIR,
    DEFAULT_NOTES_DIR,
    LEGACY_FLIPBOOK_DIRS,
    _CITATION_RE,
    _slugify,
    _slug_of,
    _related_targets,
    _note_has_visual,
    _norm_standard,
    _norm_clause,
    _clause_sort_key,
    _norm_edition,
    _edition_sort_key,
    _parse_citation,
    resolve_supersession,
    resolve_document_cascade,
)



class KBApp(_FlipbookGenerationMixin, _FlipbookIOMixin, BaseApp):

    _ref_index: dict[tuple[str, str | None, str | None], str]

    _inbound_citations: dict[str, set[str]]

    _implementation_index: dict[str, list[str]]

    # Temporal supersession maps (populated only when the feature flag is on).
    _supersession_forward: dict[str, str]
    _supersession_reverse: dict[str, list[str]]
    _supersession_terminal: dict[str, str]
    # Derived clause-level cascade from document(reference)-level supersession.
    _supersession_doc_cascade: dict[str, str]

    async def setup(self):
        await super().setup()
        self._ref_index = {}
        self._inbound_citations = {}
        self._implementation_index = {}
        self._supersession_forward = {}
        self._supersession_reverse = {}
        self._supersession_terminal = {}
        self._supersession_doc_cascade = {}
        # Per-guideline write locks (guidelines.py) — read-modify-write race
        # guard on clause section edits, per CLAUDE.md § Development Gotchas.
        self._guideline_locks = {}
        self._build_ref_index()
        self._build_implementation_index()
        self._build_supersession_index()
        # Seed the flipbook symbol library (idempotent — uses a `.seeded` sentinel)
        self._seed_demo_symbols()

    SUMMARIZE_NOTES_SYSTEM = (
        "You compress N knowledge notes into one summary. The user picked these "
        "notes because they share a theme — find that thread and surface it.\n\n"
        "Do NOT:\n"
        "- Restate every note verbatim or in order.\n"
        "- Hedge ('this seems to suggest', 'one might consider…').\n"
        "- Add citations, links, or note slugs — the reader has them already.\n"
        "- Output headers, frontmatter, or 'Summary:' prefixes."
    )

    async def companion_context(self) -> str | None:
        """Focus-mode live context — KB size + domain coverage."""
        try:
            notes = self._all_notes()
        except Exception:
            return None
        if not notes:
            return "The knowledge base is empty."
        domains = {(n.get("properties", {}) or {}).get("domain") for n in notes}
        domains.discard(None)
        dl = ", ".join(sorted(str(d) for d in domains)[:8])
        return f"{len(notes)} KB notes across {len(domains)} domain(s)" + (f": {dl}." if dl else ".")

    async def panel_summary(self) -> dict | None:
        notes = self._all_notes()
        if not notes:
            return None
        domains = {(n.get("properties", {}) or {}).get("domain") for n in notes}
        domains.discard(None)
        return {
            "label": "Knowledge Base",
            "value": f"{len(notes)} notes",
            "detail": f"{len(domains)} domain{'s' if len(domains) != 1 else ''}",
            "link": "/kb/",
        }

    @cli_command("kb")
    async def cli_kb(self, action: str = "list", domain: str = "", kind: str = ""):
        """eos kb {list|domains|note <slug>|health}"""
        if action == "domains":
            for d in self.list_domains()["domains"]:
                self.print_rich(f"[bold]{d['domain']}[/bold]  {d['total']} notes  {d['by_kind']}")
        elif action == "health":
            data = await self.health()
            self.print_rich(f"Broken implemented_in: {len(data['broken_implemented_in'])}")
            self.print_rich(
                f"Formulas missing verification: {len(data['formulas_missing_verification'])}"
            )
            self.print_rich(f"Orphans: {len(data['orphans'])}")
            self.print_rich(f"Duplicate slugs: {len(data['duplicate_slugs'])}")
            self.print_rich(f"Invalid kind: {len(data['invalid_kind'])}")
            self.print_rich(
                f"Unresolved verified_against: {len(data['unresolved_verified_against'])}"
            )
            self.print_rich(
                f"Verification target not a case: {len(data['verification_target_not_case'])}"
            )
        elif action == "note":
            data = await self.get_note(domain or kind)
            if "error" in data:
                self.print_rich(f"[red]{data['error']}[/red]")
            else:
                self.print_rich(f"[bold]{data['name']}[/bold]")
                self.print_rich(f"  path: {data['path']}")
                self.print_rich(f"  backlinks: {len(data['backlinks'])}")
        else:
            data = self.list_notes(domain=domain, kind=kind)
            for n in data["notes"]:
                self.print_rich(f"  [{n['kind']:8}] {n['slug']:40} [dim]{n['domain']}[/dim]")
            self.print_rich(f"\n[dim]{data['count']} note(s)[/dim]")

    # ── Docs (extracted to docs.py) ──
    _parse_paragraphs      = _docs._parse_paragraphs
    _encode_paragraphs     = _docs._encode_paragraphs
    _normalize_paragraphs  = _docs._normalize_paragraphs
    _para_refs             = _docs._para_refs
    _slug_to_path          = _docs._slug_to_path
    list_docs              = _docs.list_docs
    get_doc                = _docs.get_doc
    create_doc             = _docs.create_doc
    update_doc             = _docs.update_doc
    render_doc             = _docs.render_doc
    delete_doc             = _docs.delete_doc
    api_docs               = _docs.api_docs
    api_doc_detail         = _docs.api_doc_detail
    api_doc_create         = _docs.api_doc_create
    api_doc_update         = _docs.api_doc_update
    api_doc_delete         = _docs.api_doc_delete
    action_summarize_notes = _docs.action_summarize_notes

    # ── Viz-artifact embeds (note side; embeds.py) ──
    _embed_enabled    = _embeds._embed_enabled
    api_embed_enabled = _embeds.api_embed_enabled
    api_embed         = _embeds.api_embed

    # ── Flipbook (extracted to flipbook.py) ──
    api_flipbook_start         = _flipbook.api_flipbook_start
    api_flipbook_expand        = _flipbook.api_flipbook_expand
    api_flipbook_symbols       = _flipbook.api_flipbook_symbols
    api_flipbook_save_symbol   = _flipbook.api_flipbook_save_symbol
    api_flipbook_symbol_get    = _flipbook.api_flipbook_symbol_get
    api_flipbook_delete_symbol = _flipbook.api_flipbook_delete_symbol
    api_flipbook_page_get      = _flipbook.api_flipbook_page_get
    api_flipbook_asset         = _flipbook.api_flipbook_asset
    api_flipbook_save          = _flipbook.api_flipbook_save
    api_flipbook_detail        = _flipbook.api_flipbook_detail
    api_flipbook_list          = _flipbook.api_flipbook_list

    # ── Graph (extracted to graph.py) ──
    api_graph           = _graph.api_graph
    api_note_by_path    = _graph.api_note_by_path
    api_implementations = _graph.api_implementations
    _build_backlink_map = _graph._build_backlink_map
    _kb_backlinks       = _graph._kb_backlinks

    # ── Indexes (extracted to indexes.py) ──
    _on_vault_changed           = _indexes._on_vault_changed
    _build_ref_index            = _indexes._build_ref_index
    _build_implementation_index = _indexes._build_implementation_index
    _resolve_references         = _indexes._resolve_references
    _clauses_for_standard       = _indexes._clauses_for_standard
    revisions_for_document      = _indexes.revisions_for_document
    _lookup_ref                 = _indexes._lookup_ref
    _parse_impl_ref             = _indexes._parse_impl_ref
    _check_implemented_in       = _indexes._check_implemented_in
    _kind_index                 = _indexes._kind_index
    _supersession_enabled       = _indexes._supersession_enabled
    _build_supersession_index   = _indexes._build_supersession_index

    # ── Revision-diff engine (extracted to revisions.py) ──
    _revision_diff_enabled    = _revisions._revision_diff_enabled
    _revisions_cache_dir      = _revisions._revisions_cache_dir
    _reference_for            = _revisions._reference_for
    _revision_fulltext        = _revisions._revision_fulltext
    _load_revision_clauses    = _revisions._load_revision_clauses
    compute_revision_diff     = _revisions.compute_revision_diff
    api_revisions             = _revisions.api_revisions
    api_revision_diff         = _revisions.api_revision_diff
    api_revision_diff_digest  = _revisions.api_revision_diff_digest
    api_revision_diff_publish = _revisions.api_revision_diff_publish

    # ── Full-text reader (extracted to fulltext.py) ──
    _fulltext_enabled    = _fulltext._fulltext_enabled
    _note_by_slug        = _fulltext._note_by_slug
    _read_fulltext_for   = _fulltext._read_fulltext_for
    api_fulltext_index   = _fulltext.api_fulltext_index
    api_fulltext_section = _fulltext.api_fulltext_section

    # ── Composed-document reader (compose.py) — the standard as its atomic clause notes ──
    _composed_enabled    = _compose._composed_enabled
    _chapter_titles      = _compose._chapter_titles
    _compose_clauses     = _compose._compose_clauses
    api_compose_index    = _compose.api_compose_index
    api_compose_chapter  = _compose.api_compose_chapter

    # Reusable atomization — generate atomic clause notes from a reference's archive.
    atomize_standard     = _compose.atomize_standard
    api_atomize_standard = _compose.api_atomize_standard

    # ── Notes (extracted to notes.py) ──
    _docs_dir             = _notes._docs_dir
    _doc_path             = _notes._doc_path
    # ── Figure receive (extracted to figures.py) ──
    figure_asset_path     = _kbfigures.figure_asset_path
    attach_figure         = _kbfigures.attach_figure

    _notes_dir            = _notes._notes_dir
    _note_path            = _notes._note_path
    # Boards-as-view-layer contract (read-only) — see boards.py.
    SETTABLE_FIELDS       = _boards.KB_SETTABLE_FIELDS
    list_all              = _boards.list_all
    kb_board_formulas     = _boards.kb_board_formulas
    kb_board_clauses      = _boards.kb_board_clauses
    board_presets         = _boards.board_presets

    # Engineering method -> standard -> clause -> formula -> case evidence.
    engineering_evidence      = _engineering_evidence.engineering_evidence
    engineering_evidence_rows = _engineering_evidence.engineering_evidence_rows
    api_engineering_evidence  = _engineering_evidence.api_engineering_evidence

    _all_notes            = _notes._all_notes
    _summarize            = _notes._summarize
    _title_for            = _notes._title_for
    list_domains          = _notes.list_domains
    list_notes            = _notes.list_notes
    get_note              = _notes.get_note
    note_path             = _notes.note_path
    voice_kb_search       = _notes.voice_kb_search
    health                = _notes.health
    _note_health_flags    = _notes._note_health_flags
    api_domains           = _notes.api_domains
    api_notes             = _notes.api_notes
    api_note_detail       = _notes.api_note_detail
    api_note_section      = _notes.api_note_section
    api_health            = _notes.api_health
    api_references        = _notes.api_references
    api_resolve_reference = _notes.api_resolve_reference
    resolve_reference     = _notes.resolve_reference
    create_note           = _notes.create_note
    deliver_work_notes    = _notes.deliver_work_notes
    upsert_note           = _notes.upsert_note
    update_implemented_in = _notes.update_implemented_in
    api_note_create       = _notes.api_note_create

    # Reference archive + clause coverage (read model over existing notes).
    reference_coverage     = _reference_coverage.reference_coverage
    api_reference_coverage = _reference_coverage.api_reference_coverage
    api_mark_reference_checked = _reference_coverage.api_mark_reference_checked

    # ── Digest (extracted to digest.py) ──
    api_digest_doc        = _digest.api_digest_doc
    _digest_extract       = _digest._digest_extract

    # ── Source PDF locator (extracted to source_pdf.py; roots/open in BaseApp) ──
    api_source_pdf        = _source_pdf.api_source_pdf
    api_open_local        = _source_pdf.api_open_local
    _standard_query       = _source_pdf._standard_query

    # ── Guidelines (guidelines.py — absorbed from the retired `guideline` app) ──
    _gid_lock                   = _guidelines._gid_lock
    _gl_all                     = _guidelines._gl_all
    _gl_find                    = _guidelines._gl_find
    _gl_summarize               = _guidelines._gl_summarize
    _gl_rewrite                 = _guidelines._gl_rewrite
    _build_kb_resolver          = _guidelines._build_kb_resolver
    _resolve_cites_in           = _guidelines._resolve_cites_in
    guideline_list_all          = _guidelines.guideline_list_all
    guideline_categories        = _guidelines.guideline_categories
    guideline_get               = _guidelines.guideline_get
    guideline_add               = _guidelines.guideline_add
    guideline_set_field         = _guidelines.guideline_set_field
    guideline_deprecate         = _guidelines.guideline_deprecate
    guideline_remove            = _guidelines.guideline_remove
    guideline_add_clause        = _guidelines.guideline_add_clause
    guideline_update_clause     = _guidelines.guideline_update_clause
    guideline_delete_clause     = _guidelines.guideline_delete_clause
    api_guidelines              = _guidelines.api_guidelines
    api_guideline_categories    = _guidelines.api_guideline_categories
    api_guideline_get           = _guidelines.api_guideline_get
    api_guideline_add           = _guidelines.api_guideline_add
    api_guideline_set_field     = _guidelines.api_guideline_set_field
    api_guideline_deprecate     = _guidelines.api_guideline_deprecate
    api_guideline_remove        = _guidelines.api_guideline_remove
    api_guideline_add_clause    = _guidelines.api_guideline_add_clause
    api_guideline_update_clause = _guidelines.api_guideline_update_clause
    api_guideline_delete_clause = _guidelines.api_guideline_delete_clause
    panel_guideline_daily       = _guidelines.panel_guideline_daily
    voice_guideline_show        = _guidelines.voice_guideline_show
    voice_guideline_random      = _guidelines.voice_guideline_random
    cli_guideline               = _guidelines.cli_guideline
