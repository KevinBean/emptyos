"""Learn — module-level pure helpers shared across helper modules.

Extracted from app.py so helper modules (diagnostic.py) can use `_parse_lessons`
directly without cycling through the spine `.app` module (which imports them).

Pure functions only — no `self`, no kernel access, no I/O.
"""

from __future__ import annotations

import json


def _parse_lessons(fm: dict) -> list[dict]:
    """Read lessons from `lessons_json` (preferred, JSON-encoded string to dodge
    the vault YAML parser's nested-dict-list flattening) with fallback to the
    legacy `lessons` list. Returns a list of {slug, title, kind, duration_min}
    dicts; bad entries are skipped."""
    raw = fm.get("lessons_json")
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [l for l in parsed if isinstance(l, dict)]
        except Exception:
            pass
    legacy = fm.get("lessons")
    if isinstance(legacy, list):
        return [l for l in legacy if isinstance(l, dict)]
    return []
