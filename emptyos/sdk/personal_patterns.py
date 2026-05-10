"""Shared loader for `.eos-personal` regex patterns.

Two consumers today:
  - `scripts/check-personal.py` — pre-commit / release-time content scanner
  - `emptyos.web.server` PresentationMiddleware — runtime response scrubber

Both want the same parse: comments + blanks skipped, one regex per line.
What differs is how invalid lines are surfaced (CLI prints to stderr; the
middleware silently drops them so a malformed line can't crash a request).
The optional `on_error` callback lets each caller pick its own behaviour.
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
            patterns.append(re.compile(line))
        except re.error as e:
            if on_error is not None:
                on_error(line, e)
    return patterns
