"""Shared plumbing for miner-shaped maintenance loops.

The miner shape (trace-miner = syslog, kb-gap-miner = Q&A history; catalog in
``.claude/rules/self-audit-loops.md``) keeps a scored, status-carrying
findings dict in a single ``data/`` JSON file and groups raw observations by
a stable short hash of a normalized signature. This module is that shared
plumbing, extracted on the second consumer per CLAUDE.md rule 9.

kb-butler's state is deliberately NOT moved onto this helper — it is a
work-queue + history shape, not a findings dict; forcing it here would be
over-abstraction.

Pure functions only — no kernel access, no BaseApp, no I/O beyond the one
JSON file the caller names.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def sig_hash(signature: str) -> str:
    """Stable 12-hex identity for a normalized signature string."""
    return hashlib.sha1(signature.encode("utf-8", "replace")).hexdigest()[:12]


def load_state(path: Path) -> dict:
    """Read a miner's findings dict. Missing or corrupt file → ``{}`` — a
    miner must never refuse to sweep because last sweep's state went bad;
    the findings rebuild from the source on the next pass."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def recency_score(count: int, recent: int, *, boost: int = 5) -> int:
    """The miner priority base: frequency with a heavy recency boost."""
    return count + boost * recent
