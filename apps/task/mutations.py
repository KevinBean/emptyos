"""Pure markdown checkbox line transforms — no IO, no app state.

Each function takes a single task line (the raw text, no trailing newline)
and returns either the rewritten line, or ``(new_line, action)`` for the
toggle case which collapses two state transitions. Callers do the read /
write / emit dance.

SDK-extraction candidate: journal and projects also rewrite ``- [ ]``
lines. When the second consumer needs these primitives, lift this module
to ``emptyos/sdk/markdown_tasks.py`` unchanged.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from emptyos.sdk import DUE_PATTERN

_DONE_DATE_RE = re.compile(r"\s*✅\s*\d{4}-\d{2}-\d{2}")
_DUE_DATE_RE = re.compile(r"\s*📅\s*\d{4}-\d{2}-\d{2}")
_BODY_RE = re.compile(r"^(\s*-\s*\[[ xX]\]\s*)(.*?)(\s*(?:📅|✅).*)?$")


def is_open(line: str) -> bool:
    return "[ ]" in line


def is_done(line: str) -> bool:
    return "[x]" in line or "[X]" in line


def complete(line: str, today_str: str) -> str:
    """Mark an open task done. Caller must have checked ``is_open(line)``."""
    out = line.replace("[ ]", "[x]", 1)
    if "✅" not in out:
        out = out.rstrip() + f" ✅ {today_str}"
    return out


def reopen(line: str) -> str:
    """Re-open a completed task. Caller must have checked ``is_done(line)``."""
    out = line.replace("[x]", "[ ]", 1).replace("[X]", "[ ]", 1)
    return _DONE_DATE_RE.sub("", out)


def toggle(line: str, today_str: str) -> tuple[str, str] | tuple[None, None]:
    """Toggle a task line. Returns (new_line, action) or (None, None) if no checkbox."""
    if is_open(line):
        return complete(line, today_str), "completed"
    if is_done(line):
        return reopen(line), "reopened"
    return None, None


def set_due(line: str, new_due: str) -> str:
    """Replace or insert the 📅 marker. Empty ``new_due`` strips it."""
    if not new_due:
        return _DUE_DATE_RE.sub("", line)
    m = DUE_PATTERN.search(line)
    if m:
        return line[: m.start(1)] + new_due + line[m.end(1) :]
    return line.rstrip() + f" 📅 {new_due}"


def snooze(line: str, days: int, from_date: date | None = None) -> str:
    """Push the due date forward by ``days``. Adds 📅 if the line had none."""
    new_due = ((from_date or date.today()) + timedelta(days=days)).isoformat()
    return set_due(line, new_due)


def rewrite_text(line: str, new_text: str) -> str | None:
    """Rewrite the body between checkbox and trailing markers. None if line is not a checkbox."""
    m = _BODY_RE.match(line)
    if not m:
        return None
    return m.group(1) + new_text + (m.group(3) or "")


def matches(line: str, query_text: str, want_done: bool | None = None) -> bool:
    """True if ``line`` is a task whose body contains ``query_text``.

    ``want_done=False`` matches only open lines; ``True`` only done lines;
    ``None`` matches either.
    """
    if want_done is True and not is_done(line):
        return False
    if want_done is False and not is_open(line):
        return False
    return query_text in line
