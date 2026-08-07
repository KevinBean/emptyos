#!/usr/bin/env python3
"""Find `test_*` functions defined inside another `test_*` function.

A test nested inside a test is **invisible and green**. pytest collects only
module-level and class-level tests, so a nested one never runs — while the
enclosing test still passes on its own assertions, so CI reports success and
the diffstat shows the lines were added. Nothing else catches it: it is valid
Python, `py_compile` is happy, and ruff's configured rule set (E/W/F/I/B/UP/
ASYNC) has no rule for an unused nested function.

Found 2026-07-31 in `tests/test_unit_music_studio_frames.py`: a `class` body
was truncated by three module-level `def test_*` inserted into the middle of
it, which re-parented **24 methods, ~1,197 lines** into the body of the
function that happened to precede them. Among the dead was the test for the
run-transparency UI contract that the same session had just written into
`.claude/rules/staged-pipeline.md` — the rule was documented, the test was
written, and the test had never run.

The signal is unambiguous: there is no legitimate reason to define a function
named `test_*` inside another `test_*`. Helper closures inside a test are
normal and common (`fake_materialize`, `fake_probe`, …) — those are not
matched, because they are not named `test_*`. Calibrated on the whole tree:
24 findings, all genuine, zero false positives. It gates.

    python scripts/check_nested_tests.py [--json] [PATH ...]
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling scripts/ modules

from scanner_lib import emit_json  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DEFAULT_ROOTS = ("tests",)
_FUNC = (ast.FunctionDef, ast.AsyncFunctionDef)


def _rel(path: Path) -> str:
    """Repo-relative when possible, absolute otherwise (tmp dirs in tests)."""
    try:
        return str(path.resolve().relative_to(REPO)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def nested_tests(path: Path) -> list[dict]:
    """Return every `test_*` def that lives inside another `test_*` def."""
    rel = _rel(path)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError) as e:
        # A file that won't parse is a different problem, and a louder one.
        return [{"file": rel, "line": getattr(e, "lineno", 0) or 0, "name": "",
                 "enclosing": "", "reason": f"unparseable: {e}"}]

    out: list[dict] = []

    def walk(node: ast.AST, enclosing: ast.AST | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _FUNC) and child.name.startswith("test_"):
                if enclosing is not None:
                    out.append({
                        "file": rel,
                        "line": child.lineno,
                        "name": child.name,
                        "enclosing": getattr(enclosing, "name", "?"),
                        "enclosing_line": getattr(enclosing, "lineno", 0),
                        "reason": "never collected — pytest does not descend into a function body",
                    })
                walk(child, child)
            else:
                # Not a test: keep whatever enclosing test we are already under,
                # so a test buried inside `if`/`with`/`for` is still caught.
                walk(child, enclosing)

    walk(tree, None)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="*", help="files or dirs (default: tests/)")
    ap.add_argument("--json", action="store_true", help="agent-cli envelope on stdout")
    args = ap.parse_args()

    targets: list[Path] = []
    for raw in (args.paths or [str(REPO / r) for r in DEFAULT_ROOTS]):
        p = Path(raw)
        if not p.is_absolute():
            p = REPO / p
        if p.is_dir():
            targets += sorted(p.rglob("test_*.py"))
        elif p.is_file():
            targets.append(p)

    findings = [f for t in targets for f in nested_tests(t)]

    if args.json:
        return emit_json(
            not findings, "nested_tests",
            f"{len(findings)} nested test(s) across {len(targets)} file(s)",
            {"findings": findings, "files_scanned": len(targets)},
        )

    if not findings:
        print(f"OK — no nested tests in {len(targets)} file(s)")
        return 0

    print(f"{len(findings)} test(s) defined inside another test — never collected:\n")
    for f in findings:
        print(f"  {f['file']}:{f['line']}  {f['name']}()")
        if f["enclosing"]:
            print(f"      inside {f['enclosing']}() at line {f['enclosing_line']}")
    print("\nMove them to module level, or into the class whose body was truncated.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
