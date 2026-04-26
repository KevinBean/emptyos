"""Boards — Visual Work OS backed by Markdown.

Provides dynamic boards (views over vault notes), customizable column schemas,
multi-view rendering (table, kanban, calendar, timeline, chart), automation rules,
built-in presets, and a completely offline SSG export via AppExporter.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from emptyos.sdk import BaseApp, web_route
from emptyos.sdk.exporter import AppExporter

from .automation import evaluate_rules, evaluate_guards
from .board_engine import BoardConfigStore, DynamicBoardLibrary, evaluate_formulas
from .link_index import LinkIndex, _as_id_list
from .presets import PRESETS, get_preset, list_presets
from .views import ViewStore


def _link_record_columns(config: dict) -> list[dict]:
    """Return all link-record columns on a board config, in declaration order."""
    return [c for c in (config.get("columns") or []) if c.get("type") == "link-record"]


def _validate_columns(columns: list) -> tuple[list[dict] | None, str | None]:
    """Check column array for shape correctness before persisting.

    Returns (clean_columns, None) on success, or (None, error_message).
    Normalizes link-record defaults and drops internal keys.
    """
    from emptyos.sdk.column_types import ColumnTypeRegistry

    if not isinstance(columns, list):
        return None, "columns must be a list"

    seen: set[str] = set()
    clean: list[dict] = []
    for i, col in enumerate(columns):
        if not isinstance(col, dict):
            return None, f"column {i} is not an object"
        cid = (col.get("id") or "").strip()
        if not cid:
            return None, f"column {i} missing 'id'"
        if cid in seen:
            return None, f"duplicate column id: {cid!r}"
        seen.add(cid)
        ctype = col.get("type") or "text"
        if not ColumnTypeRegistry.has(ctype):
            return None, f"unknown column type: {ctype!r} (column {cid!r})"
        if ctype == "link-record":
            if not col.get("target_board"):
                return None, f"link-record column {cid!r} requires 'target_board'"
            col.setdefault("multi", False)
        if ctype == "select" or ctype == "multi-select":
            opts = col.get("options") or []
            if not isinstance(opts, list):
                return None, f"column {cid!r} 'options' must be a list"
        clean.append({k: v for k, v in col.items() if not k.startswith("_")})
    return clean, None


class BoardsApp(BaseApp):

    async def setup(self):
        await super().setup()
        self._store = BoardConfigStore(self)
        self._links = LinkIndex()
        self._views = ViewStore(self.data_dir / "views")
        # Auto-instantiate app-sourced presets as saved boards so they appear
        # in the sidebar without the user clicking through templates. These are
        # system-database views — marked readonly so edits route through the
        # source app. Existing saved boards get the readonly flag stamped in
        # idempotently (preserves any user-added columns/views).
        for pid, preset in PRESETS.items():
            src = preset.get("source") or {}
            if src.get("type") != "app":
                continue
            existing = self._store.get_board(pid)
            if existing:
                if not existing.get("readonly") or not existing.get("source_app_id"):
                    existing["readonly"] = True
                    existing["source_app_id"] = src.get("app", "")
                    self._store.save_board(pid, existing)
                continue
            cfg = dict(preset)
            cfg["readonly"] = True
            cfg["source_app_id"] = src.get("app", "")
            self._store.save_board(pid, cfg)
        # Defer initial rebuild to the next tick — other apps may still be
        # loading, and the rebuild hits their list_all() methods via source=app.
        import asyncio
        asyncio.create_task(self._rebuild_links())

    # ------------------------------------------------------------------
    # Board CRUD
    # ------------------------------------------------------------------

    @web_route("GET", "/api/boards")
    async def api_list_boards(self, request):
        """List all board configs + available presets."""
        boards = self._store.list_boards()
        return {"boards": boards, "presets": list_presets()}

    @web_route("POST", "/api/boards")
    async def api_create_board(self, request):
        """Create a new board — from preset or custom config."""
        data = await request.json()
        preset_id = data.get("preset", "")
        board_id = data.get("id", "")
        name = data.get("name", "")

        if preset_id:
            config = get_preset(preset_id)
            if not config:
                return {"error": f"Unknown preset: {preset_id}"}
            config = dict(config)  # copy
            if board_id:
                config["id"] = board_id
            if name:
                config["name"] = name
        else:
            if not board_id:
                return {"error": "Board ID is required"}
            config = {
                "id": board_id,
                "name": name or board_id.replace("-", " ").title(),
                "description": data.get("description", ""),
                "source_tag": data.get("source_tag", board_id),
                "tags": ["board-config"],
                "columns": data.get("columns", [
                    {"id": "name", "label": "Name", "type": "text"},
                    {"id": "status", "label": "Status", "type": "select",
                     "options": ["To Do", "In Progress", "Done"]},
                ]),
                "views": data.get("views", [
                    {"type": "table", "default": True},
                    {"type": "kanban", "group_by": "status"},
                ]),
                "kanban_group_by": "status",
            }

        self._store.save_board(config["id"], config)
        await self.emit("board:created", {"id": config["id"], "name": config["name"]})
        return {"ok": True, "id": config["id"], "name": config["name"]}

    @web_route("POST", "/api/boards/from-preset")
    async def api_from_preset(self, request):
        """Idempotent: ensure a board exists for the named preset and return its
        id. If a board already lives at the preset id, returns it untouched. Used
        by the "Open as Board" buttons in tasks/projects pages so the link is
        safe to click many times.
        """
        data = await request.json()
        preset_id = data.get("preset_id") or data.get("preset") or ""
        if not preset_id:
            return {"error": "preset_id is required"}
        config = get_preset(preset_id)
        if not config:
            return {"error": f"Unknown preset: {preset_id}"}
        target_id = data.get("id") or preset_id
        existing = self._store.get_board(target_id)
        if existing:
            return {"ok": True, "id": target_id, "name": existing.get("name", target_id),
                    "created": False}
        config = dict(config)
        config["id"] = target_id
        self._store.save_board(target_id, config)
        await self.emit("board:created", {"id": target_id, "name": config.get("name", target_id)})
        return {"ok": True, "id": target_id, "name": config.get("name", target_id),
                "created": True}

    @web_route("GET", "/api/boards/{id}")
    async def api_get_board(self, request):
        """Get a full board config."""
        board_id = request.path_params.get("id", "")
        config = self._store.get_board(board_id)
        if not config:
            # Check if it's a preset ID
            preset = get_preset(board_id)
            if preset:
                return preset
            return {"error": "Board not found"}
        # Strip internal keys for response
        return {k: v for k, v in config.items() if not k.startswith("_")}

    @web_route("PATCH", "/api/boards/{id}")
    async def api_update_board(self, request):
        """Update board config (columns, views, rules, etc.)."""
        board_id = request.path_params.get("id", "")
        data = await request.json()
        config = self._store.get_board(board_id)
        if not config:
            return {"error": "Board not found"}
        if "columns" in data:
            cleaned, err = _validate_columns(data["columns"])
            if err:
                return {"error": err}
            config["columns"] = cleaned
        for key in ("name", "description", "views", "kanban_group_by", "rules", "readonly"):
            if key in data:
                config[key] = data[key]
        self._store.save_board(board_id, config)
        await self.emit("board:config_updated", {"id": board_id})
        return {"ok": True}

    # ── Column-level editing ─────────────────────────────────────────

    @web_route("POST", "/api/boards/{id}/columns")
    async def api_add_column(self, request):
        """Append a column to a board config."""
        board_id = request.path_params.get("id", "")
        col = await request.json()
        config = self._store.get_board(board_id)
        if not config:
            return {"error": "Board not found"}
        new_cols = list(config.get("columns") or []) + [col]
        cleaned, err = _validate_columns(new_cols)
        if err:
            return {"error": err}
        config["columns"] = cleaned
        self._store.save_board(board_id, config)
        await self.emit("board:column_added", {"id": board_id, "col": col.get("id")})
        return {"ok": True, "columns": cleaned}

    @web_route("PATCH", "/api/boards/{id}/columns/{col_id}")
    async def api_edit_column(self, request):
        """Update one column in place. Column id is immutable."""
        board_id = request.path_params.get("id", "")
        col_id = request.path_params.get("col_id", "")
        updates = await request.json()
        config = self._store.get_board(board_id)
        if not config:
            return {"error": "Board not found"}
        cols = list(config.get("columns") or [])
        idx = next((i for i, c in enumerate(cols) if c.get("id") == col_id), -1)
        if idx < 0:
            return {"error": f"column {col_id!r} not found"}
        merged = {**cols[idx], **{k: v for k, v in updates.items() if k != "id"}}
        merged["id"] = col_id
        cols[idx] = merged
        cleaned, err = _validate_columns(cols)
        if err:
            return {"error": err}
        config["columns"] = cleaned
        self._store.save_board(board_id, config)
        await self.emit("board:column_updated", {"id": board_id, "col": col_id})
        return {"ok": True, "column": cleaned[idx]}

    @web_route("DELETE", "/api/boards/{id}/columns/{col_id}")
    async def api_delete_column(self, request):
        """Remove a column from the config. Item frontmatter fields are left
        untouched — values become orphaned but can be revived by re-adding."""
        board_id = request.path_params.get("id", "")
        col_id = request.path_params.get("col_id", "")
        config = self._store.get_board(board_id)
        if not config:
            return {"error": "Board not found"}
        cols = list(config.get("columns") or [])
        new_cols = [c for c in cols if c.get("id") != col_id]
        if len(new_cols) == len(cols):
            return {"error": f"column {col_id!r} not found"}
        config["columns"] = new_cols
        self._store.save_board(board_id, config)
        await self.emit("board:column_deleted", {"id": board_id, "col": col_id})
        return {"ok": True}

    @web_route("DELETE", "/api/boards/{id}")
    async def api_delete_board(self, request):
        """Delete a board config. Items in vault are NOT deleted."""
        board_id = request.path_params.get("id", "")
        if self._store.delete_board(board_id):
            return {"ok": True}
        return {"error": "Board not found"}

    # ------------------------------------------------------------------
    # Item queries
    # ------------------------------------------------------------------

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
            return {"ok": False, "type": "app",
                    "app": src.get("app", ""),
                    "error": lib._source_error}
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

    # ------------------------------------------------------------------
    # Item mutations
    # ------------------------------------------------------------------

    @web_route("POST", "/api/boards/{id}/items")
    async def api_create_item(self, request):
        """Create a new vault note for a board."""
        board_id = request.path_params.get("id", "")
        data = await request.json()
        config = self._store.get_board(board_id) or get_preset(board_id)
        if not config:
            return {"error": "Board not found"}
        source_tag = config.get("source_tag", board_id)
        # Build frontmatter from provided fields
        fm = {"tags": [source_tag], "created": date.today().isoformat()}
        for col in config.get("columns", []):
            col_id = col["id"]
            if col_id in data:
                fm[col_id] = data[col_id]
            elif col.get("type") == "select" and col.get("options"):
                fm[col_id] = col["options"][0]  # Default to first option

        # Determine filename
        name_field = data.get("name") or data.get("title") or f"item-{date.today().isoformat()}"
        slug = name_field.lower().replace(" ", "-")
        slug = "".join(c for c in slug if c.isalnum() or c == "-")

        # Create vault note
        vault_dir = self.vault_config("boards_items_dir", f"30_Resources/EmptyOS/boards-data/{source_tag}")
        rel_path = f"{vault_dir}/{slug}.md"
        body = data.get("body", "")
        self.vault_create_note(rel_path, fm, body)

        await self.emit("board:item_created", {"board": board_id, "file": f"{slug}.md"})

        # Run automations for item_created
        new_item = {**fm, "file": f"{slug}.md", "path": rel_path}
        await evaluate_rules(self, config, None, new_item, event_type="item_created")

        # Emit assignment deltas for any person-family column that was populated
        # at creation time. Old item is empty (new item) so everything is added.
        initial_updates = {k: new_item.get(k) for k in (data or {})}
        await self._emit_assignment_deltas(board_id, config, f"{slug}.md", {}, new_item, initial_updates)
        # Populate link-record edges (and any declared inverses) for this new item.
        await self._maintain_link_inverses(board_id, config, f"{slug}.md", {}, new_item, initial_updates)

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
        valid_cols = {col["id"] for col in config.get("columns", [])}
        safe_updates = {k: v for k, v in updates.items() if k in valid_cols}

        if not safe_updates:
            return {"error": "No valid fields to update"}

        # Pre-commit: evaluate any `kind=guard` rules. If a guard blocks, the
        # PATCH never touches disk — 409 with a human message + machine hook.
        guard_block = await evaluate_guards(self, config, old_item, safe_updates)
        if guard_block:
            return guard_block

        # Dependency cycle guard: if `blocks` or `blocked_by` is being updated,
        # make sure the new graph stays acyclic.
        cycle = self._detect_cycle_on_update(config, filename, old_item, safe_updates)
        if cycle:
            return {"error": "dependency_cycle",
                    "message": f"would create a cycle: {' → '.join(cycle)}"}

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
            await self.emit("board:item_updated", {
                "board": board_id,
                "file": filename,
                "old": old_item,
                "new": new_item,
                "updates": safe_updates,
            })
            # Emit assignment deltas for any person-family column that changed.
            await self._emit_assignment_deltas(board_id, config, filename, old_item, new_item, safe_updates)
            # Maintain link-record edges + inverse fields on target items.
            await self._maintain_link_inverses(board_id, config, filename, old_item, new_item, safe_updates)
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
                projected_blocked_by = [s.strip() for s in projected_blocked_by.split(",") if s.strip()]
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

    async def shift_item_date(self, board_id: str, item_id: str,
                              field: str, delta_days: int) -> dict:
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

    async def _emit_assignment_deltas(self, board_id, config, filename, old_item, new_item, updates):
        """For every person-family column that changed in this update, emit the
        assignment / unassignment deltas so the people app's workload index
        stays in sync."""
        from .board_engine import PERSON_SINGLE_TYPES, PERSON_MULTI_TYPES, ROLE_FOR_TYPE

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
                old_ids = set(old_val or []) if isinstance(old_val, list) else ({old_val} if old_val else set())
                new_ids = set(new_val or []) if isinstance(new_val, list) else ({new_val} if new_val else set())
            else:
                continue

            added = new_ids - old_ids
            removed = old_ids - new_ids
            weight = float(col.get("weight_hours", 5.0))
            for pid in added:
                await self.emit_assignment(pid, item_desc, weight_hours=weight, role=role, assigned=True)
            for pid in removed:
                await self.emit_assignment(pid, item_desc, weight_hours=weight, role=role, assigned=False)

    # ── Link index — build + maintain on edits ────────────────────────

    async def _rebuild_links(self) -> None:
        """Walk every saved board and every item, re-populate the link index
        from current link-record column values. Safe to call repeatedly."""
        self._links.clear()
        try:
            boards = self._store.list_boards() or []
        except Exception as e:
            self.log_warn(f"link rebuild: list_boards failed: {e}")
            return
        for b in boards:
            await self._index_board(b["id"])

    async def _index_board(self, board_id: str) -> None:
        """Populate link edges for one board from its current item set."""
        config = self._store.get_board(board_id) or get_preset(board_id)
        if not config:
            return
        link_cols = _link_record_columns(config)
        if not link_cols:
            return
        lib = DynamicBoardLibrary(self, config)
        try:
            items = await lib.get_items()
        except Exception as e:
            self.log_warn(f"link rebuild: {board_id}.get_items failed: {e}")
            return
        self._links.clear_board(board_id)
        for item in items:
            item_id = item.get("file") or item.get("id")
            if not item_id:
                continue
            for col in link_cols:
                target_board = col.get("target_board") or ""
                if not target_board:
                    continue
                targets = _as_id_list(item.get(col["id"]))
                for tgt in targets:
                    self._links.register_edge(board_id, item_id, col["id"],
                                              target_board, tgt)

    async def _maintain_link_inverses(self, board_id: str, config: dict,
                                      filename: str, old_item: dict,
                                      new_item: dict, updates: dict) -> None:
        """For every link-record column that changed, update the index AND
        write the inverse field on targets (when the column declares one)."""
        link_cols = _link_record_columns(config)
        if not link_cols:
            return

        # Refresh this item's outgoing entry from the post-write state.
        new_col_targets = {
            col["id"]: _as_id_list(new_item.get(col["id"]))
            for col in link_cols
        }
        # Rewrite edges via register_edge with full board info.
        self._links._outgoing.get(board_id, {}).pop(filename, None)
        for tgt_board_items in self._links._incoming.values():
            for tgt_item, refs in list(tgt_board_items.items()):
                tgt_board_items[tgt_item] = [
                    r for r in refs if not (r[0] == board_id and r[1] == filename)
                ]
                if not tgt_board_items[tgt_item]:
                    tgt_board_items.pop(tgt_item, None)
        for col in link_cols:
            target_board = col.get("target_board") or ""
            if not target_board:
                continue
            for tgt in new_col_targets.get(col["id"], []):
                self._links.register_edge(board_id, filename, col["id"],
                                          target_board, tgt)

        # Inverse-field maintenance: for columns with `inverse` declared, make
        # the target's inverse field point back at us. Only runs on the subset
        # of link-record columns that were in this update.
        for col in link_cols:
            if col["id"] not in updates:
                continue
            inverse = col.get("inverse")
            target_board = col.get("target_board") or ""
            if not inverse or not target_board:
                continue

            old_targets = set(_as_id_list(old_item.get(col["id"])))
            new_targets = set(_as_id_list(new_item.get(col["id"])))
            added = new_targets - old_targets
            removed = old_targets - new_targets
            if not added and not removed:
                continue

            tgt_config = self._store.get_board(target_board) or get_preset(target_board)
            if not tgt_config:
                continue
            tgt_col = next((c for c in _link_record_columns(tgt_config)
                            if c["id"] == inverse), None)
            if not tgt_col:
                self.log_warn(f"inverse column '{inverse}' not found on '{target_board}'")
                continue
            tgt_lib = DynamicBoardLibrary(self, tgt_config)

            for tgt_id in added:
                tgt_item = await tgt_lib.get_detail(tgt_id)
                if not tgt_item:
                    continue
                current = set(_as_id_list(tgt_item.get(inverse)))
                current.add(filename)
                await tgt_lib.set_field(tgt_id, inverse, sorted(current))
            for tgt_id in removed:
                tgt_item = await tgt_lib.get_detail(tgt_id)
                if not tgt_item:
                    continue
                current = set(_as_id_list(tgt_item.get(inverse)))
                current.discard(filename)
                await tgt_lib.set_field(tgt_id, inverse, sorted(current))

    @web_route("GET", "/api/boards/{id}/items/{file}/backlinks")
    async def api_item_backlinks(self, request):
        """Return items on other boards whose link-record columns point at this one."""
        board_id = request.path_params.get("id", "")
        filename = request.path_params.get("file", "")
        refs = self._links.incoming(board_id, filename)
        if not refs:
            return {"backlinks": []}

        # Group refs by (from_board) so we batch-resolve titles per board.
        by_board: dict[str, list[tuple[str, str]]] = {}
        for from_board, from_item, from_col in refs:
            by_board.setdefault(from_board, []).append((from_item, from_col))

        out = []
        for from_board, pairs in by_board.items():
            config = self._store.get_board(from_board) or get_preset(from_board)
            if not config:
                continue
            name_col_id = (config.get("columns") or [{}])[0].get("id", "name")
            lib = DynamicBoardLibrary(self, config)
            try:
                items = await lib.get_items()
            except Exception:
                items = []
            by_id = {(it.get("file") or it.get("id")): it for it in items}
            for from_item, from_col in pairs:
                it = by_id.get(from_item)
                if not it:
                    continue
                out.append({
                    "board": from_board,
                    "board_name": config.get("name", from_board),
                    "file": from_item,
                    "col": from_col,
                    "title": it.get(name_col_id) or from_item,
                })
        return {"backlinks": out}

    @web_route("POST", "/api/links/rebuild")
    async def api_links_rebuild(self, request):
        """Force a full rebuild of the link index."""
        await self._rebuild_links()
        return {"ok": True, **self._links.stats()}

    # ── Saved views ───────────────────────────────────────────────────

    @web_route("GET", "/api/boards/{id}/views")
    async def api_list_views(self, request):
        board_id = request.path_params.get("id", "")
        return {"views": self._views.list(board_id)}

    @web_route("POST", "/api/boards/{id}/views")
    async def api_save_view(self, request):
        board_id = request.path_params.get("id", "")
        data = await request.json()
        saved = self._views.save(board_id, data)
        await self.emit("board:view_saved", {"board": board_id, "view": saved["id"]})
        return {"ok": True, "view": saved}

    @web_route("GET", "/api/boards/{id}/views/{vid}")
    async def api_get_view(self, request):
        board_id = request.path_params.get("id", "")
        vid = request.path_params.get("vid", "")
        v = self._views.get(board_id, vid)
        if not v:
            return {"error": "View not found"}
        return v

    @web_route("DELETE", "/api/boards/{id}/views/{vid}")
    async def api_delete_view(self, request):
        board_id = request.path_params.get("id", "")
        vid = request.path_params.get("vid", "")
        ok = self._views.delete(board_id, vid)
        if ok:
            await self.emit("board:view_deleted", {"board": board_id, "view": vid})
        return {"ok": ok}

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

    @web_route("GET", "/api/boards/{id}/items/{file}/activity")
    async def api_item_activity(self, request):
        """Recent events touching this item (for the detail slide-out activity log)."""
        board_id = request.path_params.get("id", "")
        filename = request.path_params.get("file", "")
        limit = int(request.query_params.get("limit", 30))
        try:
            events = await self.kernel.events.history(limit=500)
        except Exception:
            events = []
        out = []
        for e in events:
            data = e.get("data") or {}
            etype = e.get("type", "")
            if not etype.startswith("board:"):
                continue
            if data.get("board") != board_id:
                continue
            if data.get("file") not in (filename, None):
                continue
            out.append({
                "type": etype,
                "timestamp": e.get("timestamp"),
                "updates": data.get("updates") or {},
                "data": data,
            })
            if len(out) >= limit:
                break
        return {"events": out}

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
            if old_item:
                await evaluate_rules(self, config, old_item,
                                     {**old_item, "status": "Archived"},
                                     event_type="item_archived")

        return result

    # ------------------------------------------------------------------
    # SSG Export
    # ------------------------------------------------------------------

    @web_route("GET", "/api/export/{id}")
    async def api_export_board(self, request):
        """Bundle the board into a standalone offline HTML file."""
        board_id = request.path_params.get("id", "")
        config = self._store.get_board(board_id) or get_preset(board_id)
        if not config:
            return {"error": "Board not found"}

        lib = DynamicBoardLibrary(self, config)
        items = await lib.get_items()

        # Strip internal keys from config
        clean_config = {k: v for k, v in config.items() if not k.startswith("_")}
        export_data = {"board": clean_config, "items": items}

        template_path = Path(__file__).parent / "pages" / "index.html"
        exporter = AppExporter(self)

        try:
            bundled_html = exporter.bundle_app("boards", export_data, template_path)
        except Exception as e:
            return {"error": str(e)}

        from starlette.responses import HTMLResponse
        return HTMLResponse(
            content=bundled_html,
            headers={"Content-Disposition": f'attachment; filename="{board_id}_export.html"'},
        )

    # ------------------------------------------------------------------
    # Presets API
    # ------------------------------------------------------------------

    @web_route("GET", "/api/column-types")
    async def api_column_types(self, request):
        """Expose the SDK registry metadata so the frontend can dispatch
        renderers / group-by eligibility without hardcoding the list."""
        from emptyos.sdk.column_types import ColumnTypeRegistry
        out = []
        for tid, t in ColumnTypeRegistry.all().items():
            out.append({
                "id": tid,
                "widget": t.widget,
                "person_like": t.person_like,
                "list_like": t.list_like,
                "groupable": t.groupable,
                "role": t.role,
            })
        return {"types": out}

    @web_route("GET", "/api/presets")
    async def api_presets(self, request):
        """List available board presets."""
        return {"presets": list_presets()}

    # ------------------------------------------------------------------
    # Hub panel
    # ------------------------------------------------------------------

    async def panel_pinned_boards(self) -> list[dict] | None:
        """Return chips for boards on the home screen."""
        boards = self._store.list_boards()
        if not boards:
            return None
        return [
            {"label": b["name"], "href": f"/boards/#{b['id']}", "icon": "📊"}
            for b in boards[:6]
        ]
