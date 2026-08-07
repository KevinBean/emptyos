"""boards — board items — create, update, archive, and the rules that guard them.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: Everything about a row inside a board: listing, stats, source-status for app-backed boards, smart-add parsing plus its field validation, the update path with its dependency-cycle check and date shifting, and the people-workload assignment deltas.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._lib / self._config_for (spine) for the board's library and column config; self._maintain_link_inverses (links) when an update touches a relation column.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from datetime import date
from .automation import evaluate_guards, evaluate_rules
from .board_engine import DynamicBoardLibrary, evaluate_formulas
from .presets import get_preset
from typing import TYPE_CHECKING

from emptyos.sdk import web_route

if TYPE_CHECKING:
    from .app import BoardsApp  # noqa: F401 — for type hints only



SMART_ADD_SYSTEM = """You convert ONE line of natural language into structured fields for a board item.

You are given the board's columns (id, label, type, and — for select columns — the allowed options) plus a single free-text description. Extract a JSON object mapping column id -> value.

Rules:
- Output ONLY a JSON object. No prose, no markdown fences, no explanation.
- Use ONLY column ids that appear in the schema. NEVER invent a field id.
- For `select` columns the value MUST be one of the given options verbatim — map the user's intent to the closest option; if nothing fits, omit the field.
- For `number` columns output a bare number (no units, no currency symbol).
- For `date` columns output ISO `YYYY-MM-DD`. Resolve relative dates ("Friday", "next week", "tomorrow") against the provided today's date.
- For `checkbox` output true or false.
- Always fill the first/title column with a concise title derived from the text.
- OMIT any field you are not confident about — a missing field is better than a wrong one.
- Never include tags, created, or any other system field.

