"""sheet — pure, kernel-free calc-sheet engine.

Cell addressing (A1 ⇄ index), range expansion, and dependency-ordered
recompute over the safe-AST function set in ``emptyos.sdk.formulas``. No
``self``, no I/O, no daemon — unit-tested under ``engines/sheet/tests/``.

Governing standard: none — spreadsheet formula-evaluation tooling; non-engineering.

The app layer (``apps/public/standard/sheet/``) owns vault storage + HTTP;
this engine only turns a sparse ``{A1: {"raw": "..."}}`` grid into a
``{A1: computed_value}`` map.
"""

from .grid import Grid, expand_range, parse_a1, to_a1
from .markdown_io import parse as parse_table
from .markdown_io import serialize as serialize_table
from .recompute import recompute

__all__ = [
    "Grid",
    "parse_a1",
    "to_a1",
    "expand_range",
    "recompute",
    "serialize_table",
    "parse_table",
]
