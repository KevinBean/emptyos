"""Pure node-placement helpers for canvas operations."""

from __future__ import annotations


def below_cluster(nodes: list[dict], gap: int = 40) -> tuple[int, int]:
    """Return the (x, y) for a new node placed below the existing cluster.

    Empty board → (0, 0). Otherwise: x = leftmost existing x; y = max
    (y + height) + gap so the new node doesn't overlap.
    """
    if not nodes:
        return 0, 0
    min_x = min(int(n.get("x", 0)) for n in nodes)
    max_y = max(int(n.get("y", 0)) + int(n.get("height", 200)) for n in nodes)
    return min_x, max_y + gap


def column_right_of(
    src: dict, count: int, gap_x: int = 100, gap_y: int = 20
) -> list[tuple[int, int]]:
    """Return ``count`` (x, y) tuples stacked in a column to the right of ``src``."""
    sx = int(src.get("x", 0)) + int(src.get("width", 250)) + gap_x
    sy = int(src.get("y", 0))
    sh = int(src.get("height", 200))
    return [(sx, sy + i * (sh + gap_y)) for i in range(count)]
