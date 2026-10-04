"""iCalendar (RFC 5545) VEVENT parsing — pure, no I/O, no capabilities.

Extracted from `apps/public/standard/calendar/ics.py` (its cached-subscription
feature) when `countdown` became a second consumer needing the same VEVENT
parse for a one-shot "import from an .ics file" action (CLAUDE.md rule 9 —
extract on the second consumer). Fetching, caching, and scheduling stay in
`calendar` — this module only turns ICS text into a flat list of events.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta

_DURATION_RE = re.compile(
    r"^P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$", re.I
)


def _unescape(value: str) -> str:
    return (
        (value or "")
        .replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _parse_duration(raw: str) -> int:
    """ISO-8601 duration -> minutes. 0 when unparseable (treated as unknown)."""
    m = _DURATION_RE.match((raw or "").strip())
    if not m:
        return 0
    weeks, days, hours, minutes, seconds = (int(g or 0) for g in m.groups())
    return weeks * 10080 + days * 1440 + hours * 60 + minutes + seconds // 60


def _parse_start(raw: str) -> tuple[str, str]:
    value = (raw or "").strip()
    if len(value) < 8:
        return "", ""
    try:
        day = datetime.strptime(value[:8], "%Y%m%d").date().isoformat()
    except ValueError:
        return "", ""
    if "T" not in value or len(value) < 13:
        return day, ""
    try:
        hour, minute = int(value[9:11]), int(value[11:13])
        if value.endswith("Z"):
            utc_dt = datetime.strptime(value[:15], "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
            local = utc_dt.astimezone()
            return local.date().isoformat(), local.strftime("%H:%M")
        return day, f"{hour:02d}:{minute:02d}"
    except (TypeError, ValueError):
        return day, ""


def _event_end(day: str, time: str, fields: dict) -> str:
    """End time as ``HH:MM`` for a timed same-day event, else ``""``.

    Unknown is the honest default and the safe one: a consumer that can't tell
    how long an event runs (an all-day entry, a DTEND we couldn't parse, a
    cached event written before this field existed) should fall back to its own
    assumption rather than trust a fabricated end. An event running past
    midnight clamps to ``23:59`` — the caller reasons about one date at a time.
    """
    if not day or not time:
        return ""  # all-day events block nothing in particular
    end_day, end_time = _parse_start(fields.get("DTEND", ""))
    if not end_day:
        minutes = _parse_duration(fields.get("DURATION", ""))
        if not minutes:
            return ""
        try:
            start_dt = datetime.strptime(f"{day} {time}", "%Y-%m-%d %H:%M")
        except ValueError:
            return ""
        end_dt = start_dt + timedelta(minutes=minutes)
        return "23:59" if end_dt.date() > start_dt.date() else end_dt.strftime("%H:%M")
    if end_day > day:
        return "23:59"
    if end_day < day or not end_time or end_time <= time:
        return ""
    return end_time


def parse_ics(text: str, *, source: str = "Subscribed calendar") -> list[dict]:
    """Parse VEVENTs into a bounded flat shape: {id, date, time, end, title,
    source, href}. A cancelled or dateless/titleless VEVENT is dropped."""
    events: list[dict] = []
    current: dict[str, str] | None = None
    for line in _unfold(text):
        marker = line.strip().upper()
        if marker == "BEGIN:VEVENT":
            current = {}
            continue
        if marker == "END:VEVENT":
            if current is not None:
                day, time = _parse_start(current.get("DTSTART", ""))
                end = _event_end(day, time, current)
                title = _unescape(current.get("SUMMARY", "")).strip()
                if day and title and current.get("STATUS", "").upper() != "CANCELLED":
                    uid = current.get("UID") or hashlib.sha256(
                        f"{source}|{day}|{time}|{title}".encode()
                    ).hexdigest()[:16]
                    events.append(
                        {
                            "id": uid[:200],
                            "date": day,
                            "time": time,
                            "end": end,
                            "title": title[:500],
                            "source": source[:120],
                            "href": current.get("URL", "")[:1000],
                        }
                    )
            current = None
            continue
        if current is None or ":" not in line:
            continue
        raw_key, value = line.split(":", 1)
        key = raw_key.split(";", 1)[0].strip().upper()
        if (
            key in {"DTSTART", "DTEND", "DURATION", "SUMMARY", "UID", "URL", "STATUS"}
            and key not in current
        ):
            current[key] = value.strip()
    return events
