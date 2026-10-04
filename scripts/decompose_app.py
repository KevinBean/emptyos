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
      },
      "shared": ["_slugify", "_CITATION_RE", "KINDS"]
    }

``imports`` may be omitted; the extractor will sniff a sensible default
(asyncio/json/re/Path/datetime/web_route). ``meta`` and ``aliases`` may
be omitted; defaults are generated. ``aliases`` is only needed when a
module name collides with a method name in the class (see the rule file).

``shared`` (optional) lists **module-level** names (constants, regexes,
pure functions defined OUTSIDE the class) to hoist into a sibling
``shared.py`` per the shared.py exception in the rule file. The spine
gains ``from .shared import (...)`` for backwards compatibility; helpers
that need shared names declare them in their own ``imports`` entry
(e.g. ``"feature_a": ["from .shared import _slugify"]``). Imports for
``shared.py`` itself live at ``imports.shared`` if defaults don't fit.

The script writes one helper module per entry in ``modules`` plus a
rewritten ``app.py`` spine with re-binding blocks. Run ``py_compile``
and the app's logic test suite afterward — see the rule file for the
import-sweep + missing-import patterns this won't catch.
"""
from __future__ import annotations

import argparse
import ast
import json
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


SHARED_DEFAULT_IMPORTS = """from __future__ import annotations

import re
from pathlib import Path
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


def parse_module_members(text: str) -> list[tuple[ast.AST, str]]:
    """Top-level (NOT inside the class) function/const members keyed by name.

    Used to source the names hoisted into ``shared.py`` — those live at
    module scope in the original ``app.py`` (constants, regexes, pure
    helpers), not as class attributes.
    """
    tree = ast.parse(text)
    out = []
    for node in tree.body:
        name = None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = node.name
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
        if name:
            out.append((node, name))
    return out


def node_source_range(node, lines: list[str] | None = None) -> tuple[int, int]:
    """1-based inclusive [start, end] line range, including decorators.

    When ``lines`` is supplied, also walks upward from the node's first line
    and absorbs contiguous ``#`` comment lines so block authorship (the
    "ARPABET → human-friendly description…" banner above a constant,
    decorative `# ── Passage drill ──` separators above a prompt) survives
    extraction. Stops at the first blank or non-comment line.
    """
    start = node.lineno
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.decorator_list:
        start = min(d.lineno for d in node.decorator_list)
    if lines is not None:
        i = start - 2  # 1-based start → 0-based prior line
        while i >= 0 and lines[i].lstrip().startswith("#"):
            start = i + 1
            i -= 1
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
    # Dedupe ``from __future__ import annotations`` — caller may have included
    # it explicitly; the seed below would otherwise produce two copies.
    caller_lines = [item.strip() for item in imports_spec]
    has_future = any(line.startswith("from __future__") for line in caller_lines)
    lines = [] if has_future else ["from __future__ import annotations\n"]
    for item in caller_lines:
        if item.startswith(("import ", "from ")):
            lines.append(item)
        else:
            lines.append(f"import {item}")
    # Always include the TYPE_CHECKING line in the import block; the conditional
    # gets emitted separately right after.
    if not any("TYPE_CHECKING" in line for line in lines):
        lines.append("from typing import TYPE_CHECKING")
    return "\n".join(lines) + "\n"


