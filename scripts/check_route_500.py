#!/usr/bin/env python3
"""A refused path segment inside a route must answer in-band, never 500.

``emptyos.sdk.utils.require_path_segment`` **raises** on a refused id — that is
its documented job, because it is "what a *path builder* wants: the single
choke-point every call site funnels through". Inside a ``@web_route`` handler
the raise becomes an unhandled 500.

That matters because the same handler almost always already answers
``{"error": "no such thing"}`` for an id that is merely *unknown*. So one user
mistake wears two different shapes depending on which character was typed:

    GET /api/items/zzz-nope   -> 200 {"error": "item not found"}
    GET /api/items/nul        -> 500 Internal Server Error

The SDK ships the fix as a sibling helper. ``path_segment_error(raw, label)``
returns the refusal message instead of raising, and its own docstring says it
exists "for route handlers that answer with an in-band ``{"error": ...}``
instead of letting an exception become a 500".

Found 2026-08-17 while writing a smoke test for one app; the same shape was
then live in a second (``read-the-room``, 4 routes, all reachable). Both fixed.
This is the guard so a third does not appear unnoticed.

**The rule is deliberately narrow.** It fires only when a ``@web_route``
function calls ``require_path_segment`` with *neither* a surrounding ``try``
*nor* a ``path_segment_error`` consultation anywhere in the function. It does
not look at the argument. An earlier draft required the argument to mention
``request.``, which measured the same 4 findings but would have missed a route
that stashes the input in a local first::

    raw = request.path_params.get("node_id") or ""
    nid = require_path_segment(raw)          # invisible to the arg-shape rule

— which is exactly the shape one of the two fixes uses, so the narrower rule
was silently blind to the code that motivated it.

Both directions are pinned in ``tests/test_unit_check_route_500.py``: the two
real pre-fix shapes flag, and guarded / try-wrapped / non-route path builders
pass. 0 findings on a healthy tree.

Scope note: a non-route *path builder* that raises is correct and is not
flagged — raising is the point there. Only the HTTP boundary is in scope.

Opt out at the call site with a comment in the function::

    # route-500: ignore — this id is generated, never user-supplied

Static AST; no kernel, no daemon. Personal apps are included — both real
findings were in personal apps.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scanner_lib import emit_json  # noqa: E402

from emptyos.sdk.app_layout import iter_app_dirs  # noqa: E402

RAISING = "require_path_segment"
GUARD = "path_segment_error"
IGNORE = re.compile(r"route-500:\s*ignore\b", re.I)


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _is_route(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any("web_route" in ast.unparse(d) for d in fn.decorator_list)


def unguarded_routes(src: str) -> list[tuple[int, str]]:
    """``[(lineno, func_name)]`` for routes that can 500 on a refused segment.

    Pure over source text so the test can drive it with synthetic cases rather
    than fixture files on disk.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []

    out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _is_route(fn):
            continue
        body = ast.unparse(fn)
        if RAISING not in body:
            continue
        # A try means the handler owns the failure, whatever it does with it.
        if any(isinstance(n, ast.Try) for n in ast.walk(fn)):
            continue
        # Consulting the in-band sibling anywhere in the function is the fix.
        if GUARD in body:
            continue
        if IGNORE.search(ast.get_docstring(fn) or ""):
            continue
        out.append((fn.lineno, fn.name))
    return out


def scan(apps_root: Path) -> tuple[int, list[dict]]:
    files = set()
    for _name, d in iter_app_dirs(apps_root, include_personal=True):
        files.update(d.rglob("*.py"))
    files.update((ROOT / "emptyos").rglob("*.py"))

    scanned, findings = 0, []
    for f in sorted(files):
        if "__pycache__" in str(f):
            continue
        try:
            src = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if RAISING not in src:
            continue
        scanned += 1
        # A file-level marker exempts the whole module.
        if IGNORE.search(src.split("\n\n", 1)[0]):
            continue
        for lineno, name in unguarded_routes(src):
            findings.append({"file": _rel(f), "line": lineno, "func": name})
    return scanned, findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="agent-cli envelope on stdout")
    args = ap.parse_args()

    scanned, findings = scan(ROOT / "apps")
    ok = not findings
    if args.json:
        return emit_json(
            ok,
            "ok" if ok else "unguarded_route_segment",
            f"{len(findings)} route(s) can 500 on a refused path segment"
            if findings else f"{scanned} file(s) using {RAISING}, every route guarded",
            {"scanned": scanned, "findings": findings},
        )

    if ok:
        print(f"check-route-500: OK — {scanned} file(s) using {RAISING}, "
              f"every @web_route guarded")
        return 0
    print(f"check-route-500: {len(findings)} route(s) call {RAISING} on a path "
          f"segment with no guard — a refused id 500s instead of answering\n")
    for f in findings:
        print(f"  {f['file']}:{f['line']}  {f['func']}()")
    print(f"\n  fix: consult {GUARD}(raw, label) first and return "
          f'{{"error": msg}}, matching the handler\'s own not-found shape.')
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
