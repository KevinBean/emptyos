"""Boards — Visual Work OS backed by Markdown.

Provides dynamic boards (views over vault notes), customizable column schemas,
multi-view rendering (table, kanban, calendar, timeline, chart), automation rules,
built-in presets, and a completely offline SSG export via AppExporter.
"""

from __future__ import annotations

from pathlib import Path

from emptyos.sdk import BaseApp, web_route
from emptyos.sdk.exporter import AppExporter

from . import items as _items
from . import links as _links
from . import saved_views as _saved_views

from . import activity as _activity
from . import attachments as _attachments
from . import comments as _comments
from . import planner_sync as _planner_sync
from . import public_form as _public_form
from .board_engine import BoardConfigStore, DynamicBoardLibrary
from .link_index import LinkIndex
from .presets import PRESETS, get_preset, list_presets
from .views import ViewStore


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

    # ── Durable activity log (extracted to activity.py) ──
    _activity_dir = _activity._activity_dir

    _actor_name = _activity._actor_name

    _log_activity = _activity._log_activity

    _read_activity = _activity._read_activity

    api_item_activity = _activity.api_item_activity

    # ── Comments (extracted to comments.py) ──
    _comments_path = _comments._comments_path

    _load_comments = _comments._load_comments

    api_list_comments = _comments.api_list_comments

    api_add_comment = _comments.api_add_comment

    api_edit_comment = _comments.api_edit_comment

    api_delete_comment = _comments.api_delete_comment

    # ── Attachments (extracted to attachments.py) ──
    _attachments_dir = _attachments._attachments_dir

    api_list_attachments = _attachments.api_list_attachments

    api_upload_attachment = _attachments.api_upload_attachment

    api_serve_attachment = _attachments.api_serve_attachment

    api_delete_attachment = _attachments.api_delete_attachment

    api_attachments_index = _attachments.api_attachments_index

    # ── Planner import (extracted to planner_sync.py) ──
    api_planner_apply = _planner_sync.api_planner_apply

    async def setup(self):
        await super().setup()
        self._store = BoardConfigStore(self)
        self._links = LinkIndex()
        self._views = ViewStore(self.data_dir / "views")
        # Auto-instantiate app-sourced presets as saved boards so they appear
        # in the sidebar without the user clicking through templates. These are
        # system-database views — marked readonly so edits route through the
        # source app.
        await self._sync_presets()
        # Defer initial rebuild to the next tick — other apps may still be
        # loading, and the rebuild hits their list_all() methods via source=app.
        import asyncio

        self.spawn_background(self._rebuild_links())

    async def _sync_presets(self) -> None:
        """Instantiate every built-in + app-contributed preset as a readonly
        system-database board. Idempotent — existing boards get their structural
        fields re-synced from the preset; missing ones are created.

        Load-order safety: apps web-lazy-load, so ``call_contributions`` (which
        only sees *loaded* apps) would miss a contributor that hasn't been hit
        yet — the board would silently never appear. So we first force-load any
        app whose *manifest* declares a ``[[contributes.boards.preset]]`` slot
        (a small, bounded set), then gather. This makes a system-database board
        appear regardless of whether the user has visited its source app. The
        board's data still hydrates lazily via ``list_all`` on rebuild.
        Fail-soft throughout: a broken contributor can't break boards.
        """
        loader = self.kernel.apps
        for app_id, manifest in list(loader.manifests.items()):
            if app_id in loader.instances:
                continue
            try:
                declares = (manifest.raw.get("contributes", {})
                            .get("boards", {}).get("preset"))
            except Exception:
                declares = None
            if declares:
                try:
                    await loader.load(app_id)
                except Exception:
                    pass  # a contributor that won't load just doesn't get a board
        all_presets = dict(PRESETS)
        contributed_ids: set[str] = set()
        try:
            for _entry, result in await self.call_contributions("boards", "preset"):
                contributed = result if isinstance(result, list) else [result]
                for p in contributed:
                    if isinstance(p, dict) and p.get("id"):
                        all_presets[p["id"]] = p
                        contributed_ids.add(p["id"])
        except Exception:
            pass
        for pid, preset in all_presets.items():
            src = preset.get("source") or {}
            stype = src.get("type")
            # `app` and `mixed` are always auto-materialized — the source
            # app(s) own the data unambiguously. A `vault_tag` preset is
            # auto-materialized only when it's APP-CONTRIBUTED: the static
            # PRESETS templates (crm-pipeline, bug-tracker, ...) stay
            # template-gallery-only (a generic template every user doesn't
            # necessarily want in their sidebar), but a contributing app's
            # own `[[contributes.boards.preset]]` is unambiguous — the app
            # is already installed and the board IS that app's data. See
            # emptyos/sdk/collection_app.py for the first vault_tag
            # contributor (`CollectionApp.board_presets`).
            if stype not in ("app", "mixed") and pid not in contributed_ids:
                continue
            src_app = src.get("app", "") if stype == "app" else ""
            # `app`/`mixed` boards route edits through the source app's
            # set_field — always read-only here. A contributed `vault_tag`
            # board owns its notes directly (no source app to route
            # through), so it's editable like any user-created vault_tag
            # board.
            force_readonly = stype in ("app", "mixed")
            existing = self._store.get_board(pid)
            if existing:
                # System-database views track their preset: the source app(s),
                # columns, and views are system-defined. Re-sync these
                # structural fields from the preset on every boot so a preset
                # that gains a source / column / view propagates instead of
                # silently sticking at whatever shape was first instantiated.
                # Saved per-user view state lives separately in ViewStore and
                # is untouched.
                changed = False
                if existing.get("readonly") != force_readonly or existing.get("source_app_id") != src_app:
                    existing["readonly"] = force_readonly
                    existing["source_app_id"] = src_app
                    changed = True
                for k in ("source", "columns", "views", "kanban_group_by", "rules"):
                    if k in preset and existing.get(k) != preset[k]:
                        existing[k] = preset[k]
                        changed = True
                if changed:
                    self._store.save_board(pid, existing)
                continue
            cfg = dict(preset)
            cfg["readonly"] = force_readonly
            cfg["source_app_id"] = src_app
            self._store.save_board(pid, cfg)

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
                "columns": data.get(
                    "columns",
                    [
                        {"id": "name", "label": "Name", "type": "text"},
                        {
                            "id": "status",
                            "label": "Status",
                            "type": "select",
                            "options": ["To Do", "In Progress", "Done"],
                        },
                    ],
                ),
                "views": data.get(
                    "views",
                    [
                        {"type": "table", "default": True},
                        {"type": "kanban", "group_by": "status"},
                    ],
                ),
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
            return {
                "ok": True,
                "id": target_id,
                "name": existing.get("name", target_id),
                "created": False,
            }
        config = dict(config)
        config["id"] = target_id
        self._store.save_board(target_id, config)
        await self.emit("board:created", {"id": target_id, "name": config.get("name", target_id)})
        return {"ok": True, "id": target_id, "name": config.get("name", target_id), "created": True}

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

    @web_route("GET", "/api/column-types")
    async def api_column_types(self, request):
        """Expose the SDK registry metadata so the frontend can dispatch
        renderers / group-by eligibility without hardcoding the list."""
        from emptyos.sdk.column_types import ColumnTypeRegistry

        out = []
        for tid, t in ColumnTypeRegistry.all().items():
            out.append(
                {
                    "id": tid,
                    "widget": t.widget,
                    "person_like": t.person_like,
                    "list_like": t.list_like,
                    "groupable": t.groupable,
                    "role": t.role,
                }
            )
        return {"types": out}

    @web_route("GET", "/api/presets")
    async def api_presets(self, request):
        """List available board presets."""
        return {"presets": list_presets()}

    @web_route("GET", "/api/me")
    async def api_me(self, request):
        """The current user's identity settings (comment author + ★ Mine)."""
        return {"me": self._actor_name(), "me_person": self.setting("boards.me_person", "")}

    async def panel_pinned_boards(self) -> list[dict] | None:
        """Return chips for boards on the home screen."""
        boards = self._store.list_boards()
        if not boards:
            return None
        return [
            {"label": b["name"], "href": f"/boards/#{b['id']}", "icon": "📊"} for b in boards[:6]
        ]

    # ── Items (extracted to items.py) ──
    api_get_items            = _items.api_get_items
    api_source_status        = _items.api_source_status
    api_get_stats            = _items.api_get_stats
    api_create_item          = _items.api_create_item
    api_smart_add            = _items.api_smart_add
    _validate_smart_fields   = _items._validate_smart_fields
    _create_item_from_fields = _items._create_item_from_fields
    api_update_item          = _items.api_update_item
    _detect_cycle_on_update  = _items._detect_cycle_on_update
    _cached_items_sync       = _items._cached_items_sync
    shift_item_date          = _items.shift_item_date
    _emit_assignment_deltas  = _items._emit_assignment_deltas
    api_get_item             = _items.api_get_item
    api_archive_item         = _items.api_archive_item

    # ── Links (extracted to links.py) ──
    _rebuild_links           = _links._rebuild_links
    _index_board             = _links._index_board
    _reindex_link_edges      = _links._reindex_link_edges
    _write_inverse_for_column = _links._write_inverse_for_column
    _maintain_link_inverses  = _links._maintain_link_inverses
    set_link_field           = _links.set_link_field
    evaluate_collection_items = _links.evaluate_collection_items
    api_item_backlinks       = _links.api_item_backlinks
    api_links_rebuild        = _links.api_links_rebuild

    # ── Public Form view (extracted to public_form.py) ──
    public_form_page       = _public_form.public_form_page
    api_public_form_schema = _public_form.api_public_form_schema
    api_public_form_submit = _public_form.api_public_form_submit

    # ── Saved Views (extracted to saved_views.py) ──
    api_list_views  = _saved_views.api_list_views
    api_save_view   = _saved_views.api_save_view
    api_get_view    = _saved_views.api_get_view
    api_delete_view = _saved_views.api_delete_view