def write_shared(
    app_dir: Path,
    app_id: str,
    consumers: list[str],
    blocks: list[tuple[str, list[str]]],
    imports_spec: list[str] | None,
) -> Path:
    """Write ``shared.py`` — pure constants + pure functions, no class binding.

    ``blocks`` is a list of ``(kind, lines)`` tuples where ``kind`` is
    ``"const"`` or ``"func"``. Adjacent const blocks render with a single
    blank line between them (PEP 8 grouped assignments); any pair involving
    a func gets two blank lines (PEP 8 top-level def separation).
    """
    consumer_str = "/".join(sorted(consumers)) if consumers else "<list>"
    docstring = (
        f'"""{app_id} — module-level constants + pure helpers shared across helper modules.\n\n'
        f"Extracted from app.py so helper modules ({consumer_str}) can import these directly\n"
        f"without cycling through the spine `.app` module.\n\n"
        f'Pure functions only — no `self`, no kernel access, no I/O.\n'
        f'"""\n'
    )
    if imports_spec:
        # Dedupe ``from __future__ import annotations`` — caller may have
        # included it explicitly; we always want exactly one at the top.
        seed = "from __future__ import annotations"
        caller_lines = [item.strip() for item in imports_spec]
        has_future = any(line.startswith("from __future__") for line in caller_lines)
        lines = [] if has_future else [seed]
        for item in caller_lines:
            if item.startswith(("import ", "from ")):
                lines.append(item)
            else:
                lines.append(f"import {item}")
        imports = "\n".join(lines) + "\n"
    else:
        imports = SHARED_DEFAULT_IMPORTS
    # PEP-8 block joining: const-const → 1 blank line, anything with a func → 2.
    body_chunks: list[str] = []
    for i, (kind, blines) in enumerate(blocks):
        if i == 0:
            body_chunks.append("\n".join(blines))
            continue
        prev_kind = blocks[i - 1][0]
        sep = "\n\n" if kind == "const" and prev_kind == "const" else "\n\n\n"
        body_chunks.append(sep + "\n".join(blines))
    body = "".join(body_chunks)
    content = docstring + "\n" + imports + "\n\n" + body + "\n"
    out = app_dir / "shared.py"
    out.write_text(content, encoding="utf-8")
    return out


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
    shared_names: list[str] | None = None,
    shared_ranges: list[tuple[int, int]] | None = None,
) -> int:
    text = app_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    tree = ast.parse(text)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)

    pre_class = lines[: cls.lineno - 1]
    # Excise hoisted shared blocks from pre_class. ``shared_ranges`` are
    # 1-based inclusive line numbers in the original file; pre_class is the
    # same slice so 1-based indexing matches.
    if shared_ranges:
        drop = set()
        for start, end in shared_ranges:
            for i in range(start, end + 1):
                drop.add(i)
        pre_class = [line for i, line in enumerate(pre_class, start=1) if i not in drop]
    # Module-level code AFTER the class (helper functions, late constants,
    # `if __name__ == "__main__":` blocks) — preserved verbatim so the
    # decomposer doesn't drop it on the floor (caught on forge 2026-05-23).
    post_class = lines[cls.end_lineno:] if cls.end_lineno else []

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
        start, end = node_source_range(node, lines)
        spine.append(lines[start - 1: end])

    # Inject helper imports after the last existing sdk/external import.
    # Track paren/bracket depth so we land *after* the closing `)` of a
    # multi-line ``from X import (\n  A,\n  B,\n)`` block — landing mid-tuple
    # breaks the parser. We classify a logical import line as a target when it
    # STARTS (at depth 0) and only commit the insertion point once that
    # statement fully CLOSES (depth back to 0). The earlier version committed
    # the *start* line's index, which for a multi-line import pointed at the
    # line right after the opening ``(`` — i.e. inside the open paren — and
    # corrupted the import (caught on company/jobs/dictionary 2026-06-04;
    # the multi-line-paren guard regressed because it used a stale candidate).
    insert_at = len(pre_class)
    paren_depth = 0
    stmt_is_target = False
    for i, line in enumerate(pre_class):
        if paren_depth == 0:
            stmt_is_target = line.startswith(
                ("from emptyos.sdk", "from emptyos.", "from apscheduler")
            )
        paren_depth += line.count("(") - line.count(")")
        paren_depth += line.count("[") - line.count("]")
        if paren_depth < 0:
            paren_depth = 0
        if paren_depth == 0 and stmt_is_target:
            insert_at = i + 1
            stmt_is_target = False

    new_imports = [""]
    for mod in sorted(routes):
        alias = module_aliases[mod]
        new_imports.append(f"from . import {mod} as {alias}")
    if shared_names:
        names_sorted = sorted(shared_names)
        if len(names_sorted) <= 3:
            new_imports.append(f"from .shared import {', '.join(names_sorted)}")
        else:
            new_imports.append("from .shared import (")
            for n in names_sorted:
                new_imports.append(f"    {n},")
            new_imports.append(")")
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
        if not names:
            continue
        longest = max(len(n) for n in names)
        out.append("")
        out.append(f"    # ── {mod.replace('_', ' ').title()} (extracted to {mod}.py) ──")
        for n in names:
            out.append(f"    {n:<{longest}} = {alias}.{n}")

    # Restore any module-level code that lived AFTER the class.
    if post_class:
        out.append("")
        out.extend(post_class)

    # Collapse runs of 3+ blank lines to 2 (PEP 8 max between top-level defs).
    # Shared-block excision can leave long blank runs where blocks used to
    # sit, and the inter-section separators added during assembly can stack
    # on top of pre-existing blanks.
    normalized: list[str] = []
    blank_run = 0
    for line in out:
        if line.strip() == "":
            blank_run += 1
            if blank_run <= 2:
                normalized.append(line)
        else:
            blank_run = 0
            normalized.append(line)
    out = normalized

    app_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return sum(1 for _ in app_path.open(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Decompose an EmptyOS app's monolith app.py.")
    ap.add_argument("app_id", help="App directory under apps/ or apps/personal/")
    ap.add_argument("class_name", help="BaseApp subclass name (e.g. RoomsApp)")
    ap.add_argument("routes_spec", help="Path to routes JSON file")
    args = ap.parse_args()

    # Resolve app dir anywhere in the track tree (public/extension/personal).
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from emptyos.sdk.app_layout import resolve_app_dir

    app_dir = resolve_app_dir(Path("apps"), args.app_id, include_personal=True)
    if app_dir is None or not (app_dir / "app.py").exists():
        print(f"FAIL: app '{args.app_id}' (app.py) not found anywhere under apps/")
        return 1
    app_path = app_dir / "app.py"

    spec = json.loads(Path(args.routes_spec).read_text(encoding="utf-8"))
    routes: dict[str, list[str]] = spec["modules"]
    imports_spec: dict[str, list[str]] = spec.get("imports", {})
    meta_spec: dict[str, dict] = spec.get("meta", {})
    alias_spec: dict[str, str] = spec.get("aliases", {})
    shared_spec: list[str] = spec.get("shared", []) or []

    text = app_path.read_text(encoding="utf-8")
    cls, members = parse_class_members(text, args.class_name)
    lines = text.splitlines()
    name_to_module = {n: mod for mod, names in routes.items() for n in names}

    # Resolve shared (module-level) names BEFORE class extraction — they're
    # parsed from the same source but live in module scope, not class body.
    shared_ranges: list[tuple[int, int]] = []
    shared_blocks: list[tuple[str, list[str]]] = []
    if shared_spec:
        module_members = parse_module_members(text)
        module_names = {n for _, n in module_members}
        missing = [n for n in shared_spec if n not in module_names]
        if missing:
            print(
                f"FAIL: 'shared' references names not at module level "
                f"in app.py: {missing[:10]}. Module-level names found: "
                f"{sorted(module_names)[:20]}"
            )
            return 1
        class_collisions = [n for n in shared_spec if n in {nm for _, nm, _ in members}]
        if class_collisions:
            print(
                f"FAIL: 'shared' names also appear as class members: "
                f"{class_collisions}. shared.py is for module-level pure "
                f"helpers only — class methods belong in routed helpers."
            )
            return 1
        node_by_name = {n: node for node, n in module_members}
        for n in shared_spec:
            node = node_by_name[n]
            start, end = node_source_range(node, lines)
            kind = "func" if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else "const"
            shared_ranges.append((start, end))
            shared_blocks.append((kind, lines[start - 1: end]))

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
        start, end = node_source_range(node, lines)
        buckets[mod].append({"name": name, "kind": kind, "lines": lines[start - 1: end]})

    # Write shared.py first — helpers may declare ``from .shared import ...``
    # in their imports_spec, so the file should exist before the importer
    # would run.
    if shared_spec:
        shared_out = write_shared(
            app_dir, args.app_id, list(routes.keys()),
            shared_blocks, imports_spec.get("shared"),
        )
        print(f"  Wrote {shared_out} ({sum(1 for _ in shared_out.open(encoding='utf-8'))} lines, {len(shared_spec)} members)")

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

    n = rewrite_app(
        app_path, args.class_name, name_to_module, module_aliases, routes,
        shared_names=shared_spec or None,
        shared_ranges=shared_ranges or None,
    )
    print(f"  Rewrote {app_path} ({n} lines)")

    print()
    print("Next steps:")
    print(f"  1. python -m py_compile apps/{args.app_id}/*.py  # sanity check")
    print(f"  2. python -m pytest tests/test_sys_{args.app_id}*.py -v  # surface missing imports")
    print( "  3. Restart the daemon; re-fetch /integrity/api/audit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