Example — columns [{"id":"title","type":"text"},{"id":"severity","type":"select","options":["Low","High","Critical"]},{"id":"due","type":"date"}], today 2026-07-04, description "fix the login crash, critical, due friday" -> {"title":"Fix the login crash","severity":"Critical","due":"2026-07-10"}
"""

# ─── Bind to BoardsApp class as ────────────────────────────────
#   api_get_items             = _items.api_get_items
#   api_source_status         = _items.api_source_status
#   api_get_stats             = _items.api_get_stats
#   api_create_item           = _items.api_create_item
#   api_smart_add             = _items.api_smart_add
#   _validate_smart_fields    = _items._validate_smart_fields
#   _create_item_from_fields  = _items._create_item_from_fields
#   api_update_item           = _items.api_update_item
#   _detect_cycle_on_update   = _items._detect_cycle_on_update
#   _cached_items_sync        = _items._cached_items_sync
#   shift_item_date           = _items.shift_item_date
#   _emit_assignment_deltas   = _items._emit_assignment_deltas
#   api_get_item              = _items.api_get_item
#   api_archive_item          = _items.api_archive_item
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


@web_route("GET", "/api/boards/{id}/items")
async def api_get_items(self, request):
    """Query items for a board with optional filtering and sorting."""
    board_id = request.path_params.get("id", "")
    config = self._store.get_board(board_id) or get_preset(board_id)
    if not config:
        return []

    lib = DynamicBoardLibrary(self, config)

    # Parse query params
    sort_by = request.query_params.get("sort", "")
    sort_desc = request.query_params.get("desc", "false") == "true"
    filters = {}
    for col in config.get("columns", []):
        val = request.query_params.get(col["id"])
        if val:
            filters[col["id"]] = val

    # Source-aware fetch first (may call another app), then filter+sort in-memory.
    raw = await lib.get_items()
    items = lib.list_filtered(filters=filters, sort_by=sort_by, sort_desc=sort_desc, items=raw)
    # Formula pass — async so link-record columns can be resolved into
    # target-board items for attribute-walking (e.g. SUM(children.hours)).
    # Overrides single-item formula values set by list_filtered.
    items = await evaluate_formulas(self, config, items)
    return items


@web_route("GET", "/api/boards/{id}/source-status")
async def api_source_status(self, request):
    """Source-app health for an app-sourced board. Returns ok=true for
    vault_tag boards or when the source app responds. Returns ok=false
    with a human-readable error when the source app is uninstalled or
    failed to load — the frontend uses this to render a banner instead
    of leaving the user staring at an empty board with no explanation."""
    board_id = request.path_params.get("id", "")
    config = self._store.get_board(board_id) or get_preset(board_id)
    if not config:
        return {"ok": False, "error": "board not found"}
    src = config.get("source") or {"type": "vault_tag"}
    if src.get("type") != "app":
        return {"ok": True, "type": src.get("type", "vault_tag")}
    lib = DynamicBoardLibrary(self, config)
    await lib.get_items()  # populates lib._source_error if missing
    if lib._source_error:
        return {
            "ok": False,
            "type": "app",
            "app": src.get("app", ""),
            "error": lib._source_error,
        }
    return {"ok": True, "type": "app", "app": src.get("app", "")}


@web_route("GET", "/api/boards/{id}/stats")
async def api_get_stats(self, request):
    """Aggregated stats for chart/dashboard views."""
    board_id = request.path_params.get("id", "")
    config = self._store.get_board(board_id) or get_preset(board_id)
    if not config:
        return {"error": "Board not found"}

    lib = DynamicBoardLibrary(self, config)
    group_by = request.query_params.get("group_by", config.get("kanban_group_by", "status"))
    agg_field = request.query_params.get("agg_field", "")
    agg_fn = request.query_params.get("agg_fn", "count")

    raw = await lib.get_items()
    return lib.aggregate(group_by=group_by, agg_field=agg_field, agg_fn=agg_fn, items=raw)


@web_route("POST", "/api/boards/{id}/items")
async def api_create_item(self, request):
    """Create a new vault note for a board."""
    board_id = request.path_params.get("id", "")
    data = await request.json()
    config = self._store.get_board(board_id) or get_preset(board_id)
    if not config:
        return {"error": "Board not found"}
    return await self._create_item_from_fields(board_id, config, data)


@web_route("POST", "/api/boards/{id}/items/smart-add")
async def api_smart_add(self, request):
    """Parse one natural-language line into board fields — does NOT create.

    Body ``{text}``. Returns ``{ok, fields}`` so the frontend can prefill the
    add form for the user to review before committing (propose-not-autofill).
    Only the user's typed text + the board's column schema reach the model —
    no vault content (Rule 19)."""
    import json as _json

    board_id = request.path_params.get("id", "")
    data = await request.json()
    text = (data.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "empty text"}
    config = self._store.get_board(board_id) or get_preset(board_id)
    if not config:
        return {"ok": False, "error": "Board not found"}
    schema = [
        {k: c.get(k) for k in ("id", "label", "type", "options") if c.get(k) is not None}
        for c in (config.get("columns") or [])
        if c.get("type") != "formula"
    ]
    user_msg = (
        f"Today is {date.today().isoformat()}.\n"
        f"Columns: {_json.dumps(schema)}\n"
        f"Description: {text}"
    )
    raw = await self.think(
        user_msg, system=SMART_ADD_SYSTEM, domain="text", temperature=0.2
    )
    from emptyos.sdk.utils import parse_llm_json

    parsed = parse_llm_json(raw, fallback={})
    if not isinstance(parsed, dict):
        return {"ok": False, "error": "could not parse"}
    return {
        "ok": True,
        "fields": self._validate_smart_fields(config, parsed),
        "provenance": self.last_provenance(),
    }


def _validate_smart_fields(self, config: dict, parsed: dict) -> dict:
    """Keep only known columns, constrain select values to their options,
    coerce numbers/checkboxes. A wrong field is worse than a missing one."""
    out: dict = {}
    by_id = {c["id"]: c for c in (config.get("columns") or [])}
    for key, val in parsed.items():
        col = by_id.get(key)
        if not col or col.get("type") == "formula":
            continue
        if val is None or val == "":
            continue
        ctype = col.get("type") or "text"
        if ctype == "select":
            opts = col.get("options") or []
            if val in opts:
                out[key] = val
            else:
                match = next((o for o in opts if str(o).lower() == str(val).lower()), None)
                if match is not None:
                    out[key] = match
        elif ctype == "number":
            try:
                out[key] = float(val)
            except (TypeError, ValueError):
                pass
        elif ctype == "checkbox":
            out[key] = val if isinstance(val, bool) else str(val).lower() in ("true", "yes", "1")
        else:
            out[key] = val
    return out


async def _create_item_from_fields(self, board_id: str, config: dict, data: dict) -> dict:
    """Shared create path — the add-item POST and the Planner importer both
    land here so slug/frontmatter/emit/automation logic stays single-source."""
    source_tag = config.get("source_tag", board_id)
    # Build frontmatter from provided fields (per-type write coercion —
    # pass-through for most types; checklist JSON-encodes its list).
    from emptyos.sdk.column_types import ColumnTypeRegistry

    fm = {"tags": [source_tag], "created": date.today().isoformat()}
    for col in config.get("columns", []):
        col_id = col["id"]
        if col_id in data:
            ctype = ColumnTypeRegistry.get(col.get("type") or "text")
            fm[col_id] = ctype.coerce(data[col_id], col)
        elif col.get("type") == "select" and col.get("options"):
            fm[col_id] = col["options"][0]  # Default to first option

    # Determine filename
    name_field = data.get("name") or data.get("title") or f"item-{date.today().isoformat()}"
    slug = name_field.lower().replace(" ", "-")
    slug = "".join(c for c in slug if c.isalnum() or c == "-")

    # Create vault note
    vault_dir = self.vault_config(
        "boards_items_dir", f"30_Resources/EmptyOS/boards-data/{source_tag}"
    )
    rel_path = f"{vault_dir}/{slug}.md"
    body = data.get("body", "")
    self.vault_create_note(rel_path, fm, body)

    await self.emit("board:item_created", {"board": board_id, "file": f"{slug}.md"})
    self._log_activity(
        board_id,
        f"{slug}.md",
        "board:item_created",
        updates={k: {"old": None, "new": v} for k, v in (data or {}).items()},
    )

    # Run automations for item_created
    new_item = {**fm, "file": f"{slug}.md", "path": rel_path}
    await evaluate_rules(self, config, None, new_item, event_type="item_created")

    # Emit assignment deltas for any person-family column that was populated
    # at creation time. Old item is empty (new item) so everything is added.
    initial_updates = {k: new_item.get(k) for k in (data or {})}
    await self._emit_assignment_deltas(
        board_id, config, f"{slug}.md", {}, new_item, initial_updates
    )
    # Populate link-record edges (and any declared inverses) for this new item.
    await self._maintain_link_inverses(
        board_id, config, f"{slug}.md", {}, new_item, initial_updates
    )

    return {"ok": True, "file": f"{slug}.md", "path": rel_path}


@web_route("PATCH", "/api/boards/{id}/items/{file}")
async def api_update_item(self, request):
    """Update an item's frontmatter fields (inline edit)."""
    board_id = request.path_params.get("id", "")
    filename = request.path_params.get("file", "")
    data = await request.json()
    updates = data.get("updates", data)  # Accept flat dict or {updates: {...}}

    config = self._store.get_board(board_id) or get_preset(board_id)
    if not config:
        return {"error": "Board not found"}
    lib = DynamicBoardLibrary(self, config)

    # Get old state — source-aware (vault read OR call_app to source app).
    old_item = await lib.get_detail(filename)
    if not old_item:
        return {"error": "Item not found"}

    # Only update declared columns
    valid_cols = {col["id"]: col for col in config.get("columns", [])}
    safe_updates = {k: v for k, v in updates.items() if k in valid_cols}

    if not safe_updates:
        return {"error": "No valid fields to update"}

    # Per-type write coercion (registry hook — pass-through for most
    # types; checklist JSON-encodes its list for flat frontmatter).
    # App-sourced items skip it: their set_field takes JSON-native values.
    if (config.get("source") or {}).get("type") not in ("app", "mixed"):
        from emptyos.sdk.column_types import ColumnTypeRegistry

        safe_updates = {
            k: ColumnTypeRegistry.get(valid_cols[k].get("type") or "text").coerce(
                v, valid_cols[k]
            )
            for k, v in safe_updates.items()
        }

    # Pre-commit: evaluate any `kind=guard` rules. If a guard blocks, the
    # PATCH never touches disk — 409 with a human message + machine hook.
    guard_block = await evaluate_guards(self, config, old_item, safe_updates)
    if guard_block:
        return guard_block

    # Dependency cycle guard: if `blocks` or `blocked_by` is being updated,
    # make sure the new graph stays acyclic.
    cycle = self._detect_cycle_on_update(config, filename, old_item, safe_updates)
    if cycle:
        return {
            "error": "dependency_cycle",
            "message": f"would create a cycle: {' → '.join(cycle)}",
        }

    # Route each update through the source-aware setter. Multi-field
    # PATCHes become multiple single-field writes; both vault_tag and app
    # sources handle one field at a time (matches projects.set_field).
    result = {"ok": True, "writes": {}}
    for f, v in safe_updates.items():
        r = await lib.set_field(filename, f, v)
        result["writes"][f] = r
        if not r.get("ok", False):
            result["ok"] = False

    if result.get("ok"):
        new_item = await lib.get_detail(filename) or {**old_item, **safe_updates}
        self._log_activity(
            board_id,
            filename,
            "board:item_updated",
            updates={
                f: {"old": old_item.get(f), "new": new_item.get(f, v)}
                for f, v in safe_updates.items()
            },
        )
        await self.emit(
            "board:item_updated",
            {
                "board": board_id,
                "file": filename,
                "old": old_item,
                "new": new_item,
                "updates": safe_updates,
            },
        )
        # Column-move signal: when the kanban group-by field changes (a card
        # dragged across columns), emit the manifest-declared board:item_moved
        # so listeners can react to workflow transitions specifically.
        group_by = config.get("kanban_group_by")
        if group_by and group_by in safe_updates:
            await self.emit(
                "board:item_moved",
                {
                    "board": board_id,
                    "file": filename,
                    "field": group_by,
                    "from": old_item.get(group_by),
                    "to": new_item.get(group_by, safe_updates[group_by]),
                },
            )
        # Emit assignment deltas for any person-family column that changed.
        await self._emit_assignment_deltas(
            board_id, config, filename, old_item, new_item, safe_updates
        )
        # Maintain link-record edges + inverse fields on target items.
        await self._maintain_link_inverses(
            board_id, config, filename, old_item, new_item, safe_updates
        )
        # Compute slip delta for any date-field that moved forward so
        # propagate_slip can use it (it runs as part of evaluate_rules).
        slip_days = 0
        from datetime import date as _d

        for f, v in safe_updates.items():
            col = next((c for c in config.get("columns", []) if c["id"] == f), None)
            if not col or col.get("type") != "date":
                continue
            try:
                old_d = _d.fromisoformat(str(old_item.get(f, ""))[:10])
                new_d = _d.fromisoformat(str(v)[:10])
                delta = (new_d - old_d).days
                if delta > slip_days:
                    slip_days = delta
            except ValueError:
                pass
        new_item["_slip_days"] = slip_days
        new_item["_board_id"] = board_id
        # Run automations
        await evaluate_rules(self, config, old_item, new_item, event_type="field_changed")

    return result


