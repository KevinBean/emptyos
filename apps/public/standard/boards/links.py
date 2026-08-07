"""boards — the relation graph between items — backlinks and inverse maintenance.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: The link index: rebuilding it, indexing one board, keeping relation columns symmetric when one side changes, and answering backlink queries. Separate from items because a link is a fact about two items, not a property of one.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._lib / self._config_for (spine); the LinkIndex in link_index.py.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from .board_engine import DynamicBoardLibrary
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
#   api_item_backlinks       = _links.api_item_backlinks
#   api_links_rebuild        = _links.api_links_rebuild
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

    # Refresh this item's outgoing entry from the post-write state.
    new_col_targets = {col["id"]: _as_id_list(new_item.get(col["id"])) for col in link_cols}
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
            self._links.register_edge(board_id, filename, col["id"], target_board, tgt)

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
        tgt_col = next(
            (c for c in _link_record_columns(tgt_config) if c["id"] == inverse), None
        )
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
