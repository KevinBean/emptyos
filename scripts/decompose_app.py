"""Parameterizable extractor: split an app's monolith ``app.py`` into helper modules.

See `.claude/rules/multi-module-apps.md` for the pattern this implements.
Reference uses: dogfood-agent + rooms decompositions (May 2026).

Usage:

    python scripts/decompose_app.py <app_id> <ClassName> <routes_spec.json>

Where:
- ``<app_id>`` is the directory under ``apps/`` (or ``apps/personal/``)
- ``<ClassName>`` is the BaseApp subclass name inside ``app.py``
- ``<routes_spec.json>`` maps ``module_name → [member_name, ...]``

Routes JSON shape::

    {
      "modules": {
        "feature_a": ["api_thing", "_helper", "_CONSTANT"],
        "feature_b": ["api_other", "_other_helper"]
      },
      "imports": {
        "feature_a": ["asyncio", "json", "from emptyos.sdk import web_route"],
        "feature_b": ["import json", "from datetime import datetime"]
      },
      "meta": {
        "feature_a": {
          "oneline": "thing-related verbs",
          "owns": "<2-3 sentences on responsibility>",
          "reads": "self._other_helper (feature_b) for X"
        }
      },
      "aliases": {
        "feature_a": "_feature_a"
      }
    }

``imports`` may be omitted; the extractor will sniff a sensible default
(asyncio/json/re/Path/datetime/web_route). ``meta`` and ``aliases`` may
be omitted; defaults are generated. ``aliases`` is only needed when a
module name collides with a method name in the class (see the rule file).

The script writes one helper module per entry in ``modules`` plus a
rewritten ``app.py`` spine with re-binding blocks. Run ``py_compile``
and the app's logic test suite afterward — see the rule file for the
import-sweep + missing-import patterns this won't catch.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path


DEFAULT_IMPORTS = """from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import web_route
"""


def parse_class_members(text: str, class_name: str):
    """Return (class_node, [(node, name, kind), ...]) for the named class."""
    tree = ast.parse(text)
    cls = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            cls = node
            break
    if cls is None:
        raise SystemExit(f"Class {class_name!r} not found")

    members = []
    for node in cls.body:
        name = None
        kind = None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name, kind = node.name, "method"
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name, kind = node.targets[0].id, "const"
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name, kind = node.target.id, "const"
        if name:
            members.append((node, name, kind))
    return cls, members


def node_source_range(node) -> tuple[int, int]:
    """1-based inclusive [start, end] line range, including decorators."""
    start = node.lineno
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.decorator_list:
        start = min(d.lineno for d in node.decorator_list)
    return start, node.end_lineno


def dedent_one(line: str) -> str:
    return line[4:] if line.startswith("    ") else line


def detect_decorator(block_lines: list[str]) -> str:
    """Return '@staticmethod' / '@classmethod' marker for the banner, or ''."""
    src = "\n".join(block_lines)
    if "@staticmethod" in src:
        return "  # @staticmethod"
    if "@classmethod" in src:
        return "  # @classmethod"
    return ""


def render_banner(class_name: str, mod_name: str, alias: str, blocks: list[dict]) -> str:
    if not blocks:
        return ""
    longest = max(len(b["name"]) for b in blocks)
    lines = [f"# ─── Bind to {class_name} class as ────────────────────────────────"]
    for b in blocks:
        kind_note = detect_decorator(b["lines"])
        lines.append(f"#   {b['name']:<{longest}}  = {alias}.{b['name']}{kind_note}")
    lines.append("# Adding a new method here? Add a matching binding line in app.py.")
    lines.append("# ─────────────────────────────────────────────────────────────────────")
    return "\n".join(lines)


def render_docstring(app_id: str, meta: dict) -> str:
    oneline = meta.get("oneline", "extracted helpers")
    owns = meta.get("owns", "<fill in: what this module is the source of truth for>")
    reads = meta.get("reads", "no cross-module reach")
    return (
        f'"""{app_id} — {oneline}.\n\n'
        f"Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md\n"
        f"rule 4). Owns: {owns}.\n\n"
        f"Cross-module callers reach methods here via ``self.X`` after re-binding.\n"
        f"Reaches into other modules: {reads}.\n"
        f"Do not import from ``.app`` (it imports us, which would cycle).\n"
        f'"""\n'
    )


def render_imports(class_name: str, imports_spec: list[str] | None) -> str:
    """Build the import block. ``imports_spec`` is a list of bare module names
    (sniffed as ``import X``) or full import statements."""
    if not imports_spec:
        return DEFAULT_IMPORTS
    lines = ["from __future__ import annotations\n"]
    for item in imports_spec:
        item = item.strip()
        if item.startswith(("import ", "from ")):
            lines.append(item)
        else:
            lines.append(f"import {item}")
    # Always include the TYPE_CHECKING line in the import block; the conditional
    # gets emitted separately right after.
    if not any("TYPE_CHECKING" in line for line in lines):
        lines.append("from typing import TYPE_CHECKING")
    return "\n".join(lines) + "\n"


