"""Collection App — a manifest-declared `[collection]` schema owns its own
vault-backed CRUD, with zero bespoke Python/JS for the common "list of items
with typed fields" app shape.

Closes the gap between `VaultLibrary` (declarative backend, but every real
app still hand-writes routes + validation + a page) and
`apps/public/standard/boards/presets.py` (a genuinely zero-code declarative
UI, but only a *view* onto data an app already owns). `CollectionApp` lets an
app own its own data AND get a real CRUD UI (via `emptyos/web/auto_ui.py`)
AND a board view (via the same `columns` vocabulary boards already uses),
purely from a manifest declaration:

    [collection]
    tag = "widget"              # vault tag this app owns
    label = "Widget"
    sort_key = "created"
    sort_reverse = true

    [[collection.fields]]
    id = "title"
    label = "Title"
    type = "text"                # a ColumnTypeRegistry id
    required = true

    [[collection.fields]]
    id = "status"
    label = "Status"
    type = "select"
    options = ["draft", "active", "done"]
    default = "draft"

`type` values are `ColumnTypeRegistry` ids — the same vocabulary
`apps/public/standard/boards/` uses for its `columns`, so a collection field
and a board column are the same shape. `CollectionLibrary.__init__` builds
its `VaultLibrary.fields` dict the same way
`emptyos.sdk.board_engine.DynamicBoardLibrary` already does at runtime.

Usage:

    from emptyos.sdk import BaseApp
    from emptyos.sdk.collection_app import CollectionApp

    class WidgetApp(CollectionApp, BaseApp):
        pass

That's the entire app — `CollectionApp` supplies `GET/POST /api/items`,
`GET/PUT/DELETE /api/items/{id}`, `GET /api/schema`, and `board_presets()`
(bind via `[[contributes.boards.preset]] method = "board_presets"` in the
manifest so the app's items are also browsable as a board).

See `.claude/rules/boards-as-view-layer.md` for the board-contribution
contract this mirrors.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

from emptyos.sdk.column_types import ColumnTypeRegistry
from emptyos.sdk.decorators import web_route
from emptyos.sdk.utils import path_segment_error
from emptyos.sdk.vault_library import VaultLibrary

if TYPE_CHECKING:
    from emptyos.sdk.base_app import BaseApp


def _slugify(name: str) -> str:
    """Mirrors boards' `_create_item_from_fields` slug rule exactly, so a
    collection app's filenames read the same as any board-created item."""
    slug = (name or "").lower().replace(" ", "-")
    return "".join(c for c in slug if c.isalnum() or c == "-") or "item"


