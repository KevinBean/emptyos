"""Structural duplicate scan for /eos-sdk-extract Phase 1e.

Finds function/method bodies that are structurally identical across apps
even when local variable names differ. Complements the name-based grep
passes in the skill, which miss copy-paste where callers renamed locals.

How it works:
  1. Parse every apps/**/*.py with ast
  2. For each function, strip the docstring and rename all local names
     (Name, arg) to positional placeholders _v0, _v1, ... in first-seen
     order. self/cls/True/False/None are preserved.
  3. Hash the normalised ast.unparse() output
  4. Group by hash; report groups with >=2 occurrences and >= MIN_STATEMENTS

Attribute names (`self.foo`, `module.func`) and literal values are kept,
so functions with the same shape but different attribute accesses or
different string literals won't collide. That keeps precision high at
the cost of missing some looser duplicates - tune MIN_STATEMENTS down
if you want more recall.

Usage:
  python scripts/sdk_duplicate_scan.py                  # default scan
  python scripts/sdk_duplicate_scan.py --min 3          # loosen threshold
  python scripts/sdk_duplicate_scan.py apps plugins     # extra roots
"""

from __future__ import annotations

import argparse
import ast
import hashlib
from collections import defaultdict
from pathlib import Path

DEFAULT_ROOTS = ["apps"]
DEFAULT_MIN_STATEMENTS = 4
KEEP_NAMES = {"self", "cls", "True", "False", "None"}
SKIP_DIR_PARTS = {"__pycache__", "_retired", "node_modules", ".venv"}


class Normaliser(ast.NodeTransformer):
    def __init__(self) -> None:
        self.map: dict[str, str] = {}

    def _placeholder(self, name: str) -> str:
        if name in KEEP_NAMES:
            return name
        if name not in self.map:
            self.map[name] = f"_v{len(self.map)}"
        return self.map[name]

    def visit_Name(self, node: ast.Name) -> ast.Name:
        node.id = self._placeholder(node.id)
        return node

    def visit_arg(self, node: ast.arg) -> ast.arg:
        node.arg = self._placeholder(node.arg)
        node.annotation = None  # type hints vary, shouldn't split duplicates
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        node.returns = None
        self.generic_visit(node)
        return node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AsyncFunctionDef:
        node.returns = None
        self.generic_visit(node)
        return node


def _strip_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return body


def _count_statements(body: list[ast.stmt]) -> int:
    return sum(1 for s in body if not isinstance(s, ast.Pass))


def _fn_signature(fn: ast.FunctionDef | ast.AsyncFunctionDef, file: Path, cls: str | None) -> dict:
    qual = f"{cls}.{fn.name}" if cls else fn.name
    return {"file": str(file), "line": fn.lineno, "qual": qual, "stmts": _count_statements(fn.body)}


def _iter_functions(
    tree: ast.AST,
) -> list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str | None]]:
    out: list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str | None]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.append((item, node.name))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Skip methods (already captured above) by checking parent isn't a class
            # A simpler heuristic: functions at module level have no class context
            pass
    # Add module-level functions
    if isinstance(tree, ast.Module):
        for item in tree.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append((item, None))
    return out


def _normalise_body(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    fn_copy = ast.parse(ast.unparse(fn)).body[0]
    assert isinstance(fn_copy, (ast.FunctionDef, ast.AsyncFunctionDef))
    fn_copy.body = _strip_docstring(fn_copy.body)
    fn_copy.name = "_fn"
    fn_copy.decorator_list = []
    Normaliser().visit(fn_copy)
    ast.fix_missing_locations(fn_copy)
    return ast.unparse(fn_copy)


def _scan_file(path: Path, groups: dict[str, list[dict]], min_statements: int) -> None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return
    for fn, cls in _iter_functions(tree):
        body = _strip_docstring(fn.body)
        if _count_statements(body) < min_statements:
            continue
        try:
            norm = _normalise_body(fn)
        except Exception:
            continue
        digest = hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]
        groups[digest].append(_fn_signature(fn, path, cls))


def _iter_py_files(roots: list[str]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        root_path = Path(root)
        if not root_path.exists():
            continue
        for p in root_path.rglob("*.py"):
            if any(part in SKIP_DIR_PARTS for part in p.parts):
                continue
            files.append(p)
    return files


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "roots",
        nargs="*",
        default=DEFAULT_ROOTS,
        help=f"Directories to scan (default: {DEFAULT_ROOTS})",
    )
    parser.add_argument(
        "--min",
        type=int,
        default=DEFAULT_MIN_STATEMENTS,
        dest="min_statements",
        help=f"Minimum statements in a function body (default: {DEFAULT_MIN_STATEMENTS})",
    )
    parser.add_argument("--limit", type=int, default=20, help="Max groups to report (default: 20)")
    args = parser.parse_args()

    groups: dict[str, list[dict]] = defaultdict(list)
    files = _iter_py_files(args.roots)
    for path in files:
        _scan_file(path, groups, args.min_statements)

    dup_groups = [(digest, entries) for digest, entries in groups.items() if len(entries) >= 2]
    dup_groups.sort(key=lambda item: (len(item[1]), max(e["stmts"] for e in item[1])), reverse=True)

    if not dup_groups:
        print(
            f"scanned {len(files)} files, no structural duplicates found (min_statements={args.min_statements})"
        )
        return 0

    print(
        f"scanned {len(files)} files, {len(dup_groups)} duplicate groups (min_statements={args.min_statements})\n"
    )
    for digest, entries in dup_groups[: args.limit]:
        stmts = max(e["stmts"] for e in entries)
        print(f"=== group {digest} | {len(entries)} callers | ~{stmts} stmts ===")
        for e in entries:
            print(f"  {e['file']}:{e['line']}  {e['qual']}")
        print()

    if len(dup_groups) > args.limit:
        print(f"... {len(dup_groups) - args.limit} more groups (raise --limit to see)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
