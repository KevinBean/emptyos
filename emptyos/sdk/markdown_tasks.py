"""Pure markdown checkbox line transforms — no IO, no app state.

Each function takes a single task line (the raw text, no trailing newline)
and returns either the rewritten line, or ``(new_line, action)`` for the
toggle case which collapses two state transitions. Callers do the read /
write / emit dance.

Promoted from ``apps/public/core/task/mutations.py`` (2026-07-03) per that
module's own SDK-extraction note, once ``apps/public/standard/projects``
became the second consumer needing the same checkbox-flip + staleness-guard
primitives instead of hand-rolling a divergent regex copy. The task app
re-exports these names from ``apps/public/core/task/mutations.py`` so its
existing ``from . import mutations`` call sites need no changes.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from emptyos.sdk.utils import DUE_PATTERN, TASK_RE

_DONE_DATE_RE = re.compile(r"\s*✅\s*\d{4}-\d{2}-\d{2}")
_DUE_DATE_RE = re.compile(r"\s*📅\s*\d{4}-\d{2}-\d{2}")
_BODY_RE = re.compile(r"^(\s*-\s*\[[ xX]\]\s*)(.*?)(\s*(?:📅|✅).*)?$")
_ACTION_TAG_RE = re.compile(r"\s*#(?:next|someday|waiting)\b")
_COMPLETION_NOTE_LABEL = "Completion note"

# ── Obsidian-Tasks priority markers (highest → lowest) ───────────────────────
# Insertion order is strongest-first so ``priority_level`` reports the highest
# marker when several are present.
PRIORITY_EMOJI = {
    "highest": "🔺",
    "high": "⏫",
    "medium": "🔼",
    "low": "🔽",
    "lowest": "⏬",
}
_PRIORITY_STRIP_RE = re.compile(r"\s*[" + "".join(PRIORITY_EMOJI.values()) + r"]")

# ── Recurrence (🔁 frequency) ────────────────────────────────────────────────
_RECUR_RE = re.compile(
    r"🔁\s*(daily|weekly|biweekly|fortnightly|monthly|yearly|annually)", re.IGNORECASE
)
_RECUR_DELTA = {
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
    "biweekly": timedelta(weeks=2),
    "fortnightly": timedelta(weeks=2),
}


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


def task_text(line: str) -> str:
    """Return the indexed task text for a checkbox line, or empty when not a task."""
    m = TASK_RE.match((line or "").strip())
    return m.group(2).strip() if m else ""


def text_matches(line: str, expected_text: str) -> bool:
    """Whether ``line``'s indexed task text equals ``expected_text``.

    An empty ``expected_text`` means "caller opted out of verification" and
    matches anything — generic helper behaviour. Callers that need the
    stale-line guard (e.g. complete-with-note) must require a non-empty text
    *before* calling this; an empty string here is NOT a safe identity check.
    """
    expected = (expected_text or "").strip()
    return not expected or task_text(line) == expected


def completion_note_lines(note: str) -> list[str]:
    """Return markdown child lines for a completion note.

    Continuation lines use blockquote markers so a note that starts with
    ``- [ ]`` cannot be re-indexed as a new task after whitespace stripping.
    """
    parts = [
        line.strip()
        for line in (note or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if line.strip()
    ]
    if not parts:
        return []
    return [f"  - {_COMPLETION_NOTE_LABEL}: {parts[0]}"] + [
        f"    > {line}" for line in parts[1:]
    ]


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


def set_actionability(line: str, state: str) -> str | None:
    """Set the ``#next``/``#someday``/``#waiting`` tag on a task line.

    Strips any existing actionability tag first, then appends the requested
    one (before trailing 📅/✅ markers). An empty/``"someday"``-without-need
    caller can pass ``state=""`` to strip all action tags and fall back to
    pure inference. Returns None if the line is not a checkbox.
    """
    m = _BODY_RE.match(line)
    if not m:
        return None
    body = _ACTION_TAG_RE.sub("", m.group(2)).rstrip()
    if state in ("next", "someday", "waiting"):
        body = f"{body} #{state}" if body else f"#{state}"
    return m.group(1) + body + (m.group(3) or "")


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


# ── priority ─────────────────────────────────────────────────────────────────


def priority_level(text: str) -> str:
    """Level name of the strongest Obsidian-Tasks priority marker, or ''."""
    for level, emoji in PRIORITY_EMOJI.items():  # highest-first
        if emoji in (text or ""):
            return level
    return ""


def set_priority(line: str, level: str) -> str | None:
    """Set / replace / clear the priority marker on a task line.

    ``level`` ∈ highest/high/medium/low/lowest (empty clears). Strips every
    existing priority marker from the body first, so this is idempotent. The
    chosen marker lands at the end of the body, before any trailing 📅/✅.
    Returns None if the line is not a checkbox.
    """
    m = _BODY_RE.match(line)
    if not m:
        return None
    body = _PRIORITY_STRIP_RE.sub("", m.group(2)).rstrip()
    lvl = (level or "").strip().lower()
    if lvl in PRIORITY_EMOJI:
        body = f"{body} {PRIORITY_EMOJI[lvl]}" if body else PRIORITY_EMOJI[lvl]
    return m.group(1) + body + (m.group(3) or "")


# ── recurrence ───────────────────────────────────────────────────────────────


def recurrence_freq(line: str) -> str:
    """The 🔁 recurrence frequency on a line (lowercased), or '' if none."""
    m = _RECUR_RE.search(line or "")
    return m.group(1).lower() if m else ""


def _add_months(base: date, months: int) -> date:
    """base + ``months`` calendar months, clamping the day (Jan 31 → Feb 28)."""
    month_index = base.month - 1 + months
    year = base.year + month_index // 12
    month = month_index % 12 + 1
    # Last valid day of the target month.
    if month == 12:
        last = 31
    else:
        last = (date(year, month + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(base.day, last))


def next_occurrence(freq: str, base: date) -> date | None:
    """Next due date for a recurrence frequency after ``base``, or None."""
    f = (freq or "").lower()
    if f in _RECUR_DELTA:
        return base + _RECUR_DELTA[f]
    if f == "monthly":
        return _add_months(base, 1)
    if f in ("yearly", "annually"):
        return _add_months(base, 12)
    return None


def regenerate_recurring(line: str, completed_date: date) -> str | None:
    """Given a recurring task line, return a fresh OPEN line for the next
    occurrence, or None if the line has no 🔁 marker.

    Rolls from the line's existing 📅 due if present, else from
    ``completed_date``. The returned line is open (``[ ]``), keeps the body +
    🔁 marker, carries no ✅, and has the new 📅 due.
    """
    freq = recurrence_freq(line)
    if not freq:
        return None
    base: date | None = None
    m = DUE_PATTERN.search(line)
    if m:
        try:
            base = date.fromisoformat(m.group(1)[:10])
        except (ValueError, TypeError):
            base = None
    if base is None:
        base = completed_date
    nxt = next_occurrence(freq, base)
    if nxt is None:
        return None
    fresh = reopen(line) if is_done(line) else line
    return set_due(fresh, nxt.isoformat())