class CollectionLibrary(VaultLibrary):
    """A VaultLibrary configured from a manifest `[collection]` schema.

    Mirrors `DynamicBoardLibrary.__init__` (`emptyos/sdk/board_engine.py`) for
    building `self.fields` from typed columns — the two are deliberately the
    same shape so a collection field and a board column round-trip through
    the one `ColumnTypeRegistry`.
    """

    def __init__(self, app: "BaseApp", collection_config: dict):
        self.schema: list[dict] = collection_config.get("fields", []) or []
        self.tag = collection_config.get("tag", "")
        self.sort_key = collection_config.get("sort_key", "created")
        self.sort_reverse = bool(collection_config.get("sort_reverse", True))
        self.fallback_folder = collection_config.get(
            "folder", f"30_Resources/EmptyOS/{getattr(app.manifest, 'id', '')}"
        )
        self.fields = {}
        for col in self.schema:
            col_type = col.get("type", "text")
            self.fields[col["id"]] = ColumnTypeRegistry.get(col_type).storage
        super().__init__(app)

    # ── Schema-driven write path ─────────────────────────────────────────

    def coerce_and_validate(self, data: dict, *, require_missing: bool = True) -> tuple[dict, list[str]]:
        """Coerce loose input against the schema, then run each field's
        `ColumnType.validate()` (required/pattern check by default — see
        `emptyos/sdk/column_types.py`). Returns (clean_values, error_messages).

        A field absent from `data` is only checked when `require_missing` is
        True (the `create()` default) AND it's `required` with no `default`.
        `update_validated()` passes `require_missing=False` — a PATCH-style
        partial update must not fail just because it didn't touch every
        required field; only a field the caller actually submitted (present
        but empty) can trigger the required check on update.
        """
        errors: list[str] = []
        out: dict = {}
        for col in self.schema:
            col_id = col["id"]
            ctype = ColumnTypeRegistry.get(col.get("type") or "text")
            provided = col_id in data
            if not provided:
                if not require_missing or col.get("default") is not None or not col.get("required"):
                    continue
                value: Any = ""  # trigger the required check below
            else:
                value = ctype.coerce(data[col_id], col)
            try:
                ctype.validate(value, col)
            except ValueError as e:
                errors.append(str(e))
                continue
            if provided:
                out[col_id] = value
        return out, errors

    async def create(self, data: dict) -> dict:
        clean, errors = self.coerce_and_validate(data)
        if errors:
            return {"error": "; ".join(errors)}

        fm: dict = {"tags": [self.tag], "created": date.today().isoformat(), **clean}
        for col in self.schema:
            col_id = col["id"]
            if col_id in fm:
                continue
            if col.get("default") is not None:
                fm[col_id] = col["default"]
            elif col.get("type") == "select" and col.get("options"):
                fm[col_id] = col["options"][0]

        name_field = clean.get("title") or clean.get("name") or next(iter(clean.values()), "")
        slug = _slugify(str(name_field) or f"item-{date.today().isoformat()}")
        rel_path = f"{self.fallback_folder}/{slug}.md"
        body = data.get("body", "")
        self.app.vault_create_note(rel_path, fm, body)
        await self.app.emit(f"{self.tag}:created", {"file": f"{slug}.md", "path": rel_path})
        await self._write_through_links(f"{slug}.md", clean, old_item=None)
        return {"ok": True, "file": f"{slug}.md", "path": rel_path}

    async def update_validated(self, filename: str, data: dict) -> dict:
        clean, errors = self.coerce_and_validate(data, require_missing=False)
        if errors:
            return {"error": "; ".join(errors)}
        if not clean:
            return {"error": "no valid fields to update"}
        old_item = self.detail(filename) if self._link_record_fields() else None
        result = self.update(filename, clean)
        if result.get("ok"):
            await self.app.emit(f"{self.tag}:updated", {"file": filename, "updates": clean})
            await self._write_through_links(filename, clean, old_item)
        return result

    def _link_record_fields(self) -> list[dict]:
        return [c for c in self.schema if c.get("type") == "link-record"]

    async def _write_through_links(self, filename: str, values: dict, old_item: dict | None) -> None:
        """Push new link-record values through to `boards`' reciprocal-inverse
        + link-index maintenance (`apps/public/standard/boards/links.py:
        set_link_field`) — the counterpart to boards' own PATCH path, which
        owns that logic because link-record columns are a boards feature
        (`.claude/rules/boards-as-view-layer.md`). `boards` is a soft
        dependency: absent/disabled, this quietly no-ops rather than failing
        an otherwise-successful create/update (`self.app.try_call_app`).
        """
        link_cols = [c for c in self._link_record_fields() if c["id"] in values]
        if not link_cols:
            return
        app_id = getattr(self.app.manifest, "id", self.tag)
        board_id = f"{app_id}-collection"
        for col in link_cols:
            _, err = await self.app.try_call_app(
                "boards", "set_link_field",
                board_id=board_id, filename=filename, col_id=col["id"],
                new_ids=values[col["id"]], old_ids=(old_item or {}).get(col["id"]) or [],
            )
            if err:
                self.app.log_warn(f"link write-through to boards failed for '{col['id']}': {err}")

    async def delete(self, filename: str) -> dict:
        path = self.find_file(filename)
        if not path:
            return {"error": "not found"}
        path.unlink()
        # Read-your-writes: index_file() removes a stale entry when the path
        # no longer exists (emptyos/runtime/vault_index.py). Without this the
        # deleted item lingers in list() until the watcher's debounce catches
        # up — the same staleness VaultLibrary.update() already guards
        # against via the identical _poke_index call.
        self._poke_index(path)
        await self.app.emit(f"{self.tag}:deleted", {"file": filename})
        return {"ok": True}


