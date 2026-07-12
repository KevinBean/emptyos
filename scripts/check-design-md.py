#!/usr/bin/env python3
"""Lint a DESIGN.md token file — unresolved refs, ref cycles, malformed colors.

DESIGN.md (the format google-labs-code/design.md + VoltAgent/awesome-design-md
both speak) carries machine-readable design tokens in YAML frontmatter:
`colors`, `typography`, `rounded`, `spacing`, `components`, … where component
rules reference tokens by `{group.key}` (e.g. `{colors.accent}`,
`{rounded.base}`). A hand-authored DESIGN.md can dangle a ref, cite a bad hex,
or cycle two tokens — none of which the consumer (`scripts/import_design_system.py`
→ a KB `pattern` note) catches before it becomes generation few-shot.

This validator is the gap-filler. Two homes:
  * import-time — `import_design_system.py` lints a fetched foreign DESIGN.md
    and refuses to convert a malformed one (the real consumer);
  * /preflight --scope ui + release — validates the canonical root DESIGN.md
    (generated from theme.css by gen-design-md.py, so it can't fail by
    construction — the gate is a backstop against a hand-edit).

Pure file I/O + PyYAML (already a daemon dep, used by import_design_system.py).
No kernel import, no daemon.

Exit code = number of errors (so it can gate). 0 = clean.

Usage:
    python scripts/check-design-md.py                 # lint root DESIGN.md
    python scripts/check-design-md.py path/to/DESIGN.md ...
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DESIGN_MD = ROOT / "DESIGN.md"

# Same ref grammar as import_design_system.py: {group.dotted.key}
_REF_RE = re.compile(r"\{(\w+)\.([\w.-]+)\}")
# A literal hex color: #rgb / #rgba / #rrggbb / #rrggbbaa
_HEX_OK = re.compile(r"^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def parse_frontmatter(text: str) -> dict | None:
    """Return the YAML frontmatter dict, or None if the file has no ---block---."""
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    loaded = yaml.safe_load(parts[1])
    return loaded if isinstance(loaded, dict) else None


def _leaves(node, prefix: tuple[str, ...] = ()):
    """Yield (dotted_path, value) for every scalar leaf in the token tree."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _leaves(v, prefix + (str(k),))
    elif isinstance(node, list):
        return  # lists (e.g. `themes:`) carry no {refs} we resolve
    else:
        yield ".".join(prefix), node


def _resolves(data: dict, group: str, dotted: str) -> bool:
    """True if `{group.dotted}` lands on a real node (leaf or subtree)."""
    node = data.get(group)
    for part in dotted.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return False
    return True


def _is_leaf(data: dict, dotted_full: str):
    """Return the scalar at a dotted path if it resolves to a leaf, else None."""
    node = data
    for part in dotted_full.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return None if isinstance(node, (dict, list)) else node


def lint_design_md(data: dict) -> list[str]:
    """Return a list of error strings for a parsed DESIGN.md frontmatter dict."""
    errors: list[str] = []
    leaves = [(p, v) for p, v in _leaves(data) if isinstance(v, str)]

    # 1. Unresolved token references.
    for path, value in leaves:
        for group, dotted in _REF_RE.findall(value):
            if not _resolves(data, group, dotted):
                errors.append(f"{path}: unresolved ref {{{group}.{dotted}}}")

    # 2. Reference cycles (only leaf->leaf edges can form a value cycle).
    graph: dict[str, set[str]] = {}
    for path, value in leaves:
        for group, dotted in _REF_RE.findall(value):
            target = f"{group}.{dotted}"
            if _is_leaf(data, target) is not None:
                graph.setdefault(path, set()).add(target)
    reported: set[frozenset[str]] = set()
    for start in list(graph):
        seen, stack = set(), [(start, (start,))]
        while stack:
            node, chain = stack.pop()
            for nxt in graph.get(node, ()):
                if nxt == start:
                    key = frozenset(chain)
                    if key not in reported:  # one report per logical cycle
                        reported.add(key)
                        errors.append("ref cycle: " + " -> ".join(chain + (start,)))
                    stack = []
                    break
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append((nxt, chain + (nxt,)))

    # 3. Malformed color values (only flag an unambiguous bad hex — a value that
    #    starts with '#' but isn't a valid 3/4/6/8-digit hex). Non-# values
    #    (rgb()/hsl()/named/transparent/{ref}) are left alone — zero false
    #    positives, per .claude/rules/audits.md.
    for path, value in _leaves(data.get("colors", {})):
        if isinstance(value, str) and value.startswith("#") and not _HEX_OK.match(value):
            errors.append(f"colors.{path}: malformed hex {value!r}")

    return errors


def lint_file(path: Path) -> list[str]:
    """Lint one DESIGN.md path. Missing frontmatter is a single error."""
    if not path.exists():
        return [f"{path}: not found"]
    data = parse_frontmatter(path.read_text(encoding="utf-8"))
    if data is None:
        return [f"{path}: no YAML frontmatter block"]
    return lint_design_md(data)


def main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv] or [DESIGN_MD]
    total = 0
    for path in paths:
        errors = lint_file(path)
        try:
            rel = path.relative_to(ROOT)
        except ValueError:
            rel = path
        if errors:
            print(f"✗ {rel} — {len(errors)} error(s)")
            for e in errors:
                print(f"    {e}")
            total += len(errors)
        else:
            print(f"✓ {rel}")
    if total:
        print(f"\n{total} DESIGN.md error(s). Fix the token refs / hex above.")
    return total


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
