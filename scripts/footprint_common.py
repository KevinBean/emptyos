"""Shared AI-session footprint primitives — scan session JSONL into time runs.

The coding agents (Claude Code, Codex) keep a timestamped record of every
action taken on your behalf. That record IS a timesheet. These pure helpers
read it into runs of continuous activity, unioned across tools onto one
timeline (there is only one human).

Extracted from ``scripts/footprint_worklog.py`` so both the CLI script and the
``worklog-capture`` app's evidence layer (``apps/.../worklog-capture/sources.py``)
read sessions the same way, rather than duplicating the scan logic. Named
without a leading underscore because ``scripts/_*.py`` is gitignored — a shared
helper must stay tracked (see ``.claude/rules/dev-gotchas.md``).

Pure stdlib, no kernel, no I/O beyond reading the session files.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude" / "projects"
CODEX_DIR = Path.home() / ".codex" / "sessions"


def iter_session_files(claude_project: str | None) -> list[tuple[str, Path]]:
    """List ``(tool, path)`` for every session JSONL.

    ``claude_project`` scopes Claude to one ``~/.claude/projects/<dir>``; None
    scans them all. Codex sessions are always scanned recursively.
    """
    out: list[tuple[str, Path]] = []
    if CLAUDE_DIR.exists():
        roots = [CLAUDE_DIR / claude_project] if claude_project else list(CLAUDE_DIR.iterdir())
        for root in roots:
            if root.is_dir():
                out += [("claude", p) for p in root.glob("*.jsonl")]
    if CODEX_DIR.exists():
        out += [("codex", p) for p in CODEX_DIR.rglob("*.jsonl")]
    return out


def on_topic_events(path: Path, pattern: re.Pattern, tz: timezone) -> list[datetime]:
    """Timestamps of records in ``path`` whose raw line matches ``pattern``."""
    hits: list[datetime] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"timestamp"' not in line or not pattern.search(line):
                    continue
                try:
                    ts = json.loads(line).get("timestamp")
                    hits.append(
                        datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(tz)
                    )
                except Exception:
                    continue
    except OSError:
        pass
    return hits


def runs_from(events: list[datetime], idle: timedelta, tail: timedelta):
    """Collapse sorted event times into ``(start, end)`` runs.

    Consecutive events <= ``idle`` apart are one run counted at full wall-clock;
    ``tail`` is credited to a run's final event so an isolated mention costs
    minutes, not zero.
    """
    if not events:
        return []
    events = sorted(events)
    runs, start, prev = [], events[0], events[0]
    for t in events[1:]:
        if t - prev > idle:
            runs.append((start, prev + tail))
            start = t
        prev = t
    runs.append((start, prev + tail))
    return runs


def union(intervals):
    """Merge overlapping ``(start, end)`` intervals (removes concurrent overlap)."""
    if not intervals:
        return []
    merged = [list(iv) for iv in sorted(intervals)[:1]]
    for s, e in sorted(intervals)[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged


def scan(pattern, idle, tail, tz, claude_project=None, since: date | None = None):
    """Topic-filtered scan -> ``(per_day, per_tool_hours, runs, overlap_h)``.

    Every run whose text matches ``pattern`` is unioned onto one timeline so
    Claude and Codex running side by side are counted once.
    """
    per_tool_runs = defaultdict(list)
    for tool, path in iter_session_files(claude_project):
        ev = on_topic_events(path, pattern, tz)
        if not ev:
            continue
        for s, e in runs_from(ev, idle, tail):
            if since and s.date() < since:
                continue  # filter here, so per-tool totals match the reported days
            per_tool_runs[tool].append({"start": s, "end": e, "session": path.stem[:28]})

    runs = [dict(r, tool=t) for t, rs in per_tool_runs.items() for r in rs]
    runs.sort(key=lambda r: r["start"])

    per_tool_h = {
        t: sum((r["end"] - r["start"]).total_seconds() for r in rs) / 3600
        for t, rs in per_tool_runs.items()
    }
    merged = union([(r["start"], r["end"]) for r in runs])
    total = sum((e - s).total_seconds() for s, e in merged) / 3600
    overlap = sum(per_tool_h.values()) - total

    per_day: dict[date, dict] = defaultdict(
        lambda: {"h": 0.0, "runs": 0, "first": None, "last": None, "tools": set(), "sessions": set()}
    )
    for s, e in merged:
        d = per_day[s.date()]
        d["h"] += (e - s).total_seconds() / 3600
        d["runs"] += 1
        d["first"] = s if d["first"] is None else min(d["first"], s)
        d["last"] = e if d["last"] is None else max(d["last"], e)
    for r in runs:
        d = per_day[r["start"].date()]
        d["tools"].add(r["tool"])
        d["sessions"].add(r["session"])
    return dict(per_day), per_tool_h, runs, overlap