class CollectionApp:
    """Mixin: `class MyApp(CollectionApp, BaseApp): pass` plus a manifest
    `[collection]` table gets a working CRUD API + auto_ui page + board view
    with no further code. See module docstring for the schema shape."""

    def _collection_config(self) -> dict:
        return (getattr(self.manifest, "raw", None) or {}).get("collection", {}) or {}

    def _collection_lib(self) -> CollectionLibrary:
        if not hasattr(self, "_collection_lib_cache"):
            self._collection_lib_cache = CollectionLibrary(self, self._collection_config())
        return self._collection_lib_cache

    @web_route("GET", "/api/schema")
    async def api_collection_schema(self, request):
        cfg = self._collection_config()
        return {
            "tag": cfg.get("tag", ""),
            "label": cfg.get("label", ""),
            "fields": cfg.get("fields", []) or [],
        }

    async def _evaluate_computed(self, items: list[dict]) -> list[dict]:
        """Run rollup (and any hand-authored formula) columns through boards'
        live evaluator, re-exposed via `evaluate_collection_items`
        (`apps/public/standard/boards/links.py`) — the same computation
        `GET /api/boards/{id}/items` runs for these columns, so a
        collection-app's own native `/api/items` returns evaluated values
        too, not just its board view. Skips the cross-app call entirely for
        the common case (no rollup/formula field declared). Soft dependency
        on `boards`; degrades to the raw (unevaluated) items.
        """
        fields = self._collection_config().get("fields") or []
        if not any(f.get("type") in ("rollup", "formula") for f in fields):
            return items
        config = self.board_presets() or {"columns": fields}
        evaluated, err = await self.try_call_app(
            "boards", "evaluate_collection_items", config=config, items=items
        )
        if err:
            self.log_warn(f"computed-field evaluation via boards failed: {err}")
            return items
        return evaluated

    @web_route("GET", "/api/items")
    async def api_collection_list(self, request):
        return await self._evaluate_computed(self._collection_lib().list())

    @web_route("POST", "/api/items")
    async def api_collection_create(self, request):
        data = await request.json()
        return await self._collection_lib().create(data)

    @web_route("GET", "/api/items/{id}")
    async def api_collection_get(self, request):
        item_id = request.path_params.get("id", "")
        err = path_segment_error(item_id, "item id")
        if err:
            return {"error": err}
        item = self._collection_lib().detail(item_id)
        if not item:
            return {"error": "not found"}
        evaluated = await self._evaluate_computed([item])
        return evaluated[0] if evaluated else item

    @web_route("PUT", "/api/items/{id}")
    async def api_collection_update(self, request):
        item_id = request.path_params.get("id", "")
        err = path_segment_error(item_id, "item id")
        if err:
            return {"error": err}
        data = await request.json()
        return await self._collection_lib().update_validated(item_id, data)

    @web_route("DELETE", "/api/items/{id}")
    async def api_collection_delete(self, request):
        item_id = request.path_params.get("id", "")
        err = path_segment_error(item_id, "item id")
        if err:
            return {"error": err}
        return await self._collection_lib().delete(item_id)

    # ── Board contribution — [[contributes.boards.preset]] method target ──

    def board_presets(self) -> dict | None:
        cfg = self._collection_config()
        tag = cfg.get("tag", "")
        if not tag:
            return None
        columns = [
            {
                k: f[k]
                for k in (
                    "id", "label", "type", "options", "default", "color_map", "required",
                    # link-record: apps/public/standard/boards/links.py needs these
                    # to index + maintain the reciprocal field. rollup: board_engine
                    # ._rollup_expr needs these to compile the aggregate expression.
                    # Dropping any of these here is silent — the column still
                    # renders as `type: link-record`/`rollup` but never resolves.
                    "target_board", "multi", "inverse",
                    "source_link", "target_field", "agg",
                )
                if k in f
            }
            for f in (cfg.get("fields", []) or [])
        ]
        app_id = getattr(self.manifest, "id", tag)
        app_name = getattr(self.manifest, "name", app_id)
        return {
            "id": f"{app_id}-collection",
            "name": cfg.get("label") or app_name,
            "description": f"Items owned by {app_name}.",
            "source_tag": tag,
            "tags": ["board-config"],
            "columns": columns,
            "views": [{"type": "table", "default": True}, {"type": "kanban", "group_by": _first_select_field(columns)}],
            "kanban_group_by": _first_select_field(columns),
        }


def _first_select_field(columns: list[dict]) -> str:
    """Best-effort kanban `group_by` — the first select column, or the first
    column if none. A board with no select field still renders (table view
    stays the default); kanban just groups by whatever's there."""
    for c in columns:
        if c.get("type") == "select":
            return c["id"]
    return columns[0]["id"] if columns else ""


__all__ = ["CollectionLibrary", "CollectionApp"]
