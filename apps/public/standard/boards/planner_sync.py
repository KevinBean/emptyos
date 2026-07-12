"""Boards — Microsoft Planner import (canonical-records apply route).

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: the bulk-apply endpoint the Planner import wizard POSTs to.
The browser parses the .xlsx (SheetJS via pages/planner-xlsx.js — the ONE
parse implementation, shared by live + standalone modes) and sends canonical
JSON records; this route upserts them into a vault_tag board by
``planner_id``, creates-or-matches assignees against the people app, and
persists the confirmed mapping on the board config for one-click re-imports.

Board columns are generated from ``pages/planner-map.js`` (strict JSON behind
a one-line wrapper — parsed here with json.loads), so Python and JS can never
drift on the column layout. No preset duplication.

Deliberate scope notes:
- Bulk import bypasses per-item guard rules/automations (50 imported rows
  firing automations would spam); it emits one ``board:planner_imported``.
- ``body`` (Planner Description) is written on create only — updating an
  existing note's body from a re-import would clobber local edits.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``self._create_item_from_fields``,
``self._log_activity``, ``self._emit_assignment_deltas`` through the class.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import web_route
from emptyos.sdk.column_types import ColumnTypeRegistry

if TYPE_CHECKING:
    from .app import BoardsApp  # noqa: F401 — for type hints only


# ─── Bind to BoardsApp class as ──────────────────────────────────────
#   api_planner_apply  = _planner_sync.api_planner_apply
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────

_MAP_WRAPPER = "window.EOS_PLANNER_MAP ="


def _load_planner_map() -> dict:
    """Parse pages/planner-map.js — strict JSON behind the one-line wrapper
    (pinned by tests/test_planner_roundtrip.py)."""
    text = (Path(__file__).parent / "pages" / "planner-map.js").read_text(encoding="utf-8")
    body = text[text.index(_MAP_WRAPPER) + len(_MAP_WRAPPER):].strip()
    return json.loads(body.rstrip(";"))


def _columns_from_map(pmap: dict, mapped_fields: list[str]) -> list[dict]:
    """Board columns for the mapped fields, in export order, planner_id +
    name always first (planner_id is the upsert key)."""
    fields = pmap.get("fields", {})
    order = pmap.get("export_order", list(fields))
    want = set(mapped_fields) | {"planner_id", "task_name"}
    cols: list[dict] = []
    for f in order:
        spec = fields.get(f) or {}
        if f not in want or spec.get("meta") or spec.get("body") or not spec.get("col"):
            continue
        cols.append(dict(spec["col"]))
    return cols


def _norm_name(s) -> str:
    return str(s or "").strip().lower().replace("-", " ").replace("_", " ")


def _val_cmp(v):
    if v is None:
        return ""
    if isinstance(v, list):
        return json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)
    return str(v)


@web_route("POST", "/api/boards/{id}/planner/apply")
async def api_planner_apply(self, request):
    """Upsert canonical Planner records into a vault_tag board.

    Body: {records: [...], mapping: {field: headerIndex}, options: {
        create_board: bool, board_name: str?, create_people: bool,
        plan_name: str?}}
    Returns {ok, created, updated, unchanged, people_created, board}.
    """
    board_id = request.path_params.get("id", "")
    data = await request.json()
    records = data.get("records") or []
    mapping = data.get("mapping") or {}
    options = data.get("options") or {}
    if not isinstance(records, list) or not records:
        return {"error": "no records"}

    pmap = _load_planner_map()
    from .board_engine import DynamicBoardLibrary

    # ── Resolve or create the target board ──
    config = self._store.get_board(board_id)
    created_board = False
    if not config:
        if not options.get("create_board"):
            return {"error": "Board not found (pass options.create_board to create it)"}
        config = {
            "id": board_id,
            "name": options.get("board_name") or options.get("plan_name") or board_id,
            "description": "Imported from Planner",
            "source_tag": board_id,
            "tags": ["board-config"],
            "columns": _columns_from_map(pmap, list(mapping.keys())),
            "views": [
                {"type": "table", "default": True},
                {"type": "kanban", "group_by": "bucket"},
                {"type": "calendar", "date_field": "due_date"},
            ],
            "kanban_group_by": "bucket",
        }
        created_board = True
    else:
        if (config.get("source") or {}).get("type") in ("app", "mixed"):
            return {"error": "cannot import into an app-sourced board"}
        # Merge any mapped column the board doesn't have yet.
        have = {c["id"] for c in config.get("columns", [])}
        for col in _columns_from_map(pmap, list(mapping.keys())):
            if col["id"] not in have:
                config.setdefault("columns", []).append(col)

    cols_by_id = {c["id"]: c for c in config.get("columns", [])}

    # ── Merge newly-seen bucket/label options into select columns ──
    for col_id, values in (
        ("bucket", {r.get("bucket") for r in records if r.get("bucket")}),
        ("labels", {l for r in records for l in (r.get("labels") or [])}),
    ):
        col = cols_by_id.get(col_id)
        if col is None:
            continue
        opts = list(col.get("options") or [])
        for v in sorted(values):
            if v not in opts:
                opts.append(v)
        col["options"] = opts

    # ── People create-or-match (assigned names → person ids) ──
    people_created: list[str] = []
    name_to_id: dict[str, str] = {}
    people_available = False
    try:
        roster = await self.call_app("people", "list_all")
        people_available = True
        for p in roster or []:
            name_to_id[_norm_name(p.get("name"))] = p.get("id", "")
            name_to_id[_norm_name(p.get("id"))] = p.get("id", "")
    except Exception:
        pass  # people app absent — keep raw names

    async def _resolve_person(name: str) -> str:
        key = _norm_name(name)
        if key in name_to_id and name_to_id[key]:
            return name_to_id[key]
        if people_available and options.get("create_people"):
            try:
                r = await self.call_app("people", "create_person", name=name)
                pid = (r or {}).get("id")
                if pid:
                    name_to_id[key] = pid
                    people_created.append(name)
                    return pid
            except Exception:
                pass
        return name  # raw name fallback

    # ── Index existing items by planner_id / name ──
    lib = DynamicBoardLibrary(self, config)
    try:
        existing_items = lib.list_filtered()
    except Exception:
        existing_items = []
    by_pid = {str(it.get("planner_id")): it for it in existing_items if it.get("planner_id")}
    by_name = {}
    for it in existing_items:
        k = _norm_name(it.get("name"))
        if k and k not in by_name:
            by_name[k] = it

    def _coerce(col_id: str, value):
        col = cols_by_id.get(col_id)
        if not col:
            return value
        return ColumnTypeRegistry.get(col.get("type") or "text").coerce(value, col)

    created = updated = unchanged = 0
    for rec in records:
        body = rec.pop("body", "") if isinstance(rec, dict) else ""
        if not isinstance(rec, dict) or not (rec.get("name") or rec.get("planner_id")):
            continue
        if rec.get("assigned"):
            rec["assigned"] = [await _resolve_person(n) for n in rec["assigned"]]

        existing = by_pid.get(str(rec.get("planner_id") or "")) or by_name.get(_norm_name(rec.get("name")))
        if existing is None:
            fields = {k: v for k, v in rec.items() if k in cols_by_id or k in ("name",)}
            fields["body"] = body
            res = await self._create_item_from_fields(board_id, config, fields)
            if res.get("ok"):
                created += 1
            continue

        key = existing.get("file") or existing.get("id")
        changes = {}
        for k, v in rec.items():
            if k not in cols_by_id:
                continue
            old = existing.get(k)
            if cols_by_id[k].get("type") == "checklist":
                # Normalize both sides to list-of-dicts for comparison.
                def _cl(x):
                    if isinstance(x, str):
                        try:
                            x = json.loads(x)
                        except Exception:
                            x = []
                    return x if isinstance(x, list) else []
                if _val_cmp(_cl(old)) == _val_cmp(_cl(v)):
                    continue
            elif _val_cmp(old) == _val_cmp(v):
                continue
            changes[k] = v
        if not changes:
            unchanged += 1
            continue
        ok = True
        for f, v in changes.items():
            r = await lib.set_field(key, f, _coerce(f, v))
            ok = ok and bool(r.get("ok", False))
        if ok:
            updated += 1
            new_item = {**existing, **changes}
            self._log_activity(
                board_id, key, "board:item_updated",
                actor="planner-import",
                updates={f: {"old": existing.get(f), "new": v} for f, v in changes.items()},
            )
            try:
                await self._emit_assignment_deltas(
                    board_id, config, key, existing, new_item, changes
                )
            except Exception:
                pass

    # ── Persist mapping + import stamp on the board config ──
    config["planner"] = {
        "plan_name": options.get("plan_name", ""),
        "mapping": mapping,
        "last_import": datetime.now().isoformat(timespec="seconds"),
    }
    self._store.save_board(board_id, config)
    if created_board:
        await self.emit("board:created", {"id": board_id, "name": config.get("name")})

    await self.emit(
        "board:planner_imported",
        {
            "board": board_id,
            "created": created,
            "updated": updated,
            "unchanged": unchanged,
            "people_created": len(people_created),
        },
    )
    return {
        "ok": True,
        "board": board_id,
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "people_created": people_created,
    }
