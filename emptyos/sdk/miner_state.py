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
import math
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


def recency_score(
    count: int, recent: int, *, boost: int = 5, days_idle: float = 0.0
) -> int:
    """The miner priority base: *damped* lifetime frequency + a live recency
    term, decayed once a finding goes quiet.

    Was ``count + boost * recent`` until 2026-08-05, which made lifetime count
    unbounded and therefore dominant: a music-video render batch that ended
    four days earlier held the top 15 syslog issues at 824 cumulative
    occurrences and 0 in the last 24h, burying everything actually firing. Three
    corrections, each aimed at one way that formula lied:

    * **Lifetime frequency is logarithmic.** It should express "this recurs",
      which is a rank signal, not a linear budget. 800 occurrences is not 80×
      more interesting than 10.
    * **Recency stays linear**, so it dominates as intended.
    * **An idle finding decays** — halving weekly once quiet for over a day.
      Nothing is deleted; a finding that resumes recovers immediately via its
      ``recent`` term. ``days_idle`` defaults to 0, so a caller that does not
      track last-seen keeps un-decayed behaviour.
    """
    freq = 10.0 * math.log2(1.0 + max(0, int(count)))
    live = boost * max(0, int(recent))
    if days_idle > 1.0 and recent <= 0:
        freq *= 0.5 ** ((days_idle - 1.0) / 7.0)
    return int(round(freq + live))
