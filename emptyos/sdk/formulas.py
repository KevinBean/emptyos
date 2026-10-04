"""Safe formula evaluator for typed-record apps.

Pure Python AST walker — no `eval`, no regex-shelled arithmetic. Reusable by
any app that needs conditional logic over a dict-shaped record (boards, CRM,
finance, etc.).

Grammar (subset of Python expressions):
    literal      :=  number | string | True | False | None
    field        :=  identifier                          # looked up in `item`
    attribute    :=  expr . identifier                   # dict-get, or map-on-list
    call         :=  FN ( expr, expr, ... )
    op           :=  + - * / == != < <= > >= and or not unary-
    grouping     :=  ( expr )

Supported functions (case-insensitive):
    SUM, AVG, COUNT, MIN, MAX                — aggregates over iterables
    EARLIEST(list), LATEST(list)             — min/max over ISO dates (as dates)
    COUNT_UNIQUE(list)                        — distinct non-empty values
    PERCENT_CHECKED(list)                     — 0-100, share of checked values
    IF(cond, a, b)                            — ternary
    IS_EMPTY(x)                               — true for None / '' / []
    TODAY()                                   — today's ISO date string
    LOOKUP(link_col, 'field')                 — first linked item's field
    CONCAT(a, b, ...)                         — string join
    LEN(x)                                    — length
    ROUND(x, n=0)                             — round to n decimals

Link-walking: when `deliverables` is a list of resolved item dicts in `ctx`,
the expression `deliverables.progress` returns a list of progress values
(one per linked item). `SUM(deliverables.weight_hours)` reduces.

Errors return `"#ERR"` by default so a bad formula never crashes a table.
"""

from __future__ import annotations

import ast
import re
from datetime import date
from typing import Any

# Accept SQL-style keywords (AND, OR, NOT) by rewriting to Python-style
# before parsing. Case-insensitive whole-word match. Strings are left alone
# so `IF(x, "AND", "OR")` survives the rewrite.
_KEYWORD_RE = re.compile(r'("[^"]*"|\'[^\']*\'|\b(?:AND|OR|NOT)\b)', re.IGNORECASE)


def _normalize_keywords(expr: str) -> str:
    def sub(m):
        tok = m.group(0)
        if tok.startswith(("'", '"')):
            return tok
        return tok.lower()

    return _KEYWORD_RE.sub(sub, expr)


class FormulaError(Exception):
    pass


# ── Built-in functions ────────────────────────────────────────────────


def _as_iterable(x: Any):
    if x is None:
        return []
    if isinstance(x, (list, tuple)):
        return x
    return [x]


def _flatten_args(args):
    """Flatten aggregate call args into a flat value list.

    Each positional arg is expanded via `_as_iterable`, so a single list arg
    (`SUM(col.field)`) and discrete args (`SUM(A1, A2, A3)`) both reduce. A
    cell range pre-expanded into a synthetic list var stays one arg → its
    members are flattened in.
    """
    out = []
    for a in args:
        out.extend(_as_iterable(a))
    return out


def _to_number(x: Any) -> float:
    if isinstance(x, bool):
        return 1.0 if x else 0.0
    if isinstance(x, (int, float)):
        return float(x)
    try:
        s = str(x).strip()
        # Strip currency-like prefixes/suffixes — keep digits, dot, minus.
        cleaned = "".join(c for c in s if c.isdigit() or c in ".-")
        return float(cleaned) if cleaned else 0.0
    except (ValueError, TypeError):
        return 0.0


def _fn_sum(*args):
    return sum(_to_number(v) for v in _flatten_args(args))


def _fn_avg(*args):
    items = [_to_number(v) for v in _flatten_args(args)]
    return sum(items) / len(items) if items else 0.0


def _fn_count(*args):
    return len([v for v in _flatten_args(args) if v not in (None, "")])


def _fn_min(*args):
    items = [_to_number(v) for v in _flatten_args(args)]
    return min(items) if items else 0.0


def _fn_max(*args):
    items = [_to_number(v) for v in _flatten_args(args)]
    return max(items) if items else 0.0


def _fn_if(cond, a, b):
    return a if bool(cond) else b


def _fn_is_empty(x):
    if x is None or x == "":
        return True
    if isinstance(x, (list, tuple, dict)) and len(x) == 0:
        return True
    return False


