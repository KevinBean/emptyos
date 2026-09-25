"""boards — the relation graph between items — backlinks and inverse maintenance.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: The link index: rebuilding it, indexing one board, keeping relation columns symmetric when one side changes, and answering backlink queries. Separate from items because a link is a fact about two items, not a property of one.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._lib / self._config_for (spine); the LinkIndex in link_index.py.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from .board_engine import DynamicBoardLibrary, evaluate_formulas
from .link_index import _as_id_list
from .presets import get_preset
from typing import TYPE_CHECKING

from emptyos.sdk import web_route

if TYPE_CHECKING:
    from .app import BoardsApp  # noqa: F401 — for type hints only



def _link_record_columns(config: dict) -> list[dict]:
    """Return all link-record columns on a board config, in declaration order."""
    return [c for c in (config.get("columns") or []) if c.get("type") == "link-record"]

# ─── Bind to BoardsApp class as ────────────────────────────────
#   _rebuild_links           = _links._rebuild_links
#   _index_board             = _links._index_board
#   _maintain_link_inverses  = _links._maintain_link_inverses
#   set_link_field           = _links.set_link_field
#   evaluate_collection_items = _links.evaluate_collection_items
#   api_item_backlinks       = _links.api_item_backlinks
#   api_links_rebuild        = _links.api_links_rebuild
#   _reindex_link_edges       = _links._reindex_link_edges
#   _write_inverse_for_column = _links._write_inverse_for_column
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


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
                self._links.register_edge(board_id, item_id, col["id"], target_board, tgt)


def _reindex_link_edges(self, board_id: str, filename: str, link_cols: list[dict], new_item: dict) -> None:
    """Drop this item's stale graph entries and re-register from `new_item`'s
    post-write state. Shared by `_maintain_link_inverses` (whole-item update,
    every link column on the item) and `set_link_field` (one column, called
    by a non-boards writer like `CollectionLibrary`)."""
    new_col_targets = {col["id"]: _as_id_list(new_item.get(col["id"])) for col in link_cols}
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
            self._links.register_edge(board_id, filename, col["id"], target_board, tgt)


async def _write_inverse_for_column(
    self, col: dict, filename: str, old_ids: list[str], new_ids: list[str]
) -> None:
    """Diff `old_ids`/`new_ids` for ONE link-record column and, if it
    declares an `inverse`, push/pull `filename` onto that field on every
    added/removed target — the write-through half of reciprocal-link
    maintenance. Pulled out of `_maintain_link_inverses` so a non-boards
    writer (`CollectionLibrary.set_link_field` call) can drive the same
    logic for a single field without needing a whole-item update shape."""
    inverse = col.get("inverse")
    target_board = col.get("target_board") or ""
    if not inverse or not target_board:
        return

    added = set(new_ids) - set(old_ids)
    removed = set(old_ids) - set(new_ids)
    if not added and not removed:
        return

    tgt_config = self._store.get_board(target_board) or get_preset(target_board)
    if not tgt_config:
        return
    tgt_col = next((c for c in _link_record_columns(tgt_config) if c["id"] == inverse), None)
    if not tgt_col:
        self.log_warn(f"inverse column '{inverse}' not found on '{target_board}'")
        return
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


async def _maintain_link_inverses(
    self,
    board_id: str,
    config: dict,
    filename: str,
    old_item: dict,
    new_item: dict,
    updates: dict,
) -> None:
    """For every link-record column that changed, update the index AND
    write the inverse field on targets (when the column declares one)."""
    link_cols = _link_record_columns(config)
    if not link_cols:
        return

    self._reindex_link_edges(board_id, filename, link_cols, new_item)

    # Inverse-field maintenance: only runs on the subset of link-record
    # columns that were in this update.
    for col in link_cols:
        if col["id"] not in updates:
            continue
        old_targets = _as_id_list(old_item.get(col["id"]))
        new_targets = _as_id_list(new_item.get(col["id"]))
        await self._write_inverse_for_column(col, filename, old_targets, new_targets)


async def set_link_field(
    self, board_id: str, filename: str, col_id: str, new_ids: list[str], old_ids: list[str] | None = None,
) -> None:
    """Entry point for a non-boards writer (`CollectionLibrary`) that already
    wrote `col_id`'s new value to `filename` itself, and now needs the SAME
    reciprocal-inverse + index maintenance a boards-native PATCH would have
    triggered via `_maintain_link_inverses`. `old_ids` is the field's value
    immediately before that write (`[]` for a brand-new item) — the caller
    must supply it since, unlike the boards PATCH path, this runs *after*
    the write already landed, so re-reading the target board's stored value
    here would return the NEW value, not the old one.

    Fails soft (logs, doesn't raise) on a config/column that can't be
    resolved — a caller's own vault write already committed; a link-index
    hiccup here shouldn't turn into a 500 for an otherwise-successful create.
    """
    config = self._store.get_board(board_id) or get_preset(board_id)
    if not config:
        self.log_warn(f"set_link_field: no board config for '{board_id}'")
        return
    col = next((c for c in _link_record_columns(config) if c["id"] == col_id), None)
    if not col:
        self.log_warn(f"set_link_field: '{col_id}' isn't a link-record column on '{board_id}'")
        return
    old_ids = old_ids or []
    self._reindex_link_edges(board_id, filename, [col], {col_id: new_ids})
    await self._write_inverse_for_column(col, filename, old_ids, new_ids)


async def evaluate_collection_items(self, config: dict, items: list[dict]) -> list[dict]:
    """Re-exposure, not new logic — a non-boards reader (`CollectionApp.
    api_collection_list`/`api_collection_get`) needs the same rollup/formula
    evaluation `GET /api/boards/{id}/items` already runs, but can't call
    `evaluate_formulas` directly: that function resolves link-record targets
    via `getattr(app, "_store", None)`, which only exists on a live BoardsApp
    instance — a `CollectionApp` calling it on itself would silently fall
    back to static presets only, missing any dynamically-registered target
    board (which is exactly the collection-app case). Passing `self` (a real
    BoardsApp) here is what makes that resolution work."""
    return await evaluate_formulas(self, config, items)


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
            out.append(
                {
                    "board": from_board,
                    "board_name": config.get("name", from_board),
                    "file": from_item,
                    "col": from_col,
                    "title": it.get(name_col_id) or from_item,
                }
            )
    return {"backlinks": out}


@web_route("POST", "/api/links/rebuild")
async def api_links_rebuild(self, request):
    """Force a full rebuild of the link index."""
    await self._rebuild_links()
    return {"ok": True, **self._links.stats()}
