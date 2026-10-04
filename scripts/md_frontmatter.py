"""Shared YAML-frontmatter parser for the standalone scripts/ scanner family.

Kernel-free, stdlib-only — safe for any `scripts/*.py` that must run as a
black-box subprocess without importing `emptyos` (see
.claude/rules/daemon-handling.md). Co-located in `scripts/` so siblings import it
via `sys.path[0]` when run as `python scripts/<scanner>.py` (the same
sibling-import pattern documented for the hook trio — see memory
`reference_scripts_underscore_gitignored`). NO leading underscore → tracked.

Extracted 2026-06-07 as the 2nd consumer (`check_memory_rot.py`) joined
`kb_claim_audit.py` in needing the same scalar+block-list parse (CLAUDE.md
rule 9). `check_vault_test_leak.py` deliberately does NOT use this — its
inline-only identity-field check intentionally ignores block lists, and folding
it in would change its classification behaviour.

Handles the block-style frontmatter EmptyOS writes (CLAUDE.md gotcha — tags must
be block-style):

    ---
    name: foo
    related:
      - bar
      - baz
    ---
    body...

`parse_frontmatter` returns ``(fm_dict, body)``; scalars are quote-stripped,
empty ``key:`` followed by ``- item`` lines becomes a list. No frontmatter →
``({}, original_text)``.
"""

from __future__ import annotations

import re

_FIELD_RE = re.compile(r"^(\w[\w-]*):\s*(.*)$")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse leading ``---`` frontmatter; return (fields, body-after-frontmatter)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    fm: dict = {}
    cur_key = None
    for line in text[3:end].splitlines():
        if not line.strip():
            continue
        m = _FIELD_RE.match(line)
        if m and not line.startswith(" "):
            key, val = m.group(1), m.group(2).strip().strip('"').strip("'")
            if val == "":
                fm[key] = []
                cur_key = key
            else:
                fm[key] = val
                cur_key = None
        elif line.startswith(" ") and line.strip().startswith("- ") and cur_key:
            item = line.strip()[2:].strip().strip('"').strip("'")
            if not isinstance(fm.get(cur_key), list):
                fm[cur_key] = []
            fm[cur_key].append(item)
    return fm, text[end + 4:]


def parse_fm(text: str) -> dict:
    """Frontmatter fields only (drop the body) — `kb_claim_audit` shape."""
    return parse_frontmatter(text)[0]
