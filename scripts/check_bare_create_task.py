#!/usr/bin/env python3
"""Find fire-and-forget `asyncio.create_task` calls that keep no strong reference.

asyncio holds only a WEAK reference to a running task:

    "Save a reference to the result of this function, to avoid a task
     disappearing mid-execution."  — asyncio.create_task docs

A detached task whose only reference is the local returned by `create_task`
can therefore be garbage-collected before it finishes. It fails silently, more
often under memory pressure, and leaves nothing in the log to find later.

`BaseApp.spawn_background(coro, label=...)` is the fix: it keeps the reference,
cancels on teardown, closes a coroutine cancelled before it started, and logs
failures instead of letting them vanish into a never-retrieved exception.

Found during an eos-simplify pass on 2026-08-05 after the same hazard was
written twice in one session — once fresh, once inherited by copying a
neighbouring app. That is the signature of something a scanner should hold
rather than a habit.

WHAT IT FLAGS — only sites where the fix is actually available:

  * the call's result is discarded (not assigned, not added to a collection);
  * the enclosing function has `self` or `app` as its first parameter, so
    `spawn_background` is reachable. A plain helper class has no app to reach,
    and a long-lived process drain is not a warm-up — neither is flagged.

Opt out at the call site (never a central allowlist — a list turns every new
legitimate case into a build break):

    asyncio.create_task(...)   # noqa: eos-bgtask  (reason)

Exit code = number of findings. Registered gate=False in preflight: the hazard
is latent, and a legitimate detached task in a class with no app reference is
not a build break.

Pure file I/O + AST. No kernel import.

Usage::

    python scripts/check_bare_create_task.py
    python scripts/check_bare_create_task.py --json
    python scripts/check_bare_create_task.py --roots apps emptyos plugins
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

from check_base import REPO_ROOT
from scanner_lib import emit_json

DEFAULT_ROOTS = ["apps", "emptyos", "plugins"]
OPT_OUT = re.compile(r"#\s*noqa:\s*eos-bgtask", re.I)


def _retained(node: ast.AST) -> bool:
    """True when the create_task result is kept somewhere.

    An Assign (`self._task = ...`), an Expr wrapping `.add(...)`/`.append(...)`,
    a return, an await, or use as a call argument all mean someone holds it.
    Only a bare expression-statement drops it on the floor.
    """
    return not isinstance(node, ast.Expr)


def _params(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    return [a.arg for a in fn.args.args]


def _asyncio_aliases(tree: ast.AST) -> set[str]:
    """Every local name bound to the asyncio module in this file.

    `import asyncio as _asyncio` inside a function is a real pattern here (four
    files use it, usually to keep a lazy import out of module scope). Matching
    only the literal name `asyncio` missed them — including
    `reactor/reactions_system.py`, whose own comment says "create_task swallows
    exceptions silently" and hand-rolls the wrapper this helper exists to be.
    Found by the wrapup's Class-B doc scan, not by the scanner itself.
    """
    names = {"asyncio"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "asyncio" and a.asname:
                    names.add(a.asname)
    return names


# A `self` that actually carries spawn_background: an app, a plugin, or a mixin
# destined to be mixed into one.
_APP_BASES = ("BaseApp", "BasePlugin")


def _self_is_an_app(cls: ast.ClassDef | None) -> bool:
    """True when `self` in this class has spawn_background.

    `cls is None` means a module-level `def f(self, ...)` — the multi-module
    bound-helper pattern (.claude/rules/multi-module-apps.md), where `self` is
    the app the function is re-bound onto.

    Getting this wrong is not theoretical: the first cut of this scanner tested
    only "is the first parameter named self", flagged `SubAgentTool.run` in
    `emptyos/sdk/agent_tools/subagent.py`, and the mechanical sweep rewrote it
    to `self.spawn_background` on a class that has no such method. It would have
    raised AttributeError the first time a background subagent was dispatched.
    """
    if cls is None:
        return True
    for b in cls.bases:
        name = b.id if isinstance(b, ast.Name) else b.attr if isinstance(b, ast.Attribute) else ""
        if name in _APP_BASES:
            return True
    # A mixin is only ever used by being mixed into an app class.
    return cls.name.endswith("Mixin")


def scan_file(path: Path) -> list[dict]:
    try:
        src = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    if "asyncio.create_task" not in src:
        return []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []

    lines = src.splitlines()
    fns = [
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]

    def _innermost(nodes, lineno):
        best = None
        for n in nodes:
            if n.lineno <= lineno <= getattr(n, "end_lineno", n.lineno):
                if best is None or n.lineno > best.lineno:
                    best = n
        return best

    def owner(lineno: int) -> str | None:
        """Which name at this site can reach spawn_background — or None."""
        fn = _innermost(fns, lineno)
        if fn is None:
            return None
        params = _params(fn)
        if params and params[0] == "self" and _self_is_an_app(_innermost(classes, lineno)):
            return "self"
        # A helper on a non-app class can still reach the app when one was
        # passed in — the shape `async def run(self, app, **kw)` uses.
        if "app" in params:
            return "app"
        return None

    out: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        f = call.func
        name = (
            f.attr if isinstance(f, ast.Attribute)
            else f.id if isinstance(f, ast.Name) else ""
        )
        if name != "create_task":
            continue
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
                and f.value.id not in _asyncio_aliases(tree):
            continue  # loop.create_task on a specific loop — different shape

        ln = node.lineno
        # opt-out may sit on the call line or the line the statement ends on
        window = "\n".join(lines[ln - 1: getattr(node, "end_lineno", ln)])
        if OPT_OUT.search(window):
            continue

        holder = owner(ln)
        if holder not in ("self", "app"):
            continue  # no app to reach spawn_background through

        out.append({
            "file": str(path.relative_to(REPO_ROOT)).replace("\\", "/"),
            "line": ln,
            "holder": holder,
            "src": lines[ln - 1].strip()[:96],
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--roots", nargs="*", default=DEFAULT_ROOTS)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    findings: list[dict] = []
    for root in args.roots:
        base = REPO_ROOT / root
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            findings.extend(scan_file(p))

    msg = (
        f"{len(findings)} detached create_task call(s) keeping no reference"
        if findings else "no unreferenced detached tasks"
    )
    if args.json:
        return emit_json(not findings, "unreferenced_task", msg, {"findings": findings})

    print(f"bare create_task audit — {len(findings)} finding(s)\n")
    by_file: dict[str, list[dict]] = {}
    for f in findings:
        by_file.setdefault(f["file"], []).append(f)
    for fname in sorted(by_file):
        print(f"  {fname}")
        for f in by_file[fname]:
            print(f"     :{f['line']:<5} {f['src']}")
    if findings:
        print(f"\nEach can be `{findings[0]['holder']}.spawn_background(<coro>, label=...)`,")
        print("which keeps the reference, cancels on teardown, and logs failures.")
        print("Deliberately detached? Mark it: `# noqa: eos-bgtask  (reason)`")
    else:
        print("  (clean)")
    print(f"\n{msg}")
    return len(findings)


if __name__ == "__main__":
    sys.exit(main())