def _detect_cycle_on_update(self, config, item_id, old_item, updates):
    """Return the cycle path (list of ids) if these updates would create a
    cycle in the blocks/blocked_by graph, or None if clean. O(V+E)."""
    if "blocks" not in updates and "blocked_by" not in updates:
        return None

    # Build graph from the board's current items, overlaying the proposed updates.
    lib = DynamicBoardLibrary(self, config)
    try:
        items = self._cached_items_sync(lib)
    except Exception:
        return None
    graph: dict[str, list[str]] = {}
    for it in items:
        iid = it.get("id") or it.get("file")
        if not iid:
            continue
        graph[iid] = list(it.get("blocks") or [])
    # Overlay the update on the edited item.
    projected_blocks = updates.get("blocks")
    if projected_blocks is not None:
        if isinstance(projected_blocks, str):
            projected_blocks = [s.strip() for s in projected_blocks.split(",") if s.strip()]
        graph[item_id] = list(projected_blocks)
    # If blocked_by was updated, convert to edges from-those items toward item_id.
    projected_blocked_by = updates.get("blocked_by")
    if projected_blocked_by is not None:
        if isinstance(projected_blocked_by, str):
            projected_blocked_by = [
                s.strip() for s in projected_blocked_by.split(",") if s.strip()
            ]
        # Wipe old inbound edges that targeted item_id (from old_item's blocked_by),
        # then add the projected ones.
        old_inbound = set((old_item or {}).get("blocked_by") or [])
        for src in old_inbound:
            graph[src] = [x for x in graph.get(src, []) if x != item_id]
        for src in projected_blocked_by:
            graph.setdefault(src, []).append(item_id)

    # DFS for a cycle reachable from item_id.
    path: list[str] = []
    visiting: set[str] = set()

    def dfs(node: str) -> list[str] | None:
        if node in visiting:
            # Cycle found — return the path from where we re-entered.
            idx = path.index(node) if node in path else 0
            return path[idx:] + [node]
        visiting.add(node)
        path.append(node)
        for nxt in graph.get(node, []):
            hit = dfs(nxt)
            if hit:
                return hit
        path.pop()
        visiting.discard(node)
        return None

    return dfs(item_id)


