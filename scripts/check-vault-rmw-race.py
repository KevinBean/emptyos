#!/usr/bin/env python3
"""Lint for vault read-modify-write races (silent data loss).

Pattern (CLAUDE.md § Development Gotchas — "Vault read-modify-write races"):
an `async def` that does `self.read(X)` then `self.write(X)` on the SAME path
expression, with no enclosing `self.write_lock(...)` or shared
`self.note_lock(...)` in the function. Two
concurrent calls (or a user POST racing a reactor handler) both read the
pre-write content and the later write drops the earlier writer's change.

This is the class that lost captures / canvas nodes / expense rows (audit loop,
iters 10-12). Fix: wrap vault-note RMW in `async with self.note_lock(path):`
(or generic state in `self.write_lock(<key>)`; emit stays OUTSIDE the lock). Reference
impl: journal `_daily_lock`, quick-action capture lock.

ADVISORY (exit 0) by design — per-entity files (one note per project/person)
rarely collide, so this fires on benign sites too (audits.md FP discipline).
Its value is surfacing SHARED-file writers (one inbox/ledger/board many writers)
for a human to judge. Suppress a benign site with `# noqa: eos-rmw` on the
read or write line. The deadlock caveat is the load-bearing nuance: only lock
when the locked methods don't call each other with the same key (asyncio.Lock
is not reentrant) — verify the call graph before applying the fix.

Usage:
    python scripts/check-vault-rmw-race.py
    python scripts/check-vault-rmw-race.py apps/public/core/quick-action
    python scripts/check-vault-rmw-race.py --json
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

from check_base import REPO_ROOT, filter_noqa, iter_py_files

DEFAULT_ROOTS = ["apps", "emptyos"]

# self.<name>(...) calls that read / write a vault path. Keyed by method name;
# the first positional arg is the path expression we match on.
READ_METHODS = {"read", "vault_read_body", "vault_read_section", "read_text"}
WRITE_METHODS = {"write", "vault_write", "vault_set_section", "vault_append_section", "write_text"}
LOCK_METHODS = {"write_lock", "note_lock"}


def _self_method(call: ast.Call) -> str | None:
    f = call.func
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "self":
        return f.attr
    return None


def _path_key(call: ast.Call) -> str | None:
    """Structural id of the first positional arg (the path expr), for matching
    a read against a write on the *same* target."""
    if not call.args:
        return None
    try:
        return ast.dump(call.args[0])
    except Exception:
        return None


def _scan_func(fnode, path: Path) -> list[dict]:
    reads: dict[str, int] = {}
    writes: dict[str, int] = {}
    has_lock = False
    for n in ast.walk(fnode):
        if isinstance(n, ast.Call):
            m = _self_method(n)
            if m in LOCK_METHODS:
                has_lock = True
            elif m in READ_METHODS:
                k = _path_key(n)
                if k:
                    reads.setdefault(k, n.lineno)
            elif m in WRITE_METHODS:
                k = _path_key(n)
                if k:
                    writes.setdefault(k, n.lineno)
    if has_lock:
        return []
    rel = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
    out = []
    for k, rline in reads.items():
        if k in writes:
            out.append({
                "rule": "vault-rmw-race",
                "file": rel,
                "line": rline,
                "detail": f"async {fnode.name}() does read→write on the same path with no "
                          "self.write_lock(...) / self.note_lock(...) — concurrent writers "
                          "can drop data; wrap vault notes in `async with self.note_lock(path):`",
            })
    return out


def _scan(tree: ast.AST, path: Path) -> list[dict]:
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.AsyncFunctionDef):
            out.extend(_scan_func(n, path))
    return out


def main() -> int:
    args = sys.argv[1:]
    as_json = "--json" in args
    args = [a for a in args if a != "--json"]
    roots = args or DEFAULT_ROOTS

    findings: list[dict] = []
    for f in iter_py_files(roots):
        if "_retired" in str(f):
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        except (SyntaxError, UnicodeDecodeError):
            continue
        findings.extend(_scan(tree, f))

    findings = filter_noqa(findings, "eos-rmw")

    if as_json:
        print(json.dumps(findings, indent=2))
        return 0

    by_file: dict[str, int] = {}
    for x in findings:
        by_file[x["file"]] = by_file.get(x["file"], 0) + 1
    print("=" * 60)
    print("vault read-modify-write races (ADVISORY — triage shared-file writers)")
    print("=" * 60)
    if not findings:
        print("\n  Clean.")
    else:
        print(f"\n{len(findings)} findings in {len(by_file)} files:")
        for x in sorted(findings, key=lambda d: (d["file"], d["line"])):
            print(f"  {x['file']}:{x['line']}  {x['detail']}")
    print("\nSuppress a benign (per-entity) site with: # noqa: eos-rmw")
    return 0


if __name__ == "__main__":
    sys.exit(main())
