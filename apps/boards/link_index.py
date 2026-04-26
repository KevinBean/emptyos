"""In-memory link graph across boards.

Tracks outgoing references (item A's link-record columns → item B on another
board) and back-references (who points at me). Rebuilt on boot by walking
every board; kept live by `board:item_created`, `board:item_updated`,
`board:item_deleted` events.

Generic API shape (collection_id, item_id) so a future cross-app link index
can absorb this without a breaking change. For v1, collection_id == board_id.
"""

from __future__ import annotations

from typing import Any


def _as_id_list(value: Any) -> list[str]:
    """Coerce a field value into a list of IDs. Accepts list, str, or None."""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v]
    if isinstance(value, str):
        # Comma-separated fallback for legacy input.
        return [s.strip() for s in value.split(",") if s.strip()]
    return [str(value)]


class LinkIndex:
    """Process-wide link graph. One instance per BoardsApp."""

    def __init__(self):
        # outgoing[board][item] = {col_id: [target_ids]}
        self._outgoing: dict[str, dict[str, dict[str, list[str]]]] = {}
        # incoming[board][item] = [(from_board, from_item, from_col), ...]
        self._incoming: dict[str, dict[str, list[tuple[str, str, str]]]] = {}

    # ── Query API ─────────────────────────────────────────────────────

    def outgoing(self, board: str, item: str) -> dict[str, list[str]]:
        return dict(self._outgoing.get(board, {}).get(item, {}))

    def incoming(self, board: str, item: str) -> list[tuple[str, str, str]]:
        return list(self._incoming.get(board, {}).get(item, []))

    # ── Mutation API (called by app.py on events) ─────────────────────

    def set_item(self, board: str, item: str, col_targets: dict[str, list[str]]) -> None:
        """Replace the outgoing edge set for one item. Rewrites back-refs to
        match. Call with the item's full link-record column state; partial
        updates would orphan edges."""
        old_outgoing = self._outgoing.get(board, {}).get(item, {})

        # Compute old and new flat edge sets: (col, target).
        old_edges = {(col, tgt) for col, tgts in old_outgoing.items() for tgt in tgts}
        new_edges = {(col, tgt) for col, tgts in col_targets.items() for tgt in tgts if tgt}

        # Remove dropped back-references.
        for col, tgt in old_edges - new_edges:
            # We don't know the target board here without column config; the
            # incoming index is keyed by target board, so we scan to remove.
            for tgt_board, items in self._incoming.items():
                ref = (board, item, col)
                if tgt in items and ref in items[tgt]:
                    items[tgt].remove(ref)
                    if not items[tgt]:
                        items.pop(tgt, None)

        # Record new outgoing.
        clean = {col: [t for t in tgts if t] for col, tgts in col_targets.items() if tgts}
        if clean:
            self._outgoing.setdefault(board, {})[item] = clean
        else:
            self._outgoing.get(board, {}).pop(item, None)

        # Add new back-references. We don't resolve target_board here either;
        # callers pass (col → target_board) via register_edge() when known.

    def register_edge(self, from_board: str, from_item: str, from_col: str,
                      to_board: str, to_item: str) -> None:
        """Record a single outgoing + incoming edge. Used during rebuild and
        after set_item to populate the target side with board info."""
        out = self._outgoing.setdefault(from_board, {}).setdefault(from_item, {})
        targets = out.setdefault(from_col, [])
        if to_item not in targets:
            targets.append(to_item)

        inc = self._incoming.setdefault(to_board, {}).setdefault(to_item, [])
        ref = (from_board, from_item, from_col)
        if ref not in inc:
            inc.append(ref)

    def remove_item(self, board: str, item: str) -> None:
        """Item deleted — drop outgoing + back-references that target it."""
        self._outgoing.get(board, {}).pop(item, None)

        for tgt_board, items in list(self._incoming.items()):
            items.pop(item, None) if tgt_board == board else None
            for tgt_item, refs in list(items.items()):
                items[tgt_item] = [r for r in refs if not (r[0] == board and r[1] == item)]
                if not items[tgt_item]:
                    items.pop(tgt_item, None)

    def clear_board(self, board: str) -> None:
        """Reset a board's own outgoing entries + any back-references pointing
        FROM this board. Does NOT touch `incoming[board]` — those entries are
        populated by OTHER boards (back-refs pointing AT this board), and
        wiping them would destroy work already done when those boards were
        indexed earlier in the rebuild pass."""
        self._outgoing.pop(board, None)
        # Drop incoming entries pointing FROM this board toward others.
        for _, items in self._incoming.items():
            for tgt_item in list(items.keys()):
                items[tgt_item] = [r for r in items[tgt_item] if r[0] != board]
                if not items[tgt_item]:
                    items.pop(tgt_item, None)

    def clear(self) -> None:
        self._outgoing.clear()
        self._incoming.clear()

    # ── Introspection ─────────────────────────────────────────────────

    def stats(self) -> dict:
        edges = sum(
            len(tgts) for board in self._outgoing.values()
            for item in board.values() for tgts in item.values()
        )
        return {
            "boards_with_outgoing": len(self._outgoing),
            "items_with_outgoing": sum(len(b) for b in self._outgoing.values()),
            "total_edges": edges,
        }