def _cached_items_sync(self, lib):
    """Best-effort sync fetch of items for cycle detection.

    Vault-tag boards support this directly; app-sourced boards would need
    an async fetch, which we skip here (cycle detection only applies to
    vault_tag boards that own their items). App-sourced boards' cycles
    would surface at the source app's write path."""
    if lib._source.get("type") != "vault_tag":
        return []
    return lib.list()


async def shift_item_date(
    self, board_id: str, item_id: str, field: str, delta_days: int
) -> dict:
    """Propagate-slip helper: move one item's date field forward by
    `delta_days`. Called by the auto-slip automation action. Idempotent
    at the frontmatter level — worst case a double-fire nudges twice."""
    from datetime import date, timedelta

    config = self._store.get_board(board_id) or get_preset(board_id)
    if not config:
        return {"error": "board not found"}
    lib = DynamicBoardLibrary(self, config)
    item = await lib.get_detail(item_id)
    if not item:
        return {"error": "item not found"}
    cur = str(item.get(field, "") or "")[:10]
    try:
        new_dt = date.fromisoformat(cur) + timedelta(days=delta_days)
    except ValueError:
        return {"error": f"field '{field}' is not a date: {cur!r}"}
    return await lib.set_field(item_id, field, new_dt.isoformat())


async def _emit_assignment_deltas(
    self, board_id, config, filename, old_item, new_item, updates
):
    """For every person-family column that changed in this update, emit the
    assignment / unassignment deltas so the people app's workload index
    stays in sync."""
    from .board_engine import PERSON_MULTI_TYPES, PERSON_SINGLE_TYPES, ROLE_FOR_TYPE

    # Build item descriptor once.
    name_col = (config.get("columns") or [{}])[0].get("id", "name")
    item_desc = {
        "app": "boards",
        "board": board_id,
        "id": filename,
        "title": new_item.get(name_col) or new_item.get("title") or filename,
    }

    for col in config.get("columns", []):
        if col["id"] not in updates:
            continue
        ctype = col.get("type")
        role = ROLE_FOR_TYPE.get(ctype) or col["id"]
        old_val = old_item.get(col["id"])
        new_val = new_item.get(col["id"])

        if ctype in PERSON_SINGLE_TYPES:
            old_ids = {old_val} if old_val else set()
            new_ids = {new_val} if new_val else set()
        elif ctype in PERSON_MULTI_TYPES:
            old_ids = (
                set(old_val or [])
                if isinstance(old_val, list)
                else ({old_val} if old_val else set())
            )
            new_ids = (
                set(new_val or [])
                if isinstance(new_val, list)
                else ({new_val} if new_val else set())
            )
        else:
            continue

        added = new_ids - old_ids
        removed = old_ids - new_ids
        weight = float(col.get("weight_hours", 5.0))
        for pid in added:
            await self.emit_assignment(
                pid, item_desc, weight_hours=weight, role=role, assigned=True
            )
        for pid in removed:
            await self.emit_assignment(
                pid, item_desc, weight_hours=weight, role=role, assigned=False
            )


