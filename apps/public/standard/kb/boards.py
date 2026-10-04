"""KB — boards-as-view-layer contract over the typed note corpus.

The KB is a nine-`kind` corpus of vault notes; until now the only way to see it
as a table was the app's own browser. This exposes it through the generic boards
view layer (`.claude/rules/boards-as-view-layer.md`) so the same corpus renders
as a table / kanban / etc. with filter + sort + saved views for free — the
"table/board over the typed corpus" gap (formulas by verification, clauses by
standard).

It is **read-only**: KB notes are the source of truth and carry semantics
(citation resolution, kind typing, verification links) that must stay edited in
the KB app itself, so `SETTABLE_FIELDS` is empty and there is no `set_field`.
Verification status isn't a stored field — a formula is "verified" iff it has a
non-empty `verified_against`, so it's derived here.

Pure row builder is module-level; the list/preset methods bind onto `KBApp`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .shared import KINDS, _slug_of

if TYPE_CHECKING:
    from .app import KBApp  # noqa: F401 — for type hints only


# ─── Bind to KBApp class as ──────────────────────────────────────────
#   SETTABLE_FIELDS  = _boards.KB_SETTABLE_FIELDS   # readonly board — empty set
#   list_all         = _boards.list_all             # all KB notes  (boards source)
#   kb_board_formulas = _boards.kb_board_formulas    # formulas only (boards source)
#   kb_board_clauses  = _boards.kb_board_clauses     # clauses only  (boards source)
#   board_presets    = _boards.board_presets        # [[contributes.boards.preset]]
# Adding one? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


# Read-only view — KB notes are the source of truth; edits stay in /kb/.
KB_SETTABLE_FIELDS: frozenset = frozenset()


def _as_text(v) -> str:
    """A frontmatter value → a flat display string (lists join with commas)."""
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v)
    return "" if v is None else str(v)


def _kb_row(note: dict) -> dict:
    """One KB note → a boards row. Display scalars only; no body, no path."""
    p = note.get("properties", {}) or {}
    kind = p.get("kind", "") or ""
    verified = ""
    if kind == "formula":
        verified = "verified" if p.get("verified_against") else "unverified"
    return {
        "id": _slug_of(note.get("path", "")),
        "title": p.get("title", "") or note.get("name", ""),
        "kind": kind,
        "domain": p.get("domain", "") or "",
        "topic": p.get("topic", "") or "",
        "standard": _as_text(p.get("standard") or p.get("standard_id")),
        "edition": _as_text(p.get("edition")),
        "clause": _as_text(p.get("clause")),
        "verified": verified,
        "implemented_in": _as_text(p.get("implemented_in")),
    }


# ─── Boards sources (bound onto KBApp) ───────────────────────────────


async def list_all(self) -> list[dict]:
    """Every KB note as a boards row, title-sorted. The generic corpus board."""
    rows = [_kb_row(n) for n in self._all_notes()]
    rows.sort(key=lambda r: (r["kind"], r["title"].lower()))
    return rows


async def kb_board_formulas(self) -> list[dict]:
    """Formulas only — the "by verification status" board."""
    return [r for r in await list_all(self) if r["kind"] == "formula"]


async def kb_board_clauses(self) -> list[dict]:
    """Clauses only — the "by standard/edition" board."""
    rows = [r for r in await list_all(self) if r["kind"] == "clause"]
    rows.sort(key=lambda r: (r["standard"].lower(), r["edition"], r["clause"]))
    return rows


# ─── Board presets ([[contributes.boards.preset]]) ───────────────────


def board_presets(self):
    """Live views over the KB corpus. Boards aggregates these generically
    via call_contributions; boards code holds no KB knowledge. All readonly."""
    src = lambda method: {"type": "app", "app": "kb", "method": method}
    verified_col = {
        "id": "verified", "label": "Verified", "type": "select",
        "options": ["verified", "unverified"],
        "color_map": {"verified": "green", "unverified": "amber"},
    }
    return [
        {
            "id": "kb-corpus",
            "name": "Knowledge Base",
            "description": "A live table/board over the whole KB corpus. Notes are "
                           "edited in the KB app itself — this is a read-only view.",
            "source": src("list_all"),
            "columns": [
                {"id": "title", "label": "Title", "type": "text"},
                {"id": "kind", "label": "Kind", "type": "select", "options": list(KINDS)},
                {"id": "domain", "label": "Domain", "type": "text"},
                {"id": "topic", "label": "Topic", "type": "text"},
                {"id": "standard", "label": "Standard", "type": "text"},
            ],
            "views": [
                {"type": "table", "default": True},
                {"type": "kanban", "group_by": "kind"},
            ],
            "kanban_group_by": "kind",
        },
        {
            "id": "kb-formulas",
            "name": "KB Formulas",
            "description": "Every formula note by verification status — a formula is "
                           "verified when it links a worked case in verified_against.",
            "source": src("kb_board_formulas"),
            "columns": [
                {"id": "title", "label": "Formula", "type": "text"},
                verified_col,
                {"id": "domain", "label": "Domain", "type": "text"},
                {"id": "implemented_in", "label": "Implemented in", "type": "text"},
            ],
            "views": [
                {"type": "table", "default": True},
                {"type": "kanban", "group_by": "verified"},
            ],
            "kanban_group_by": "verified",
        },
        {
            "id": "kb-clauses",
            "name": "KB Clauses",
            "description": "Standard clauses grouped by source standard + edition.",
            "source": src("kb_board_clauses"),
            "columns": [
                {"id": "title", "label": "Clause", "type": "text"},
                {"id": "standard", "label": "Standard", "type": "text"},
                {"id": "edition", "label": "Edition", "type": "text"},
                {"id": "clause", "label": "§", "type": "text"},
            ],
            "views": [
                {"type": "table", "default": True},
                {"type": "kanban", "group_by": "standard"},
            ],
            "kanban_group_by": "standard",
        },
        {
            "id": "kb-engineering-evidence",
            "name": "Engineering Evidence",
            "description": "App methods traced through standards and clauses to "
                           "implemented formulas and verification cases.",
            "source": src("engineering_evidence_rows"),
            "columns": [
                {"id": "app_id", "label": "App", "type": "text"},
                {"id": "method", "label": "Method", "type": "text"},
                {"id": "standard", "label": "Standard", "type": "text"},
                {"id": "clauses", "label": "Clauses", "type": "text"},
                {"id": "formulas", "label": "Formulas", "type": "text"},
                {"id": "verification_cases", "label": "Cases", "type": "text"},
                {"id": "status", "label": "Coverage", "type": "select",
                 "options": ["verified", "unverified", "unresolved"],
                 "color_map": {"verified": "green", "unverified": "amber",
                               "unresolved": "red"}},
            ],
            "views": [
                {"type": "table", "default": True},
                {"type": "kanban", "group_by": "status"},
            ],
            "kanban_group_by": "status",
        },
    ]
