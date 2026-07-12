"""Shared parse of `emptyos/web/static/theme.css` — one source of truth.

Two checkers read the theme's token declarations and were doing it with their own
near-identical regexes (`check-contrast.py` for the *values*, `check-text-tokens.py`
for the *names*). Second consumer → extract, per CLAUDE.md rule 9. If theme.css's
shape ever changes, exactly one parser needs updating.

Deliberately regex, not a CSS parser: theme.css is a flat, hand-maintained file of
`.theme-<name> { --tok: value; … }` blocks plus a `:root` block, and a dependency
would buy nothing.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
THEME_CSS = REPO / "emptyos" / "web" / "static" / "theme.css"

_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_THEME_BLOCK = re.compile(r"\.theme-([a-z0-9-]+)\s*\{([^{}]*)\}", re.IGNORECASE | re.DOTALL)
_VAR = re.compile(r"--([\w-]+)\s*:\s*([^;]+);")

#: Matches a `:root` / `html` / `body` declaration block. Public — callers scanning
#: *other* stylesheets for token hijacks need the same notion of "global block".
ROOT_BLOCK = re.compile(r"(?:^|[\s,{}])(?::root|html|body)\s*(?:,[^{]*)?\{([^{}]*)\}", re.MULTILINE)


def blank_comments(src: str) -> str:
    """Blank out `/* … */` bodies, preserving newlines so line numbers hold.

    Prose in a comment ("mixed toward --text:") is not a declaration — quickref's
    own explanatory comment tripped the token scan before this existed.
    """
    return _COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), src)


def parse_themes(css: str) -> dict[str, dict[str, str]]:
    """`.theme-<name>` blocks → {theme: {token: raw_value}}. Colour tokens only,
    by construction — the shared non-colour scale lives in `:root`."""
    return {
        m.group(1): {v.group(1): v.group(2).strip() for v in _VAR.finditer(m.group(2))}
        for m in _THEME_BLOCK.finditer(blank_comments(css))
    }


def parse_root(css: str) -> dict[str, str]:
    """`:root` / `html` / `body` blocks → {token: raw_value} (merged)."""
    out: dict[str, str] = {}
    for m in ROOT_BLOCK.finditer(blank_comments(css)):
        out.update({v.group(1): v.group(2).strip() for v in _VAR.finditer(m.group(1))})
    return out


def load(path: Path = THEME_CSS) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""