@web_route("GET", "/api/boards/{id}/items/{file}")
async def api_get_item(self, request):
    """Return a single item's full data (for the detail slide-out)."""
    board_id = request.path_params.get("id", "")
    filename = request.path_params.get("file", "")
    config = self._store.get_board(board_id) or get_preset(board_id)
    if not config:
        return {"error": "Board not found"}
    lib = DynamicBoardLibrary(self, config)
    item = await lib.get_detail(filename)
    if not item:
        return {"error": "Item not found"}
    return item


@web_route("DELETE", "/api/boards/{id}/items/{file}")
async def api_archive_item(self, request):
    """Archive an item (update status to 'Archived')."""
    board_id = request.path_params.get("id", "")
    filename = request.path_params.get("file", "")
    config = self._store.get_board(board_id) or get_preset(board_id)
    if not config:
        return {"error": "Board not found"}
    lib = DynamicBoardLibrary(self, config)
    old_item = await lib.get_detail(filename)
    result = await lib.set_field(filename, "status", "Archived")

    if result.get("ok"):
        await self.emit("board:item_archived", {"board": board_id, "file": filename})
        self._log_activity(board_id, filename, "board:item_archived")
        if old_item:
            await evaluate_rules(
                self,
                config,
                old_item,
                {**old_item, "status": "Archived"},
                event_type="item_archived",
            )

    return result
