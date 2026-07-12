"""Parse a vault-stored bulk-library JSON file into its entry list.

Several apps keep a bulk catalogue as ONE vault JSON file (deliberately not
one-note-per-entry — 100+ rows would flood boards views and pickers):
cable_network's rating library and cable-library's catalogue both read
``30_Resources/Electrical-Engineering/cables/library.json``; the overhead-line conductor loader
reads ``30_Resources/Electrical-Engineering/conductors/library.json``. All accept either a bare
list or the importer envelope ``{"_meta": ..., "entries": [...]}`` and fail
soft to ``[]``. Extracted on the third consumer (CLAUDE.md rule 9).

Pure — no ``self``, no I/O. Callers fetch the text (``vault_read_at`` etc.)
and keep their own per-entry validation.
"""

from __future__ import annotations

import json


def parse_json_library(text: str) -> list[dict]:
    """JSON text → entry list. Accepts a bare list or ``{"entries": [...]}``;
    anything else (empty, invalid JSON, wrong shape) → ``[]``. Never raises."""
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except ValueError:
        return []
    if isinstance(parsed, dict):
        parsed = parsed.get("entries")
    return parsed if isinstance(parsed, list) else []
