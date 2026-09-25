"""worklog — boards-as-view-layer integration.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns the flat item-row projection consumed by the generic `boards`
app (.claude/rules/boards-as-view-layer.md): SETTABLE_FIELDS, list_all,
set_field, and the manifest-contributed board preset. Reuses the existing
day-note read/write paths (reads.py::_all_days, app.py::set_status) rather
than inventing a second storage model — a board row IS a work item, not a
new record type.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Do not import from ``.app`` (it imports us, which would cycle).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import WorklogApp  # noqa: F401 — for type hints only


# ─── Bind to WorklogApp class as ────────────────────────────────
#   list_all      = _boards.list_all
#   set_field     = _boards.set_field
#   board_presets = _boards.board_presets
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────

# A kanban's value is CURRENT work — items older than this fall out of the
# board rather than making a year of completed history compete visually with
# what's active. `_all_days()` has no pagination, so this window is what
# keeps list_all() bounded.
_BOARD_WINDOW_DAYS = 90
_ID_SEP = "\x1f"  # ASCII unit separator — never typed by hand in a note

SETTABLE_FIELDS = {"status"}


def _item_id(date_s: str, project: str, text: str) -> str:
    return f"{date_s}{_ID_SEP}{project}{_ID_SEP}{text}"


def _parse_item_id(item_id: str) -> tuple[str, str, str] | None:
    parts = (item_id or "").split(_ID_SEP, 2)
    if len(parts) != 3:
        return None
    return parts[0], parts[1], parts[2]


async def list_all(self) -> list[dict]:
    """Flat item-row list consumed by boards (source.type == "app").

    Known v1 limitation: `set_field` routes through `set_status`, which
    matches an item by `(project, item_text)` and flips EVERY match with no
    early break (`app.py::set_status`) — two identical bullets logged under
    the same project on the same day will co-flip from one board click.
    Pre-existing behaviour of `set_status`; not fixed here.
    """
    cutoff = (date.today() - timedelta(days=_BOARD_WINDOW_DAYS)).isoformat()
    rows: list[dict] = []
    for day in await self._all_days():
        if day["date"] < cutoff:
            break
        for g in day["parsed"]["projects"]:
            project = g["project"]
            for it in g["items"]:
                text = it.get("text", "")
                rows.append({
                    "id": _item_id(day["date"], project, text),
                    "date": day["date"],
                    "project": project,
                    "text": text,
                    "status": it.get("status") or "",
                    "employer": day["employer"],
                })
    return rows


async def set_field(self, id: str, field: str, value) -> dict:
    if field not in SETTABLE_FIELDS:
        return {"error": f"field '{field}' not settable", "settable": sorted(SETTABLE_FIELDS)}
    parsed = _parse_item_id(id)
    if not parsed:
        return {"error": f"bad item id '{id}'"}
    date_s, project, text = parsed
    return await self.set_status(date_s, project, text, value)


def board_presets(self) -> list[dict]:
    """Live kanban-by-status view over recent work items. Boards aggregates
    this generically via call_contributions; boards code holds no worklog
    knowledge. Read/write goes through this same SETTABLE_FIELDS-gated
    set_field, so an edit on the board writes the same day note the Work Log
    app itself writes to."""
    return [{
        "id": "worklog-boards",
        "name": "Work Log",
        "description": f"Work items from the last {_BOARD_WINDOW_DAYS} days, by status. "
                        "Edited here or in the Work Log app itself — both write the "
                        "same day note.",
        "source": {"type": "app", "app": "worklog", "method": "list_all"},
        "columns": [
            {"id": "text", "label": "Item", "type": "text"},
            {"id": "project", "label": "Project", "type": "text"},
            {"id": "status", "label": "Status", "type": "select",
             "options": ["todo", "in-progress", "next", "waiting", "review",
                         "blocked", "complete"]},
            {"id": "date", "label": "Date", "type": "text"},
            {"id": "employer", "label": "Employer", "type": "text"},
        ],
        "views": [
            # meta_fields opts the card into showing project + employer
            # instead of the generic positional default, which put "date"
            # in the slot and never reached employer at all.
            # .claude/rules/list-card-density.md
            {"type": "kanban", "group_by": "status", "default": True,
             "meta_fields": ["project", "employer"]},
            {"type": "table"},
        ],
        "kanban_group_by": "status",
    }]
