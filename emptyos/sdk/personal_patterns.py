"""Shared loader for `.eos-personal` regex patterns.

Consumers today:
  - `scripts/check-personal.py` — pre-commit / release-time content scanner
  - `emptyos.web.server` PresentationMiddleware — runtime response scrubber
  - `emptyos.kernel.syslog` — write-time log scrubber
  - `emptyos.capabilities.outbound_scan` — pre-cloud scanner

All want the same parse: comments + blanks skipped, one regex per line.
What differs is how invalid lines are surfaced (CLI prints to stderr;
middleware/scrubbers silently drop them so a malformed line can't crash
a request). The optional `on_error` callback lets each caller pick its
own behaviour.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable


def load(
    path: str | Path,
    *,
    on_error: Callable[[str, Exception], None] | None = None,
) -> list[re.Pattern]:
    """Read `.eos-personal` and return one compiled regex per non-comment line.

    Returns an empty list if the file is missing — callers can decide whether
    that's an error (release-public.py) or a no-op (middleware with an
    unconfigured deployment).

    Patterns are compiled **case-insensitively**. Personal data does not care about
    casing — Windows paths are case-insensitive, so a mis-cased vault or home
    directory is the *same* directory while being a *different* string. Two such
    variants sat in tracked files precisely because the gate matched case-sensitively
    (found 2026-07-17). Every consumer here is a protective filter — the commit gate,
    syslog redaction, the outbound leak-scan — so widening only ever redacts or
    refuses *more*, never less.

    Measured before adopting: across 3,647 tracked files this yields exactly one
    additional finding, and it is a true positive. Word-boundary and structural
    controls (`renenerventure`, bare `Kevin`, `binbian.net`) are unaffected —
    they discriminate on shape, not case (`tests/test_unit_privacy_patterns.py`).
    """
    p = Path(path)
    if not p.exists():
        return []
    patterns: list[re.Pattern] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            patterns.append(re.compile(line, re.IGNORECASE))
        except re.error as e:
            if on_error is not None:
                on_error(line, e)
    return patterns


def find_and_load(start: Path | None = None) -> list[re.Pattern]:
    """Walk up from `start` looking for `.eos-personal`; load on first hit.

    `start` defaults to this module's resolved path — works for runtime
    callers that don't know the repo root. Returns empty list if no
    `.eos-personal` is found in any ancestor directory.

    Use this when the caller is a runtime module (kernel, capability,
    plugin) without a `kernel.config` to consult. For release-time scripts
    that DO know the repo root, call `load()` directly with an explicit
    path so the dependency is obvious.
    """
    if start is None:
        start = Path(__file__).resolve()
    for parent in start.parents:
        f = parent / ".eos-personal"
        if f.exists():
            return load(f)
    return []
