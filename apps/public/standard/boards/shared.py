"""boards — module-level constants + pure helpers shared across helper modules.

Extracted so helper modules (activity, comments, …) can import these directly
without cycling through the spine ``.app`` module (multi-module rule 6 —
the one allowed cross-helper import).

Pure functions only — no ``self``, no kernel access, no I/O.
"""

from __future__ import annotations

import re


def safe_segment(raw: str, fallback: str = "x") -> str:
    """One filesystem-safe segment from a hostile path param (board id /
    item key): collapse anything outside [A-Za-z0-9._-] (kills separators
    and traversal), strip edge dots, fall back when nothing survives.

    Coercing by design — sidecar files must exist for whatever key the
    routes were called with. The attachments module keeps its own stricter
    reject-variant (returns None) because uploads should refuse, not rename.
    """
    return re.sub(r"[^A-Za-z0-9._-]+", "-", raw or fallback).strip(".") or fallback
