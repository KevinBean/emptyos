"""Cell addressing + range expansion for the calc sheet.

A1 notation: columns are letters (A, B, …, Z, AA, AB, …), rows are 1-based
integers. Internally columns are 0-based ints. A ``Grid`` is a sparse dict
keyed by uppercase A1 string → ``{"raw": "<source>"}`` where source is a
literal (``"42"``, ``"hello"``) or a formula (``"=SUM(A1:A3)"``).
"""

from __future__ import annotations

import re

_A1_RE = re.compile(r"^([A-Za-z]+)([1-9][0-9]*)$")
# A single A1 reference token, used to scan formula bodies. Bounded so it
# doesn't match the column letters inside a function name like SUM.
A1_TOKEN_RE = re.compile(r"\b([A-Za-z]{1,3})([1-9][0-9]*)\b")
# A range token "B2:B10".
RANGE_TOKEN_RE = re.compile(
    r"\b([A-Za-z]{1,3}[1-9][0-9]*):([A-Za-z]{1,3}[1-9][0-9]*)\b"
)


def col_to_index(col: str) -> int:
    """'A' -> 0, 'Z' -> 25, 'AA' -> 26."""
    col = col.upper()
    n = 0
    for ch in col:
        if not ("A" <= ch <= "Z"):
            raise ValueError(f"bad column: {col!r}")
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def index_to_col(idx: int) -> str:
    """0 -> 'A', 25 -> 'Z', 26 -> 'AA'."""
    if idx < 0:
        raise ValueError(f"bad column index: {idx}")
    out = ""
    idx += 1
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


def parse_a1(ref: str) -> tuple[int, int]:
    """'B2' -> (col=1, row=2). Row is 1-based, col is 0-based."""
    m = _A1_RE.match(ref.strip())
    if not m:
        raise ValueError(f"bad A1 reference: {ref!r}")
    return col_to_index(m.group(1)), int(m.group(2))


def to_a1(col: int, row: int) -> str:
    """(col=1, row=2) -> 'B2'."""
    if row < 1:
        raise ValueError(f"bad row: {row}")
    return f"{index_to_col(col)}{row}"


def expand_range(rng: str) -> list[str]:
    """'B2:C3' -> ['B2','B3','C2','C3'] (column-major)."""
    parts = rng.split(":")
    if len(parts) != 2:
        raise ValueError(f"bad range: {rng!r}")
    c1, r1 = parse_a1(parts[0])
    c2, r2 = parse_a1(parts[1])
    lo_c, hi_c = sorted((c1, c2))
    lo_r, hi_r = sorted((r1, r2))
    cells = []
    for c in range(lo_c, hi_c + 1):
        for r in range(lo_r, hi_r + 1):
            cells.append(to_a1(c, r))
    return cells


class Grid:
    """Sparse cell store keyed by A1 string.

    ``cells`` maps ``"A1" -> {"raw": "<source>"}``. ``rows``/``cols`` are the
    declared dimensions (for the UI); the cell dict only holds non-empty cells.
    """

    def __init__(self, cells: dict | None = None, rows: int = 1, cols: int = 1):
        self.cells: dict[str, dict] = {}
        for k, v in (cells or {}).items():
            key = k.upper()
            if isinstance(v, dict):
                self.cells[key] = {"raw": str(v.get("raw", ""))}
            else:
                self.cells[key] = {"raw": str(v)}
        self.rows = max(1, int(rows))
        self.cols = max(1, int(cols))

    def raw(self, ref: str) -> str:
        cell = self.cells.get(ref.upper())
        return cell["raw"] if cell else ""

    def set(self, ref: str, raw: str) -> None:
        ref = ref.upper()
        parse_a1(ref)  # validate
        raw = "" if raw is None else str(raw)
        if raw == "":
            self.cells.pop(ref, None)
        else:
            self.cells[ref] = {"raw": raw}

    def raw_grid(self) -> dict[str, str]:
        """Flat {A1: raw} of non-empty cells, for serialization/transport."""
        return {k: v["raw"] for k, v in self.cells.items()}


__all__ = [
    "Grid",
    "parse_a1",
    "to_a1",
    "expand_range",
    "col_to_index",
    "index_to_col",
    "A1_TOKEN_RE",
    "RANGE_TOKEN_RE",
]
