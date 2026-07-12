"""Runbook — safe expression evaluator for ``calculate`` blocks.

Pure module (no ``self``, no I/O). Evaluates a single Python *expression* over
named upstream outputs WITHOUT ``eval`` of arbitrary code. Two guards compose:

  1. AST whitelist — only the node types in ``_ALLOWED`` survive; attribute
     access to dunders (``__globals__`` …) and method-style calls are rejected
     statically.
  2. Empty ``__builtins__`` at eval time — even if a node slipped through, no
     builtin (``open``, ``__import__`` …) is reachable; only the whitelisted
     functions + bound names exist.

Specific-first per CLAUDE.md rule 9: lives in the runbook app. Extract to
``emptyos/sdk/safe_expr.py`` only when a second consumer needs it.
"""

from __future__ import annotations

import ast

# Whitelisted callables exposed as names (no builtins beyond these).
_FUNCS = {
    "sum": sum, "min": min, "max": max, "len": len, "abs": abs,
    "round": round, "sorted": sorted, "any": any, "all": all,
    "float": float, "int": int, "str": str, "list": list,
    "dict": dict, "set": set, "bool": bool, "tuple": tuple,
}

_ALLOWED = (
    ast.Expression, ast.Constant, ast.Name, ast.Load,
    ast.Store,  # comprehension loop-var targets only (no assignment in eval mode)
    ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp,
    ast.Call, ast.List, ast.Tuple, ast.Dict, ast.Set,
    ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.comprehension,
    ast.Subscript, ast.Slice, ast.Attribute,
    # operators
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd, ast.Not, ast.And, ast.Or,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.In, ast.NotIn, ast.Is, ast.IsNot,
)


class CalcError(Exception):
    pass


class _Row(dict):
    """dict with attribute access so ``r.amount`` works in expressions."""
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError as e:
            raise AttributeError(k) from e


def _wrap(v):
    if isinstance(v, dict):
        return _Row({k: _wrap(x) for k, x in v.items()})
    if isinstance(v, list):
        return [_wrap(x) for x in v]
    return v


def _validate(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise CalcError(f"disallowed syntax: {type(node).__name__}")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise CalcError("dunder/private attribute access is forbidden")
        if isinstance(node, ast.Call):
            # Only direct calls of a bare name (a whitelisted function). No
            # method calls (x.foo()), no call-of-call (f()()).
            if not isinstance(node.func, ast.Name):
                raise CalcError("only direct function calls are allowed")
            if node.keywords:
                raise CalcError("keyword arguments are not allowed")


def safe_eval(expr: str, names: dict):
    """Evaluate ``expr`` with ``names`` bound (each value is wrapped so dict
    attribute access works). Raises CalcError on disallowed syntax or any
    runtime failure."""
    expr = (expr or "").strip()
    if not expr:
        raise CalcError("empty expression")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise CalcError(f"syntax error: {e}") from e
    _validate(tree)
    env = dict(_FUNCS)
    for k, v in (names or {}).items():
        env[k] = _wrap(v)
    try:
        return eval(  # noqa: S307 — guarded: AST-whitelisted + empty builtins
            compile(tree, "<calc>", "eval"), {"__builtins__": {}}, env,
        )
    except CalcError:
        raise
    except Exception as e:  # noqa: BLE001
        raise CalcError(f"{type(e).__name__}: {e}") from e
