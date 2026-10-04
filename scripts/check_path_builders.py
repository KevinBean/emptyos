"""Find vault path-builders that interpolate a caller-supplied id unsanitized.

The shape (confirmed twice: an engineering library_path, sim _run_path):

    def _run_path(self, run_id):            # id comes from a path param / body
        return template.replace("{run_id}", run_id)     # raw interpolation

...whose result feeds vault_create_note / .unlink(). A "../.." id then escapes
the vault entirely.

Heuristic: a function whose name ends in _path/_file, that returns an f-string
or .replace()/join using one of its parameters, and whose body contains NO
sanitizer (safe_path_segment / slugify / slugify_id / _slug).
"""
from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SANITIZERS = ("safe_path_segment", "slugify", "_slug", "sanitize", "secure_filename")
SINKS = ("unlink", "vault_create_note", "vault_write", "write_text", "rmtree", "remove")


def fn_uses_param_in_path(fn: ast.FunctionDef) -> bool:
    params = {a.arg for a in fn.args.args if a.arg != "self"}
    if not params:
        return False
    for node in ast.walk(fn):
        # f"...{param}..."
        if isinstance(node, ast.JoinedStr):
            for v in node.values:
                if isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name):
                    if v.value.id in params:
                        return True
        # template.replace("{x}", param)  /  "a/" + param
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("replace", "format", "join"):
                for a in node.args:
                    if isinstance(a, ast.Name) and a.id in params:
                        return True
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            for side in (node.left, node.right):
                if isinstance(side, ast.Name) and side.id in params:
                    return True
    return False


def scan(path: pathlib.Path):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []
    src = path.read_text(encoding="utf-8", errors="replace")
    has_sink = any(s in src for s in SINKS)
    if not has_sink:
        return []
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        name = node.name
        if not (name.endswith("_path") or name.endswith("_file") or name.endswith("_dir")):
            continue
        if not fn_uses_param_in_path(node):
            continue
        body = ast.get_source_segment(src, node) or ""
        if any(s in body for s in SANITIZERS):
            continue
        out.append((name, node.lineno))
    return out


def main() -> int:
    hits = []
    for p in sorted(ROOT.glob("apps/**/*.py")):
        if "__pycache__" in str(p) or "_retired" in str(p):
            continue
        for name, line in scan(p):
            hits.append((p.relative_to(ROOT).as_posix(), name, line))
    for f, n, l in hits:
        print(f"{f}:{l}  {n}()")
    print(f"\nTOTAL: {len(hits)} candidate path-builders in {len({h[0] for h in hits})} files")


if __name__ == "__main__":
    main()