def write_helper(
    app_dir: Path,
    app_id: str,
    class_name: str,
    mod_name: str,
    alias: str,
    blocks: list[dict],
    imports_spec: list[str] | None,
    meta: dict,
) -> Path:
    docstring = render_docstring(app_id, meta)
    imports = render_imports(class_name, imports_spec)
    type_check = (
        "\nif TYPE_CHECKING:\n"
        f"    from .app import {class_name}  # noqa: F401 — for type hints only\n"
    )
    banner = render_banner(class_name, mod_name, alias, blocks)
    body_chunks = ["\n".join(dedent_one(line) for line in b["lines"]) for b in blocks]
    body = "\n\n\n".join(body_chunks)
    content = (
        docstring + "\n" + imports + type_check + "\n\n" + banner + "\n\n\n" + body + "\n"
    )
    out = app_dir / f"{mod_name}.py"
    out.write_text(content, encoding="utf-8")
    return out


def rewrite_app(
    app_path: Path,
    class_name: str,
    name_to_module: dict[str, str],
    module_aliases: dict[str, str],
    routes: dict[str, list[str]],
) -> int:
    text = app_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    tree = ast.parse(text)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)

    pre_class = lines[: cls.lineno - 1]

    # Collect spine members (those not extracted)
    spine = []
    for node in cls.body:
        name = None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = node.name
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
        if not name or name in name_to_module:
            continue
        start, end = node_source_range(node)
        spine.append(lines[start - 1: end])

    # Inject helper imports after the last existing sdk/external import
    insert_at = len(pre_class)
    for i, line in enumerate(pre_class):
        if line.startswith(("from emptyos.sdk", "from emptyos.", "from apscheduler")):
            insert_at = i + 1

    new_imports = [""]
    for mod in sorted(routes):
        alias = module_aliases[mod]
        new_imports.append(f"from . import {mod} as {alias}")
    pre_class = pre_class[:insert_at] + new_imports + pre_class[insert_at:]

    out = list(pre_class)
    out.append("")
    out.append(lines[cls.lineno - 1])  # class declaration line
    for block in spine:
        out.append("")
        out.extend(block)

    for mod in sorted(routes):
        alias = module_aliases[mod]
        names = routes[mod]
        longest = max(len(n) for n in names)
        out.append("")
        out.append(f"    # ── {mod.replace('_', ' ').title()} (extracted to {mod}.py) ──")
        for n in names:
            out.append(f"    {n:<{longest}} = {alias}.{n}")

    app_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return sum(1 for _ in app_path.open(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Decompose an EmptyOS app's monolith app.py.")
    ap.add_argument("app_id", help="App directory under apps/ or apps/personal/")
    ap.add_argument("class_name", help="BaseApp subclass name (e.g. RoomsApp)")
    ap.add_argument("routes_spec", help="Path to routes JSON file")
    args = ap.parse_args()

    # Resolve app dir
    cands = [Path("apps") / args.app_id, Path("apps/personal") / args.app_id]
    app_dir = next((c for c in cands if (c / "app.py").exists()), None)
    if app_dir is None:
        print(f"FAIL: apps/{args.app_id}/app.py not found (also checked apps/personal/)")
        return 1
    app_path = app_dir / "app.py"

    spec = json.loads(Path(args.routes_spec).read_text(encoding="utf-8"))
    routes: dict[str, list[str]] = spec["modules"]
    imports_spec: dict[str, list[str]] = spec.get("imports", {})
    meta_spec: dict[str, dict] = spec.get("meta", {})
    alias_spec: dict[str, str] = spec.get("aliases", {})

    text = app_path.read_text(encoding="utf-8")
    cls, members = parse_class_members(text, args.class_name)
    lines = text.splitlines()
    name_to_module = {n: mod for mod, names in routes.items() for n in names}

    # Validate: every routed name exists in the class
    all_names = {n for _, n, _ in members}
    spurious = [n for n in name_to_module if n not in all_names]
    if spurious:
        print(f"FAIL: routes reference members not in {args.class_name}: {spurious[:10]}")
        return 1

    # Module aliases: default `_<mod>`, override via spec
    module_aliases = {mod: alias_spec.get(mod, f"_{mod}") for mod in routes}

    # Collision check: alias must not match any class member name
    for mod, alias in module_aliases.items():
        if alias in all_names:
            print(
                f"FAIL: alias {alias!r} for module {mod} collides with method/const "
                f"of the same name in {args.class_name}. Override via "
                f'"aliases": {{"{mod}": "_{mod}_mod"}} in the routes JSON.'
            )
            return 1

    # Build per-module blocks in source order
    buckets: dict[str, list[dict]] = {mod: [] for mod in routes}
    for node, name, kind in members:
        mod = name_to_module.get(name)
        if not mod:
            continue
        start, end = node_source_range(node)
        buckets[mod].append({"name": name, "kind": kind, "lines": lines[start - 1: end]})

    # Write helpers
    for mod, blocks in buckets.items():
        if not blocks:
            continue
        alias = module_aliases[mod]
        meta = meta_spec.get(mod, {})
        out = write_helper(
            app_dir, args.app_id, args.class_name, mod, alias,
            blocks, imports_spec.get(mod), meta,
        )
        print(f"  Wrote {out} ({sum(1 for _ in out.open(encoding='utf-8'))} lines, {len(blocks)} members)")

    n = rewrite_app(app_path, args.class_name, name_to_module, module_aliases, routes)
    print(f"  Rewrote {app_path} ({n} lines)")

    print()
    print("Next steps:")
    print(f"  1. python -m py_compile apps/{args.app_id}/*.py  # sanity check")
    print(f"  2. python -m pytest tests/test_sys_{args.app_id}*.py -v  # surface missing imports")
    print( "  3. Restart the daemon; re-fetch /integrity/api/audit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
