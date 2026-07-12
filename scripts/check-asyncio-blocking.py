"""Lint for known asyncio event-loop wedge patterns.

Three rules, all derived from real incidents recorded in MEMORY.md:

  lock-emit          `await self.emit(...)` inside `async with <lock>`
                     Reason: emit awaits handlers serially; if a handler
                     re-enters the same lock the daemon deadlocks. Fix:
                     `asyncio.create_task(self.emit(...))` inside the lock.

  long-handler       `await self.emit(...)` inside a function decorated
                     with `@web_route` (or any HTTP route shape).
                     Reason: long emit chains block the HTTP response;
                     Starlette middleware raises "No response returned".
                     Fix: `asyncio.create_task(self.emit(...))`.

  sync-in-middleware `def apply(self, ...)` body calls known-blocking
                     APIs (onnx/torch/requests/urllib/sqlite3/time.sleep)
                     without wrapping in `asyncio.to_thread`.
                     Reason: middleware fires inside the request loop;
                     sync ML/IO wedges every awaitable.

  blocking-in-async  a blocking primitive (subprocess/http/sleep) reached
                     from an `async def` directly OR via a sync sibling it
                     calls (intra-file transitive), without `to_thread`.
                     Reason: freezes the event loop for the call's duration
                     (this caught the /topology git freeze). Fix: wrap the
                     sync call in `await asyncio.to_thread(...)`.

Advisory output only — exit code is the count of findings but main loop
returns 0 so this won't block CI. Run manually before merges, or wire
into pre-commit when you trust the false-positive rate.

Usage:
    python scripts/check-asyncio-blocking.py
    python scripts/check-asyncio-blocking.py apps/rooms     # subset
    python scripts/check-asyncio-blocking.py --json         # machine-readable
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

from check_base import REPO_ROOT, filter_noqa, iter_py_files

# Walk these by default; CLI args override.
DEFAULT_ROOTS = ["apps", "plugins", "emptyos"]

# Modules whose top-level call is reliably blocking in async context. Not
# exhaustive — extend as new wedges surface. Each entry matches either the
# import alias (`requests.get`) or the bare function (`sleep`).
BLOCKING_CALL_PREFIXES = {
    "requests.",
    "urllib.request.urlopen",
    "urllib.request.Request",
    "urlopen",
    "sqlite3.connect",
    "time.sleep",
    "torch.",  # any torch.* call in middleware is suspicious
    "onnx.",
    "onnxruntime.",
    "InferenceSession",
    "subprocess.run",
    "subprocess.check_output",
    "subprocess.check_call",
    # NOT subprocess.Popen — it returns immediately (the documented detached
    # external-service spawn pattern, CLAUDE.md). The blockers are run/
    # check_output/check_call and a later .communicate()/.wait() on the handle.
}

# Heuristic: `async with` whose context-manager source contains one of these
# tokens is treated as a lock — covers `self._lock`, `self._daily_lock(...)`,
# `self._lock_for(slug)`, `asyncio.Lock()`, etc.
LOCK_NAME_HINTS = ("lock", "_lock", "Lock", "Semaphore", "_sem", "mutex")

# Route decorators that mark HTTP handlers — match by trailing name.
ROUTE_DECORATOR_NAMES = {"web_route", "route", "get", "post", "put", "delete", "patch"}


def _is_lock_context(node: ast.expr) -> bool:
    """Heuristic: does this `async with` context manager look like a lock?"""
    src = ast.unparse(node)
    return any(hint in src for hint in LOCK_NAME_HINTS)


def _is_route_decorator(dec: ast.expr) -> bool:
    """`@web_route(...)`, `@app.get(...)`, `@router.post(...)`, etc."""
    if isinstance(dec, ast.Call):
        dec = dec.func
    if isinstance(dec, ast.Attribute):
        return dec.attr in ROUTE_DECORATOR_NAMES
    if isinstance(dec, ast.Name):
        return dec.id in ROUTE_DECORATOR_NAMES
    return False


def _is_self_emit(node: ast.expr) -> bool:
    """Match `self.emit(...)` exactly. Other `.emit()` is allowed (e.g. ws.emit)."""
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    return (
        isinstance(f, ast.Attribute)
        and isinstance(f.value, ast.Name)
        and f.value.id == "self"
        and f.attr == "emit"
    )


def _is_blocking_call(node: ast.Call) -> str | None:
    """Return the matched prefix if this call looks blocking, else None."""
    try:
        src = ast.unparse(node.func)
    except Exception:
        return None
    for prefix in BLOCKING_CALL_PREFIXES:
        if src.startswith(prefix) or src == prefix.rstrip("."):
            return prefix
    return None


def _enclosed_in_to_thread(stack: list[ast.AST]) -> bool:
    """True if any ancestor in `stack` is `await asyncio.to_thread(...)`."""
    for anc in stack:
        if isinstance(anc, ast.Await) and isinstance(anc.value, ast.Call):
            f = anc.value.func
            try:
                src = ast.unparse(f)
            except Exception:
                continue
            if "to_thread" in src or "run_in_executor" in src:
                return True
    return False


def _has_decorator(func: ast.AsyncFunctionDef | ast.FunctionDef, pred) -> bool:
    return any(pred(d) for d in func.decorator_list)


class Visitor(ast.NodeVisitor):
    def __init__(self, path: Path):
        self.path = path
        self.findings: list[dict] = []
        # Stack of (kind, node) for "inside <kind>" lookups:
        #   'route'      — inside @web_route handler
        #   'lock_with'  — inside `async with <lock>`
        #   'mw_apply'   — inside Middleware.apply()
        self._scope: list[str] = []
        self._class_stack: list[str] = []
        self._ancestors: list[ast.AST] = []

    def _report(self, rule: str, lineno: int, detail: str) -> None:
        self.findings.append(
            {
                "rule": rule,
                "file": str(self.path.relative_to(REPO_ROOT)).replace("\\", "/"),
                "line": lineno,
                "detail": detail,
            }
        )

    # ── Generic ancestor tracking (cheap; lets us check enclosure) ──────────
    def generic_visit(self, node):
        self._ancestors.append(node)
        try:
            super().generic_visit(node)
        finally:
            self._ancestors.pop()

    # ── Classes — detect Middleware shape ───────────────────────────────────
    def visit_ClassDef(self, node: ast.ClassDef):
        self._class_stack.append(node.name)
        try:
            self.generic_visit(node)
        finally:
            self._class_stack.pop()

    # ── async with — push lock scope if matched ─────────────────────────────
    def visit_AsyncWith(self, node: ast.AsyncWith):
        is_lock = any(_is_lock_context(item.context_expr) for item in node.items)
        if is_lock:
            self._scope.append("lock_with")
        try:
            self.generic_visit(node)
        finally:
            if is_lock:
                self._scope.pop()

    # ── Async function definitions — push route / mw_apply scope ────────────
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._handle_function(node)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._handle_function(node)

    def _handle_function(self, node):
        pushed = []
        if _has_decorator(node, _is_route_decorator):
            self._scope.append("route")
            pushed.append("route")
        in_middleware = self._class_stack and self._class_stack[-1].endswith("Middleware")
        if in_middleware and node.name == "apply":
            self._scope.append("mw_apply")
            pushed.append("mw_apply")
        try:
            self.generic_visit(node)
        finally:
            for _ in pushed:
                self._scope.pop()

    # ── Await — main inspection point ───────────────────────────────────────
    def visit_Await(self, node: ast.Await):
        # await self.emit(...) inside lock → lock-emit
        if _is_self_emit(node.value):
            if "lock_with" in self._scope:
                self._report(
                    "lock-emit",
                    node.lineno,
                    "await self.emit(...) inside `async with <lock>` — "
                    "use asyncio.create_task(self.emit(...)) to avoid handler deadlock",
                )
            elif "route" in self._scope:
                self._report(
                    "long-handler",
                    node.lineno,
                    "await self.emit(...) inside HTTP route handler — "
                    "long emit chains wedge the response; use asyncio.create_task(...)",
                )
        self.generic_visit(node)

    # ── Call inside Middleware.apply() without to_thread ────────────────────
    def visit_Call(self, node: ast.Call):
        if "mw_apply" in self._scope:
            prefix = _is_blocking_call(node)
            if prefix and not _enclosed_in_to_thread(self._ancestors):
                self._report(
                    "sync-in-middleware",
                    node.lineno,
                    f"blocking call `{prefix}...` in Middleware.apply() without "
                    "`await asyncio.to_thread(...)` — wedges the event loop",
                )
        self.generic_visit(node)


# ── Rule 4: blocking-in-async (direct + transitive) ─────────────────────────
# A blocking primitive (subprocess/http/sleep — BLOCKING_CALL_PREFIXES) reached
# from an `async def`, either directly or via a sync sibling it calls, without
# `asyncio.to_thread`. This is the wedge that froze /topology on git and stalled
# capture/promote (audit loop, iters 5/17). Separate per-file pass so the three
# Visitor rules above stay untouched. Intra-file call graph only (cross-file
# helpers like sdk.git_run are out of scope — flag those at their call site).
def _directly_blocks(fnode) -> bool:
    found = [False]

    class W(ast.NodeVisitor):
        def visit_FunctionDef(self, n):
            pass  # nested sync def is its own scope

        def visit_AsyncFunctionDef(self, n):
            pass

        def visit_Call(self, n):
            try:
                src = ast.unparse(n.func)
            except Exception:
                src = ""
            if "to_thread" in src or "run_in_executor" in src:
                return  # wrapped — not on the loop
            if _is_blocking_call(n):
                found[0] = True
            self.generic_visit(n)

    for st in fnode.body:
        W().visit(st)
    return found[0]


def _self_call_names(fnode) -> set[str]:
    names: set[str] = set()
    for n in ast.walk(fnode):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "self":
                names.add(f.attr)
            elif isinstance(f, ast.Name):
                names.add(f.id)
    return names


def _scan_blocking_in_async(tree: ast.AST, path: Path) -> list[dict]:
    funcs: dict[str, tuple] = {}
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs[n.name] = (n, isinstance(n, ast.AsyncFunctionDef))
    direct = {name for name, (node, _) in funcs.items() if _directly_blocks(node)}
    calls = {name: _self_call_names(node) for name, (node, _) in funcs.items()}
    blocks = set(direct)
    changed = True
    while changed:
        changed = False
        for name in funcs:
            if name not in blocks and calls[name] & blocks:
                blocks.add(name)
                changed = True
    rel = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
    out: list[dict] = []
    for name, (node, is_async) in funcs.items():
        if not is_async:
            continue
        if name in direct:
            out.append({"rule": "blocking-in-async", "file": rel, "line": node.lineno,
                        "detail": f"async {name}() calls a blocking primitive directly — "
                                  "wrap in await asyncio.to_thread(...)"})
        elif calls[name] & blocks:
            via = ", ".join(sorted(calls[name] & blocks)[:3])
            out.append({"rule": "blocking-in-async", "file": rel, "line": node.lineno,
                        "detail": f"async {name}() reaches a blocking primitive via {via} — "
                                  "wrap the sync call in await asyncio.to_thread(...)"})
    return out


# Inline suppression rides the shared `filter_noqa(findings, "eos-asyncio")`:
# a bare `# noqa: eos-asyncio` on a finding's line silences all rules there;
# `# noqa: eos-asyncio:long-handler` silences only that rule.


def main() -> int:
    args = sys.argv[1:]
    as_json = "--json" in args
    show_low = "--show-low" in args  # by default, low-signal tier prints count only
    args = [a for a in args if a not in ("--json", "--show-low")]
    roots = args or DEFAULT_ROOTS

    all_findings: list[dict] = []
    for f in iter_py_files(roots):
        try:
            src = f.read_text(encoding="utf-8")
            tree = ast.parse(src, filename=str(f))
        except (SyntaxError, UnicodeDecodeError):
            continue
        v = Visitor(f)
        v.visit(tree)
        all_findings.extend(v.findings)
        all_findings.extend(_scan_blocking_in_async(tree, f))

    all_findings = filter_noqa(all_findings, "eos-asyncio")

    if as_json:
        print(json.dumps(all_findings, indent=2))
        return 0

    by_rule: dict[str, list[dict]] = {}
    for x in all_findings:
        by_rule.setdefault(x["rule"], []).append(x)

    # High-signal tier — every finding here is a real wedge risk worth fixing.
    HIGH = ("lock-emit", "sync-in-middleware")
    LOW = ("long-handler", "blocking-in-async")

    print("=" * 60)
    print("HIGH-SIGNAL (each finding is a real wedge risk)")
    print("=" * 60)
    high_total = 0
    for rule in HIGH:
        hits = by_rule.get(rule, [])
        if not hits:
            continue
        high_total += len(hits)
        print(f"\n{rule} ({len(hits)}):")
        for h in hits:
            print(f"  {h['file']}:{h['line']}  {h['detail']}")
    if high_total == 0:
        print("\n  Clean.")

    print()
    print("=" * 60)
    print("LOW-SIGNAL (emit-in-route is risky only if the emit chain is slow)")
    print("=" * 60)
    for rule in LOW:
        hits = by_rule.get(rule, [])
        if not hits:
            continue
        # Group by file so per-file totals show without 239 lines of output
        by_file: dict[str, int] = {}
        for h in hits:
            by_file[h["file"]] = by_file.get(h["file"], 0) + 1
        print(f"\n{rule} ({len(hits)} total, {len(by_file)} files):")
        if show_low:
            for h in hits:
                print(f"  {h['file']}:{h['line']}  {h['detail']}")
        else:
            print("  (use --show-low for the full list, or grep manually)")
            for path, n in sorted(by_file.items(), key=lambda kv: -kv[1])[:10]:
                print(f"  {n:>3}  {path}")
            if len(by_file) > 10:
                print(f"  ... +{len(by_file) - 10} more files")

    print()
    print(f"Total: {len(all_findings)} findings ({high_total} high-signal)")
    print("Suppress per-line with: # noqa: eos-asyncio[:rule-id]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
