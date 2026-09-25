"""viz — board preset + the flat listing it reads.

Extracted from app.py to keep the spine atomic (CLAUDE.md rule 4). Owns: the
single `[[contributes.boards.preset]]` entry and its `list_all` source verb.

Every artifact record has carried `tags: [viz]` since day one — written by
`generation._persist` through `vault_create_note` — and nothing ever queried
it. The app lists its own output with `root.iterdir()` instead, so the tag sat
in a vacuum. `list_all` is the first reader.

Why an `app` source and not `{"type": "vault_tag", "tag": "viz"}`, which reads
like the obvious fit: boards only auto-instantiates presets whose source type
is `app` or `mixed` (`boards/app.py` — `if stype not in ("app", "mixed"):
continue`). A `vault_tag` preset is offered in the picker but never created,
so the board would not exist until someone made it by hand. Going through
`list_all` keeps the auto-instantiation *and* still resolves the corpus by
tag, which is the part that was missing.

Read-only on purpose, and the reason is not squeamishness: the artifact IS
`scene.html`. Editing `shape` or `prompt` in a table would rewrite a record's
description of a file without touching the file, i.e. make the record lie.
Iteration belongs in the Viz app, where it regenerates the artifact.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: shared.SHAPE_META for the shape option list.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .shared import SHAPE_META

if TYPE_CHECKING:
    from .app import VizApp  # noqa: F401 — for type hints only


# ─── Bind to VizApp class as ────────────────────────────────
#   list_all      = _boards.list_all
#   board_presets = _boards.board_presets
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────

# Deliberately NO `set_field` and no `SETTABLE_FIELDS` here. The board engine
# only ever calls `set_field`; `SETTABLE_FIELDS` is a convention an app
# enforces inside its own setter, not a protocol the engine reads — so an
# empty whitelist with no setter would be inert, and would imply an editable
# contract this app does not offer. The absent setter IS the guarantee.


async def list_all(self) -> list[dict]:
    """Flat rows for every artifact record, resolved by tag.

    Display fields only: `history` (every iteration prompt) and the full
    `prompt` are deliberately dropped — a board cell is one line, and the
    brief is already the `title`.
    """
    rows: list[dict] = []
    for entry in self.vault_query(tags=["viz"]):
        props = entry.get("properties") or {}
        rid = str(props.get("viz_id") or "").strip()
        if not rid:
            continue  # not an artifact record (a stray note carrying the tag)
        used = props.get("used_in") or []
        rows.append({
            "id": rid,
            "title": props.get("title") or rid,
            "shape": props.get("shape", ""),
            "used_in": ", ".join(u for u in used if isinstance(u, str)),
            "created": props.get("created", ""),
            "updated": props.get("updated", ""),
            "size_kb": props.get("size_kb", 0),
        })
    rows.sort(key=lambda r: str(r.get("created") or ""), reverse=True)
    return rows


def board_presets(self):
    """A live, read-only table over every generated artifact."""
    return [
        {
            "id": "viz-artifacts",
            "name": "Viz artifacts",
            "description": (
                "Every generated artifact, by shape and date. Read-only — the "
                "artifact itself is scene.html; open it in the Viz app to iterate."
            ),
            "source": {"type": "app", "app": "viz", "method": "list_all"},
            "columns": [
                {"id": "title", "label": "Title", "type": "text"},
                {
                    "id": "shape", "label": "Shape", "type": "select",
                    "options": sorted(SHAPE_META),
                },
                {"id": "used_in", "label": "Used in", "type": "text"},
                {"id": "created", "label": "Created", "type": "date"},
                {"id": "size_kb", "label": "Size (KB)", "type": "number"},
            ],
            "views": [
                {"type": "table", "default": True},
                {"type": "kanban", "group_by": "shape"},
            ],
            "kanban_group_by": "shape",
        }
    ]
