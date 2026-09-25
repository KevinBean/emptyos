"""Shared parse of `emptyos/web/static/theme.css` — re-export of the SDK module.

The implementation moved to `emptyos/sdk/theme_tokens.py` when the publish app
(a runtime consumer, not a script) needed the same parse and could not import
from `scripts/`. This shim keeps `check-contrast.py` / `check-text-tokens.py`
importing `theme_css` unchanged.

Import from `emptyos.sdk.theme_tokens` in new code.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from emptyos.sdk.theme_tokens import (  # noqa: E402,F401
    REPO,
    ROOT_BLOCK,
    SITE_TOKENS,
    THEME_CSS,
    blank_comments,
    global_token_prefixes,
    load,
    parse_root,
    parse_themes,
    theme_var_map,
)

__all__ = [
    "ROOT_BLOCK", "SITE_TOKENS", "THEME_CSS", "REPO",
    "blank_comments", "global_token_prefixes", "load", "parse_root",
    "parse_themes", "theme_var_map",
]