def _fn_today():
    return date.today().isoformat()


def _fn_concat(*args):
    return "".join(str(a) for a in args if a is not None)


def _fn_len(x):
    return len(_as_iterable(x))


def _fn_round(x, n=0):
    return round(_to_number(x), int(_to_number(n)))


def _fn_lookup(link_col_value, field):
    """LOOKUP(col, 'field') — col is the resolved link list; return first.field."""
    items = _as_iterable(link_col_value)
    if not items:
        return ""
    first = items[0]
    if isinstance(first, dict):
        return first.get(field, "")
    return ""


# ── Rollup-flavoured aggregates ───────────────────────────────────────
# MIN/MAX coerce to numbers, so an ISO date collapses to 0 — these keep dates
# as dates. COUNT_UNIQUE / PERCENT_CHECKED close the remaining Notion-rollup
# gaps over a linked field.

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")

# Values a checkbox/boolean field reads as "checked" (it's stored as a string).
_TRUTHY = {"true", "1", "yes", "y", "checked", "done", "x", "✓", "☑"}


def _dates(args) -> list[str]:
    """ISO date strings from the args (empties/non-dates dropped)."""
    out = []
    for v in _flatten_args(args):
        s = str(v).strip()
        if _DATE_RE.match(s):
            out.append(s[:10])
    return out


def _fn_earliest(*args):
    """EARLIEST(list) — the smallest ISO date (lexical == chronological). '' if none."""
    ds = _dates(args)
    return min(ds) if ds else ""


def _fn_latest(*args):
    """LATEST(list) — the largest ISO date. '' if none."""
    ds = _dates(args)
    return max(ds) if ds else ""


def _fn_count_unique(*args):
    """COUNT_UNIQUE(list) — number of distinct non-empty values (case-folded)."""
    seen = {str(v).strip().lower() for v in _flatten_args(args) if str(v).strip()}
    return len(seen)


def _fn_percent_checked(*args):
    """PERCENT_CHECKED(list) — 0-100, the share of values that read as checked."""
    vals = [v for v in _flatten_args(args) if v not in (None, "")]
    if not vals:
        return 0.0
    checked = sum(1 for v in vals if str(v).strip().lower() in _TRUTHY)
    return round(100.0 * checked / len(vals), 1)


_FUNCTIONS: dict[str, callable] = {
    "SUM": _fn_sum,
    "AVG": _fn_avg,
    "COUNT": _fn_count,
    "MIN": _fn_min,
    "MAX": _fn_max,
    "IF": _fn_if,
    "IS_EMPTY": _fn_is_empty,
    "TODAY": _fn_today,
    "CONCAT": _fn_concat,
    "LEN": _fn_len,
    "ROUND": _fn_round,
    "LOOKUP": _fn_lookup,
    "EARLIEST": _fn_earliest,
    "LATEST": _fn_latest,
    "COUNT_UNIQUE": _fn_count_unique,
    "PERCENT_CHECKED": _fn_percent_checked,
}


# ── Evaluator ─────────────────────────────────────────────────────────

_ALLOWED_NODES = (
    ast.Expression,
    ast.Constant,
    ast.Name,
    ast.Attribute,
    ast.Call,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.Pow,
    ast.FloorDiv,
    ast.USub,
    ast.UAdd,
    ast.Not,
    ast.And,
    ast.Or,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.Load,
)


