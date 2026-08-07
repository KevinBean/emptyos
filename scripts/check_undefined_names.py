#!/usr/bin/env python3
"""check_undefined_names.py — catch names that are undefined at RUNTIME.

Why this exists
---------------
The multi-module app pattern (`.claude/rules/multi-module-apps.md`) splits a big
`app.py` into helper modules whose functions are re-bound onto the class. A
function's ``__globals__`` is *its own module's* dict, so an import left behind
in the spine is simply not there when the helper runs. The rule documents this
("module-level constants don't move with their consumer") and it still shipped:
`apps/personal/shadowing` had **nine** such names across two helpers, killing
every audio route in the pronunciation app. It failed soft — one route's
try/except turned it into an error toast — so nothing surfaced it for months.

Two passes, because one is not enough
-------------------------------------
1. **pyflakes** — catches a bare missing import (`shutil`, `FileResponse`).
2. **TYPE_CHECKING pass** — catches what pyflakes *cannot*. A name imported
   under ``if TYPE_CHECKING:`` is bound as far as pyflakes is concerned, so a
   runtime use of it reads as fine. It is not: at runtime the block never
   executed. This was the worst of shadowing's nine
   (`passages.py` calling `ShadowingApp._alignment_to_events(...)`), and it was
   invisible to the linter.

   Annotations are excluded — that is what the guard is *for*. Only genuine
   runtime expression use is reported.

Both classes are unambiguous runtime bugs with no legitimate use, so this gates.
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCAN_DIRS = ("apps", "plugins", "emptyos", "engines")
SKIP_PARTS = {
    "__pycache__", "node_modules", ".git", "_retired",
    ".claude", "worktrees", "sandbox-9002", "sandbox-9003", "sandbox-9004",
}

# Inline opt-out at the call site, per .claude/rules/audits.md (an inline
# marker beats a central allowlist — a central list turns every new legitimate
# case into a build break).
IGNORE_MARKER = "undefined-names: ignore"


def _iter_py() -> list[Path]:
    out: list[Path] = []
    for d in SCAN_DIRS:
        root = REPO / d
        if not root.exists():
            continue
        for p in root.rglob("*.py"):
            if SKIP_PARTS & set(p.parts):
                continue
            out.append(p)
    return out


# ── pass 1: pyflakes undefined names ────────────────────────────────────────

def pyflakes_undefined(files: list[Path]) -> list[dict]:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pyflakes", *[str(f) for f in files]],
            capture_output=True, text=True, timeout=300,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    findings = []
    for line in (proc.stdout or "").splitlines():
        if "undefined name" not in line:
            continue
        parts = line.split(":", 3)
        if len(parts) < 4:
            continue
        path, lineno = parts[0], parts[1]
        try:
            rel = str(Path(path).resolve().relative_to(REPO)).replace("\\", "/")
        except ValueError:
            rel = path
        if _line_opted_out(Path(path), lineno):
            continue
        findings.append({
            "kind": "undefined-name",
            "file": rel,
            "line": int(lineno) if lineno.isdigit() else 0,
            "detail": parts[3].strip(),
        })
    return findings


def _line_opted_out(path: Path, lineno: str) -> bool:
    if not lineno.isdigit():
        return False
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return False
    i = int(lineno) - 1
    for j in (i, i - 1):
        if 0 <= j < len(lines) and IGNORE_MARKER in lines[j]:
            return True
    return False


# ── pass 2: TYPE_CHECKING-only names used at runtime ────────────────────────

class _TypeCheckingVisitor(ast.NodeVisitor):
    """Collect names bound ONLY inside `if TYPE_CHECKING:` blocks."""

    def __init__(self) -> None:
        self.guarded: set[str] = set()
        self.runtime_bound: set[str] = set()

    @staticmethod
    def _is_type_checking(test: ast.expr) -> bool:
        if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
            return True
        if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
            return True
        return False

    def visit_If(self, node: ast.If) -> None:
        if self._is_type_checking(node.test):
            for sub in ast.walk(ast.Module(body=node.body, type_ignores=[])):
                if isinstance(sub, (ast.Import, ast.ImportFrom)):
                    for a in sub.names:
                        self.guarded.add(a.asname or a.name.split(".")[0])
            # the else-branch IS runtime
            for sub in node.orelse:
                self.visit(sub)
            return
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            self.runtime_bound.add(a.asname or a.name.split(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for a in node.names:
            self.runtime_bound.add(a.asname or a.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.runtime_bound.add(node.name)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.runtime_bound.add(node.name)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.runtime_bound.add(node.name)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for t in node.targets:
            if isinstance(t, ast.Name):
                self.runtime_bound.add(t.id)
        self.generic_visit(node)


def _runtime_name_uses(tree: ast.AST) -> list[ast.Name]:
    """Every `Name` load that is genuinely evaluated at runtime.

    Skips annotations entirely — a guarded import used in an annotation is the
    correct, intended pattern (and with `from __future__ import annotations`
    it is never evaluated).
    """
    skip: set[int] = set()

    for node in ast.walk(tree):
        ann = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            ann.append(node.returns)
            for a in list(node.args.args) + list(node.args.kwonlyargs) + list(node.args.posonlyargs):
                ann.append(a.annotation)
            if node.args.vararg:
                ann.append(node.args.vararg.annotation)
            if node.args.kwarg:
                ann.append(node.args.kwarg.annotation)
        elif isinstance(node, ast.AnnAssign):
            ann.append(node.annotation)
        elif isinstance(node, ast.arg):
            ann.append(node.annotation)
        for a in ann:
            if a is None:
                continue
            for sub in ast.walk(a):
                skip.add(id(sub))
        # A string literal is a forward ref, not a runtime use.
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            skip.add(id(node))

    return [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and id(n) not in skip
    ]


def type_checking_leaks(files: list[Path]) -> list[dict]:
    findings = []
    for path in files:
        try:
            src = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "TYPE_CHECKING" not in src:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        v = _TypeCheckingVisitor()
        v.visit(tree)
        guarded_only = v.guarded - v.runtime_bound
        if not guarded_only:
            continue
        lines = src.splitlines()
        for name_node in _runtime_name_uses(tree):
            if name_node.id not in guarded_only:
                continue
            ln = getattr(name_node, "lineno", 0)
            ctx = lines[ln - 1] if 0 < ln <= len(lines) else ""
            if IGNORE_MARKER in ctx or (ln > 1 and IGNORE_MARKER in lines[ln - 2]):
                continue
            rel = str(path.relative_to(REPO)).replace("\\", "/")
            findings.append({
                "kind": "type-checking-only",
                "file": rel,
                "line": ln,
                "detail": (
                    f"'{name_node.id}' is imported only under `if TYPE_CHECKING:` "
                    f"but used at runtime here — pyflakes cannot see this"
                ),
            })
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--full", action="store_true", help="list every finding")
    args = ap.parse_args()

    files = _iter_py()
    findings = pyflakes_undefined(files) + type_checking_leaks(files)
    findings.sort(key=lambda f: (f["file"], f["line"]))

    ok = not findings
    msg = (
        f"{len(files)} files scanned, no undefined names"
        if ok else
        f"{len(findings)} undefined name(s) across "
        f"{len({f['file'] for f in findings})} file(s)"
    )

    if args.json:
        try:
            sys.path.insert(0, str(REPO / "scripts"))
            from scanner_lib import emit_json
            return emit_json(ok, "ok" if ok else "undefined_names", msg,
                             {"findings": findings})
        except ImportError:
            print(json.dumps({"ok": ok, "code": "ok" if ok else "undefined_names",
                              "message": msg, "data": {"findings": findings}}))
            return 0 if ok else 1

    print(msg)
    shown = findings if args.full else findings[:25]
    for f in shown:
        print(f"  {f['file']}:{f['line']}  [{f['kind']}]  {f['detail']}")
    if len(findings) > len(shown):
        print(f"  … +{len(findings) - len(shown)} more (--full to list)")
    if findings:
        print("\n  Both classes are runtime NameErrors. A helper module needs its "
              "own imports —\n  see .claude/rules/multi-module-apps.md. Opt out at "
              f"the call site with `# {IGNORE_MARKER}`.")
    return len(findings)


if __name__ == "__main__":
    sys.exit(main())
