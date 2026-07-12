"""Dependency-ordered recompute of a calc-sheet grid.

Turns a sparse ``Grid`` of raw cell sources into a ``{A1: computed_value}``
map. Formula cells (raw starts with ``=``) are evaluated in dependency order
via the safe-AST evaluator in ``emptyos.sdk.formulas``; literal cells are
coerced to number/string. Cells in (or downstream of) a dependency cycle
resolve to ``"#CYCLE"``; an un-evaluable formula resolves to ``"#ERR"``.

Ranges (``B2:B10``) can't parse in ``formulas.py`` (``:`` is a slice token),
so this module pre-expands each range into a synthetic list variable before
handing the rewritten expression to the evaluator. All cell scanning happens
*outside* string literals so ``IF(A1>0,"B2","C3")`` neither creates a false
dependency nor gets its string corrupted.
"""

from __future__ import annotations

import re
from typing import Any

from emptyos.sdk import formulas

from .grid import A1_TOKEN_RE, RANGE_TOKEN_RE, Grid, expand_range

_STR_RE = re.compile(r'"[^"]*"|\'[^\']*\'')
_INT_RE = re.compile(r"-?\d+")


def _coerce_literal(raw: str) -> Any:
    s = raw.strip()
    if s == "":
        return ""
    if _INT_RE.fullmatch(s):
        try:
            return int(s)
        except ValueError:
            pass
    try:
        return float(s)
    except ValueError:
        return s


def _outside_string_segments(expr: str) -> list[str]:
    segs, last = [], 0
    for m in _STR_RE.finditer(expr):
        segs.append(expr[last : m.start()])
        last = m.end()
    segs.append(expr[last:])
    return segs


def _map_outside_strings(expr: str, fn) -> str:
    out, last = [], 0
    for m in _STR_RE.finditer(expr):
        out.append(fn(expr[last : m.start()]))
        out.append(m.group(0))
        last = m.end()
    out.append(fn(expr[last:]))
    return "".join(out)


def _referenced_cells(expr: str) -> set[str]:
    """Every A1 cell a formula reads (range-expanded), strings excluded."""
    refs: set[str] = set()
    for seg in _outside_string_segments(expr):
        for m in RANGE_TOKEN_RE.finditer(seg):
            refs.update(expand_range(m.group(0)))
        seg2 = RANGE_TOKEN_RE.sub(" ", seg)
        for m in A1_TOKEN_RE.finditer(seg2):
            refs.add((m.group(1) + m.group(2)).upper())
    return refs


def _topo(formula_refs, deps) -> tuple[list[str], set[str]]:
    """Kahn's algorithm. Returns (eval order, cyclic-or-downstream set)."""
    formula_set = set(formula_refs)
    prereq = {
        n: sorted({d for d in deps.get(n, ()) if d in formula_set})
        for n in formula_set
    }
    indeg = {n: len(prereq[n]) for n in formula_set}
    dependents: dict[str, list[str]] = {n: [] for n in formula_set}
    for n in formula_set:
        for p in prereq[n]:
            dependents[p].append(n)
    queue = sorted(n for n in formula_set if indeg[n] == 0)
    order: list[str] = []
    while queue:
        n = queue.pop()
        order.append(n)
        for d in sorted(dependents[n]):
            indeg[d] -= 1
            if indeg[d] == 0:
                queue.append(d)
    cyclic = {n for n in formula_set if indeg[n] > 0}
    return order, cyclic


def _eval_formula(expr: str, computed: dict) -> Any:
    ctx: dict[str, Any] = {}
    counter = [0]

    def sub_seg(seg: str) -> str:
        def repl(m):
            name = f"rng__{counter[0]}"
            counter[0] += 1
            ctx[name] = [computed.get(c, "") for c in expand_range(m.group(0))]
            return name

        seg2 = RANGE_TOKEN_RE.sub(repl, seg)
        for m in A1_TOKEN_RE.finditer(seg2):
            ref = (m.group(1) + m.group(2)).upper()
            ctx.setdefault(ref, computed.get(ref, ""))
        return seg2

    work = _map_outside_strings(expr, sub_seg)
    return formulas.evaluate(work, ctx, default="#ERR")


def recompute(grid: Grid) -> dict[str, Any]:
    """Compute every cell's value. Pure function of the grid."""
    formula_exprs: dict[str, str] = {}
    computed: dict[str, Any] = {}
    for ref, cell in grid.cells.items():
        raw = cell["raw"]
        if raw.startswith("="):
            formula_exprs[ref] = raw[1:]
        else:
            computed[ref] = _coerce_literal(raw)

    deps = {ref: _referenced_cells(expr) for ref, expr in formula_exprs.items()}
    order, cyclic = _topo(formula_exprs.keys(), deps)

    for ref in order:
        computed[ref] = _eval_formula(formula_exprs[ref], computed)
    for ref in cyclic:
        computed[ref] = "#CYCLE"

    return computed


__all__ = ["recompute"]
