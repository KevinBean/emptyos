"""Markdown-table ⇄ Grid (de)serialization.

The vault note body is a single markdown table whose header row is the
column labels (``A B C …``) and whose first column is the 1-based row
number. Each interior cell holds the **raw source** (a literal or a
``=formula``) — never a computed value. The table stays hand-editable in
any markdown editor.

Pipes inside a cell are escaped (``\\|``); cells are single-line.
"""

from __future__ import annotations

import re

from .grid import Grid, index_to_col, to_a1

_PIPE_SPLIT_RE = re.compile(r"(?<!\\)\|")


def _escape(cell: str) -> str:
    return cell.replace("\n", " ").replace("|", r"\|")


def _unescape(cell: str) -> str:
    return cell.replace(r"\|", "|").strip()


def serialize(grid: Grid) -> str:
    """Grid → markdown table string."""
    cols, rows = grid.cols, grid.rows
    labels = [index_to_col(c) for c in range(cols)]
    header = "|   | " + " | ".join(labels) + " |"
    sep = "| " + " | ".join(["---"] * (cols + 1)) + " |"
    lines = [header, sep]
    for r in range(1, rows + 1):
        cells = [_escape(grid.raw(to_a1(c, r))) for c in range(cols)]
        lines.append(f"| {r} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def parse(body: str) -> Grid:
    """Markdown table string → Grid. Inverse of :func:`serialize`."""
    table_lines = [ln.strip() for ln in body.splitlines() if ln.strip().startswith("|")]
    if len(table_lines) < 2:
        return Grid(rows=1, cols=1)

    def split_row(line: str) -> list[str]:
        parts = _PIPE_SPLIT_RE.split(line)
        # Drop the leading/trailing empty cells around the outer pipes.
        if parts and parts[0].strip() == "":
            parts = parts[1:]
        if parts and parts[-1].strip() == "":
            parts = parts[:-1]
        return parts

    header = split_row(table_lines[0])
    cols = max(1, len(header) - 1)  # minus the corner cell
    cells: dict[str, str] = {}
    rows = 0
    for line in table_lines[2:]:  # skip header + separator
        fields = split_row(line)
        if not fields:
            continue
        try:
            r = int(fields[0].strip())
        except ValueError:
            continue
        rows = max(rows, r)
        for c, raw in enumerate(fields[1 : cols + 1]):
            val = _unescape(raw)
            if val:
                cells[to_a1(c, r)] = val
    return Grid(cells, rows=max(1, rows), cols=cols)


__all__ = ["serialize", "parse"]