def _eval(node: Any, ctx: dict) -> Any:
    if isinstance(node, ast.Expression):
        return _eval(node.body, ctx)

    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.Name):
        if node.id in ctx:
            return ctx[node.id]
        # Allow TRUE/FALSE/NULL aliases.
        upper = node.id.upper()
        if upper == "TRUE":
            return True
        if upper == "FALSE":
            return False
        if upper == "NULL" or upper == "NONE":
            return None
        return ""  # missing field → empty string (non-crashy default)

    if isinstance(node, ast.Attribute):
        owner = _eval(node.value, ctx)
        attr = node.attr
        # Link-walking: if owner is a list of dicts, map attribute lookup.
        if isinstance(owner, list):
            return [x.get(attr, "") if isinstance(x, dict) else "" for x in owner]
        if isinstance(owner, dict):
            return owner.get(attr, "")
        return ""

    if isinstance(node, ast.Call):
        fn_name = node.func.id if isinstance(node.func, ast.Name) else None
        if not fn_name:
            raise FormulaError("function name must be an identifier")
        fn = _FUNCTIONS.get(fn_name.upper())
        if not fn:
            raise FormulaError(f"unknown function: {fn_name}")
        args = [_eval(a, ctx) for a in node.args]
        kwargs = {kw.arg: _eval(kw.value, ctx) for kw in (node.keywords or [])}
        return fn(*args, **kwargs)

    if isinstance(node, ast.BinOp):
        left = _eval(node.left, ctx)
        right = _eval(node.right, ctx)
        op = node.op
        ln, rn = _to_number(left), _to_number(right)
        if isinstance(op, ast.Add):
            return ln + rn
        if isinstance(op, ast.Sub):
            return ln - rn
        if isinstance(op, ast.Mult):
            return ln * rn
        if isinstance(op, ast.Div):
            return ln / rn if rn else 0
        if isinstance(op, ast.Mod):
            return ln % rn if rn else 0
        if isinstance(op, ast.Pow):
            return ln**rn
        if isinstance(op, ast.FloorDiv):
            return ln // rn if rn else 0
        raise FormulaError(f"unsupported binary op: {type(op).__name__}")

    if isinstance(node, ast.UnaryOp):
        operand = _eval(node.operand, ctx)
        if isinstance(node.op, ast.USub):
            return -_to_number(operand)
        if isinstance(node.op, ast.UAdd):
            return +_to_number(operand)
        if isinstance(node.op, ast.Not):
            return not bool(operand)
        raise FormulaError(f"unsupported unary op: {type(node.op).__name__}")

    if isinstance(node, ast.BoolOp):
        values = [_eval(v, ctx) for v in node.values]
        if isinstance(node.op, ast.And):
            result = True
            for v in values:
                result = result and v
                if not result:
                    return result
            return result
        if isinstance(node.op, ast.Or):
            for v in values:
                if v:
                    return v
            return values[-1] if values else False
        raise FormulaError(f"unsupported bool op: {type(node.op).__name__}")

    if isinstance(node, ast.Compare):
        left = _eval(node.left, ctx)
        for op, comparator in zip(node.ops, node.comparators, strict=False):
            right = _eval(comparator, ctx)
            if isinstance(op, ast.Eq):
                ok = left == right
            elif isinstance(op, ast.NotEq):
                ok = left != right
            else:
                # Prefer string comparison when both sides are strings (so
                # ISO dates like "2020-01-01" order correctly). Fall back
                # to numeric coercion otherwise.
                if isinstance(left, str) and isinstance(right, str):
                    a, b = left, right
                else:
                    a, b = _to_number(left), _to_number(right)
                if isinstance(op, ast.Lt):
                    ok = a < b
                elif isinstance(op, ast.LtE):
                    ok = a <= b
                elif isinstance(op, ast.Gt):
                    ok = a > b
                elif isinstance(op, ast.GtE):
                    ok = a >= b
                else:
                    raise FormulaError(f"unsupported comparison op: {type(op).__name__}")
            if not ok:
                return False
            left = right
        return True

    raise FormulaError(f"unsupported node: {type(node).__name__}")


def evaluate(expression: str, context: dict, *, default: Any = "#ERR") -> Any:
    """Parse and evaluate `expression` against `context`.

    `context` is a dict of the current record's fields. Link-record columns
    should have their values pre-resolved to a list of target item dicts, so
    `col.field` attribute-access works. Unknown fields resolve to empty
    string (not an error).

    Returns `default` on any parse/eval failure. Pass `default=None` or a
    raising sentinel to see errors during development.
    """
    if not expression:
        return default if default is not None else ""
    try:
        tree = ast.parse(_normalize_keywords(expression), mode="eval")
    except SyntaxError:
        return default

    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            return default

    try:
        return _eval(tree, context)
    except Exception:
        return default


def format_result(value: Any) -> str:
    """Stringify an evaluator result for table display."""
    if isinstance(value, bool):
        return "✓" if value else ""
    if isinstance(value, float):
        if value == int(value):
            return str(int(value))
        return f"{value:.2f}"
    if isinstance(value, (list, tuple)):
        return ", ".join(format_result(v) for v in value)
    return str(value) if value is not None else ""


__all__ = ["evaluate", "format_result", "FormulaError"]
