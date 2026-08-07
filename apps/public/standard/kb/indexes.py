"""kb — in-memory caches: free-text reference index + implementation map.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: Reference + implementation index build & lookup helpers (`_build_ref_index`, `_build_implementation_index`, `_lookup_ref`, `_resolve_references`, `_clauses_for_standard`). Subscribes to `vault:changed` for cache invalidation.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._all_notes / self._note_path (notes) when scanning the vault.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import re
from pathlib import Path
from emptyos.sdk import on_event
from emptyos.sdk.kb_refs import parse_impl_ref
from typing import TYPE_CHECKING

from .shared import (
    _clause_sort_key,
    _edition_sort_key,
    _norm_clause,
    _norm_edition,
    _norm_standard,
    _parse_citation,
    _slug_of,
    resolve_supersession,
    resolve_document_cascade,
)

if TYPE_CHECKING:
    from .app import KBApp  # noqa: F401 — for type hints only


# ─── Bind to KBApp class as ────────────────────────────────
#   _on_vault_changed            = _indexes._on_vault_changed
#   _build_ref_index             = _indexes._build_ref_index
#   _build_implementation_index  = _indexes._build_implementation_index
#   _resolve_references          = _indexes._resolve_references
#   _clauses_for_standard        = _indexes._clauses_for_standard
#   revisions_for_document       = _indexes.revisions_for_document
#   _lookup_ref                  = _indexes._lookup_ref
#   _kind_index                  = _indexes._kind_index
#   _parse_impl_ref              = _indexes._parse_impl_ref  # @staticmethod
#   _check_implemented_in        = _indexes._check_implemented_in
#   _supersession_enabled        = _indexes._supersession_enabled
#   _build_supersession_index    = _indexes._build_supersession_index
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


@on_event("vault:changed")
async def _on_vault_changed(self, payload: dict):
    # Cheap to rebuild; the kb-tagged set is small relative to the full vault.
    self._build_ref_index()
    self._build_implementation_index()
    # When the feature flag is off this is a single bool check + 3 empty
    # assigns — no behaviour change vs. pre-feature.
    self._build_supersession_index()


def _build_ref_index(self) -> None:
    all_notes = self._all_notes()
    # Forward: (standard, edition, clause) → ref_slug
    idx: dict[tuple[str, str | None, str | None], str] = {}
    for n in all_notes:
        props = n.get("properties", {}) or {}
        if props.get("kind") != "clause":
            continue
        standard = _norm_standard(props.get("standard") or "")
        if not standard:
            continue
        edition = props.get("edition")
        edition = str(edition) if edition else None
        clause = _norm_clause(props.get("clause")) if props.get("clause") else None
        slug = _slug_of(n.get("path", ""))
        if not slug:
            continue
        idx[(standard, edition, clause)] = slug
    self._ref_index = idx
    # Inbound: walk every kb note's `references:` field, parse, lookup, record citers.
    # Use the same _lookup_ref fallback semantics as get_note's resolution path.
    inbound: dict[str, set[str]] = {}
    for n in all_notes:
        citer_slug = _slug_of(n.get("path", ""))
        if not citer_slug:
            continue
        props = n.get("properties", {}) or {}
        for raw in props.get("references") or []:
            parsed = _parse_citation(str(raw))
            if not parsed:
                continue
            standard, edition, clause = parsed
            ref_slug = self._lookup_ref(standard, edition, clause)
            if ref_slug:
                inbound.setdefault(ref_slug, set()).add(citer_slug)
    self._inbound_citations = inbound


def _build_implementation_index(self) -> None:
    """Reverse index: code path → [slug] of formula/concept notes implementing it.

    Reads each KB note's `implemented_in:` field and partitions the slugs by
    normalized code path (the part before `::symbol` if present). Lets apps
    and CI ask "which KB notes does this file implement?"
    """
    idx: dict[str, list[str]] = {}
    for n in self._all_notes():
        slug = _slug_of(n.get("path", ""))
        if not slug:
            continue
        props = n.get("properties", {}) or {}
        for ref in props.get("implemented_in") or []:
            kind, code_path, _ = self._parse_impl_ref(ref)
            # method-only refs don't carry a code path; skip for the reverse
            # lookup since callers will query by file path.
            if kind == "method" or not code_path:
                continue
            idx.setdefault(code_path, []).append(slug)
    # Stable ordering inside each bucket
    for path_key in idx:
        idx[path_key].sort()
    self._implementation_index = idx


def _supersession_enabled(self) -> bool:
    """Dark-ship gate for temporal supersession (default off = no behaviour change)."""
    return bool(self.app_config("feature.temporal-supersession.enabled", False))


def _build_supersession_index(self) -> None:
    """Build forward/reverse/terminal supersession maps from `superseded_by:`.

    Sibling of `_build_ref_index` (NOT folded into it) so the flag-off path is
    trivially a no-op: empty maps + an early return. Only `clause`/`reference`
    notes participate. See `.claude/rules/*` borrow-plan (Supermemory B2a).
    """
    # Empty maps are the flag-OFF default — every consumer reads these and
    # finds nothing, so behaviour is identical to pre-feature.
    self._supersession_forward = {}
    self._supersession_reverse = {}
    self._supersession_terminal = {}
    # Derived document-level cascade: {clause_slug -> successor_reference_slug}
    # for clauses whose whole document revision was superseded (no per-clause
    # edge). Empty when the flag is off.
    self._supersession_doc_cascade = {}
    if not self._supersession_enabled():
        return
    edges: dict[str, str] = {}
    known: set[str] = set()
    references: list[dict] = []
    clauses: list[dict] = []
    for n in self._all_notes():
        slug = _slug_of(n.get("path", ""))
        if not slug:
            continue
        known.add(slug)
        props = n.get("properties", {}) or {}
        kind = props.get("kind")
        if kind not in ("clause", "reference"):
            continue
        target = str(props.get("superseded_by") or "").strip().strip("[]").strip()
        if target:
            edges[slug] = target
        rec = {
            "slug": slug,
            "standard_id": props.get("standard_id"),
            "edition": props.get("edition"),
            "superseded_by": target,
        }
        if kind == "reference":
            references.append(rec)
        else:
            clauses.append(rec)
    maps = resolve_supersession(edges, known)
    self._supersession_forward = maps["forward"]
    self._supersession_reverse = maps["reverse"]
    self._supersession_terminal = maps["terminal"]
    # Document-cascade: only keep derived edges whose successor reference exists.
    cascade = resolve_document_cascade(references, clauses)
    self._supersession_doc_cascade = {
        c: tgt for c, tgt in cascade.items() if tgt in known
    }


def _resolve_references(self, refs: list) -> list[dict]:
    """Convert free-text references into structured {text, target_slug, in_kb} dicts.

    Falls through with target_slug=None for un-parseable / un-matched strings so
    the UI render path is uniform. Match order: exact triple → drop edition (try
    any edition with same clause) → drop clause (try any edition with same
    standard whole-standard) → no match.
    """
    out: list[dict] = []
    for raw in refs or []:
        text = str(raw).strip()
        if not text:
            continue
        parsed = _parse_citation(text)
        target = None
        if parsed:
            standard, edition, clause = parsed
            target = self._lookup_ref(standard, edition, clause)
        out.append({"text": text, "target_slug": target, "in_kb": target is not None})
    return out


def _clauses_for_standard(
    self,
    standard_id: str,
    standard_name: str = "",
    edition: str | None = None,
) -> list[dict]:
    """List the clause notes belonging to a standard, for its landing page.

    Join order (a clause matches if EITHER holds):
      1. clause ``standard_id`` equals the passed id (case-insensitive) — the
         reliable join; both reference + clause notes carry ``standard_id``.
      2. clause ``standard:`` *title* prefix-matches (legacy notes that only set
         the title, e.g. `IEC 60287` collecting `IEC 60287-1-1`).

    Sorted by (standard, edition **version-desc** via ``_edition_sort_key``,
    clause numerically). When the supersession flag is on, each row also carries
    ``superseded_by`` (immediate successor slug or ``None``) so a landing page
    can mix current + superseded revisions and badge them.
    """
    want_id = str(standard_id or "").strip().lower()
    want_name = _norm_standard(standard_name or standard_id)
    want_edition = _norm_edition(edition)
    if not want_id and not want_name:
        return []
    on = self._supersession_enabled()
    rows: list[dict] = []
    for n in self._all_notes():
        props = n.get("properties", {}) or {}
        if props.get("kind") != "clause":
            continue
        sid = str(props.get("standard_id") or "").strip().lower()
        std = _norm_standard(props.get("standard") or "")
        clause_edition = _norm_edition(props.get("edition"))
        if want_edition and clause_edition and want_edition != clause_edition:
            continue
        id_match = bool(want_id) and sid == want_id
        name_match = bool(want_name) and bool(std) and std.startswith(want_name)
        if not (id_match or name_match):
            continue
        slug = _slug_of(n.get("path", ""))
        # Clause as stored in frontmatter may have leading '§' — strip it; the UI prepends.
        raw_clause = str(props.get("clause") or "").replace("§", "").strip()
        row = {
            "slug": slug,
            "standard": std,
            "edition": str(props.get("edition")) if props.get("edition") else None,
            "clause": raw_clause,
            "clause_title": props.get("clause_title") or "",
            "title": props.get("title") or n.get("name") or "",
        }
        if on:
            succ = self._supersession_forward.get(slug) or self._supersession_doc_cascade.get(slug)
            row["superseded_by"] = succ
        rows.append(row)
    # Multi-pass stable sort (mixed directions): least-significant key first.
    rows.sort(key=lambda r: _clause_sort_key(_norm_clause(r["clause"])))
    rows.sort(key=lambda r: _edition_sort_key(r["edition"]), reverse=True)  # newest edition first
    rows.sort(key=lambda r: r["standard"])
    return rows


def revisions_for_document(self, standard_id: str) -> list[dict]:
    """List the reference notes (revisions) sharing a ``standard_id``, newest first.

    Foundation for the revisions panel + the revision-diff engine: a "document"
    is a ``standard_id`` and its revisions are the ``kind: reference`` notes that
    share it. ``current`` is True for a revision not superseded by another (the
    live one). ``superseded_by`` reflects the supersession flag (``None`` when off).
    """
    want_id = str(standard_id or "").strip().lower()
    if not want_id:
        return []
    on = self._supersession_enabled()
    rows: list[dict] = []
    for n in self._all_notes():
        props = n.get("properties", {}) or {}
        if props.get("kind") != "reference":
            continue
        if str(props.get("standard_id") or "").strip().lower() != want_id:
            continue
        slug = _slug_of(n.get("path", ""))
        succ = self._supersession_forward.get(slug) if on else None
        rows.append({
            "slug": slug,
            "edition": str(props.get("edition")) if props.get("edition") else None,
            "title": props.get("title") or n.get("name") or "",
            "superseded_by": succ,
            "current": succ is None,
        })
    rows.sort(key=lambda r: _edition_sort_key(r["edition"]), reverse=True)
    return rows


def _lookup_ref(self, standard: str, edition: str | None, clause: str | None) -> str | None:
    """Match (standard, edition, clause) against the index with progressive fallback.

    Citations may omit edition or use a wider/older edition; the index always
    carries whatever the reference note declared. So we:
      1. Try exact match.
      2. If exact fails, scan all editions of (standard, clause) and pick the
         newest edition that matches (or any if all editions are None).
      3. If clause-specific match fails, try whole-standard match.
    """
    # 1. Exact
    key = (standard, edition, clause)
    if key in self._ref_index:
        return self._ref_index[key]
    # 2. Drop edition — scan for any edition with this (standard, clause)
    candidates = [
        (s, e, c, slug) for (s, e, c), slug in self._ref_index.items()
        if s == standard and c == clause
    ]
    if candidates:
        # Prefer the newest edition (version-aware: 'Rev 1.0' > 'Rev 0.2'; None last)
        candidates.sort(key=lambda x: _edition_sort_key(x[1]), reverse=True)
        return candidates[0][3]
    # 3. Whole-standard fallback
    whole = self._ref_index.get((standard, None, None))
    if whole:
        return whole
    # 4. Any edition of whole-standard
    any_whole = [slug for (s, e, c), slug in self._ref_index.items() if s == standard and c is None]
    if any_whole:
        return any_whole[0]
    return None


def _kind_index(self, all_notes: list[dict]) -> dict[str, dict]:
    """Map slug -> {kind, domain} for quick lookups (outgoing-edge enrichment)."""
    return {
        _slug_of(n.get("path", "")): {
            "kind": (n.get("properties", {}) or {}).get("kind", ""),
            "domain": (n.get("properties", {}) or {}).get("domain", ""),
        }
        for n in all_notes
        if _slug_of(n.get("path", ""))
    }


# Canonical parse lives in emptyos.sdk.kb_refs (shared with kb-butler's repair
# resolver — rule 9, 2nd consumer). Bound as a staticmethod so existing
# ``self._parse_impl_ref(ref)`` call sites + the app.py class binding are unchanged.
_parse_impl_ref = staticmethod(parse_impl_ref)


def _check_implemented_in(self, refs: list) -> list[dict]:
    repo_root = Path(self.kernel.config.path).parent
    if isinstance(refs, str):
        refs = [refs]  # scalar string — don't iterate char-by-char
    out = []
    for ref in refs:
        kind, code_path, sym = self._parse_impl_ref(ref)
        sref = str(ref).strip()
        if kind == "method":
            out.append({"ref": sref, "path": "", "symbol": code_path, "kind": "method", "exists": None})
            continue
        ok = bool(code_path) and (repo_root / code_path).exists()
        out.append({"ref": sref, "path": code_path, "symbol": sym, "kind": "path", "exists": ok})
    return out
