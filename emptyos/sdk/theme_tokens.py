"""Parse `emptyos/web/static/theme.css` — the one source of truth for theme colours.

theme.css declares every theme as a flat `.theme-<name> { --tok: value; … }` block.
Anything that needs those values — the contrast/text-token checkers, the publish
app's static-site generator — reads them from here rather than keeping a copy.

This started life as `scripts/theme_css.py`, serving the two static checkers. It
moved into the SDK when a *runtime* consumer appeared: the publish app had been
carrying a hand-copied `THEME_VARS` fork, which drifted badly enough to ship the
pre-2026-07-11 palettes — including the exact `success: #34d399` (1.9:1 on white)
and `warning: #d97706` that the readability audit had already fixed in theme.css.
Every published site inherited those. A fork of design tokens does not stay in
sync; that is the whole reason this module exists.

`scripts/theme_css.py` now re-exports from here, so the checkers are unchanged.

Deliberately regex, not a CSS parser: theme.css is flat and hand-maintained, and
a dependency would buy nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

#: Repo root → emptyos/sdk/theme_tokens.py is three levels down.
REPO = Path(__file__).resolve().parent.parent.parent
THEME_CSS = REPO / "emptyos" / "web" / "static" / "theme.css"

_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_THEME_BLOCK = re.compile(r"\.theme-([a-z0-9-]+)\s*\{([^{}]*)\}", re.IGNORECASE | re.DOTALL)
_VAR = re.compile(r"--([\w-]+)\s*:\s*([^;]+);")

#: Matches a `:root` / `html` / `body` declaration block. Public — callers scanning
#: *other* stylesheets for token hijacks need the same notion of "global block".
ROOT_BLOCK = re.compile(
    r"(?:^|[\s,{}])(?::root|html|body)\s*(?:,[^{]*)?\{([^{}]*)\}", re.MULTILINE
)


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


def global_token_prefixes(css: str | None = None) -> frozenset[str]:
    """Token families theme.css itself owns — `bg`, `accent`, `text`, `space`…

    Read from theme.css rather than hardcoded, for the same reason the T3 token
    set is: a literal list drifts the moment a family is added, and the drift is
    silent in both directions.

    The consumer is brand-island detection (`scanner_lib.is_brand_island`),
    which asks "does this file declare a *private* namespace". Answering that by
    prefix length alone is wrong in both directions, and the two scanners that
    tried it were wrong differently: theme.css owns 13 families of >=3 tokens,
    so `--accent-ink/-dim/-bg` reads as a private `accent-*` namespace at a
    6-char bound, and `--bg-*` / `--text-*` / `--fs-*` do so even at 4.
    """
    if css is None:
        css = load()
    return frozenset(
        m.group(1).split("-", 1)[0].lower()
        for m in _VAR.finditer(blank_comments(css))
        if "-" in m.group(1)
    )


#: Tokens a standalone export needs, in the snake_case shape those consumers use.
#: Every one is a literal hex/rgba in every theme — a static site has no daemon to
#: resolve `var()` against, so a token that became an indirection would silently
#: render as nothing. `theme_var_map` raises rather than emit that.
SITE_TOKENS = (
    "bg", "bg-card", "bg-input", "text", "text-heading", "text-secondary",
    "text-muted", "border", "border-strong", "accent", "accent-bg",
    "success", "warning", "danger",
)


def theme_var_map(css: str | None = None) -> dict[str, dict[str, str]]:
    """{theme_id: {snake_key: literal}} over SITE_TOKENS, for standalone exports.

    Raises ValueError if a token is missing or is not a literal colour, because
    both failure modes are invisible in the generated site — the affected text
    just renders unstyled against an unstyled background.
    """
    themes = parse_themes(css if css is not None else load())
    if not themes:
        # `load()` returns "" for a missing file, so an empty map here means
        # theme.css is absent or unparseable. Say so: callers assign the result
        # to a module-level dict and immediately index known ids, so returning {}
        # surfaces as a bare `KeyError: 'soft-light'` from inside an unrelated
        # app at import time.
        raise ValueError(f"no themes parsed from {THEME_CSS} — missing or unparseable")
    out: dict[str, dict[str, str]] = {}
    for name, toks in themes.items():
        vals: dict[str, str] = {}
        for tok in SITE_TOKENS:
            v = (toks.get(tok) or "").strip()
            if not v:
                raise ValueError(f"theme '{name}' is missing --{tok}")
            if not v.startswith(("#", "rgb(", "rgba(")):
                raise ValueError(
                    f"theme '{name}' --{tok} is {v!r}, not a literal colour; a "
                    "standalone export cannot resolve it"
                )
            vals[tok.replace("-", "_")] = v
        out[name] = vals
    return out
