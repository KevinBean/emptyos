"""Log reducer — collapse duplicate lines, always preserve signal.

Deterministic + query-independent. NEVER drops an ERROR / WARN / failed-test /
Traceback line. Collapses runs of identical lines into ``Nx <line>`` and keeps
a head + tail window so the shape of the log survives.
"""

from __future__ import annotations

import re

from ..budget import est_tokens
from ..blocks import ReducerResult

_SIGNAL = re.compile(
    r"\b(error|err|warn|warning|critical|fatal|fail|failed|failure|traceback|exception|panic)\b",
    re.IGNORECASE,
)
_BOUNDARY = re.compile(
    r"\b(started|starting|startup|shutdown|stopping|stopped|listening|booted|ready|kernel|daemon)\b",
    re.IGNORECASE,
)

HEAD_WINDOW = 3
TAIL_WINDOW = 3


def _is_signal(line: str) -> bool:
    return bool(_SIGNAL.search(line))


def reduce(text: str) -> ReducerResult:
    original_tokens = est_tokens(text)
    lines = text.splitlines()
    n = len(lines)
    if n == 0:
        return ReducerResult(text=text, original_tokens=original_tokens, packed_tokens=original_tokens)

    keep_idx: set[int] = set(range(min(HEAD_WINDOW, n)))
    keep_idx |= set(range(max(0, n - TAIL_WINDOW), n))

    signal_count = 0
    for i, line in enumerate(lines):
        if _is_signal(line):
            keep_idx.add(i)
            signal_count += 1
        elif _BOUNDARY.search(line):
            keep_idx.add(i)

    # Walk runs of identical consecutive lines. A run that contains any kept
    # index is shown once verbatim (with a duplicate-count note); a run with no
    # kept index collapses to "Nx <line>".
    out: list[str] = []
    collapsed = 0
    i = 0
    while i < n:
        j = i
        while j + 1 < n and lines[j + 1] == lines[i]:
            j += 1
        run = j - i + 1
        run_kept = any(k in keep_idx for k in range(i, j + 1))
        if run == 1:
            out.append(lines[i])
        elif run_kept:
            out.append(lines[i])
            out.append(f"... (+{run - 1}x identical)")
            collapsed += run - 1
        else:
            out.append(f"{run}x {lines[i]}")
            collapsed += run - 1
        i = j + 1

    header = (
        f"[log summary: {n} lines, {signal_count} signal (error/warn), "
        f"{collapsed} duplicate lines collapsed]"
    )
    summary = header + "\n" + "\n".join(out)
    return ReducerResult(
        text=summary,
        original_tokens=original_tokens,
        packed_tokens=est_tokens(summary),
        warnings=[],
    )
