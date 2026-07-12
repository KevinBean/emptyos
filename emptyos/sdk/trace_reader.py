"""Stitch one trace_id's recorded work into an ordered timeline — the "Record"
half of Record & Replay (the ``replay`` app).

A session's semantic work is already recorded across two stores, both keyed by
the per-turn ``trace_id`` minted in ``emptyos/sdk/trace.py``:

  * ``data/events.db`` — ``think:executed`` events (the think steps), persisted by
    the EventBus. trace_id lives INSIDE the JSON ``data`` blob; events.db has no
    trace_id column/index (only ``idx_events_type``), so we read the most recent
    rows *by type* — the only index — and filter on trace_id in Python. Bounded:
    a very old trace can scroll past the window (documented best-effort; the
    primary distill path uses the agent session conversation, not this).
  * ``data/apps/agent/tool-audit.jsonl`` — one JSON line per agent tool call
    (Read/Grep/Edit/Bash/...), carrying ``trace_id`` when inside a traced turn.

:func:`stitch` is PURE (feed it the raw row lists) so it unit-tests without a
daemon. The read helpers do thin, read-only IO.

HONESTY NOTE — tool-audit lines are the agent's *internal* tools, NOT ``call_app``
semantic verbs. This module returns what was recorded; mapping raw tool/think
activity → semantic recipe steps (and pinning verbs to the live verb registry)
is the distiller's job (``emptyos/sdk/recipe_distill.py``), never a mechanical
conversion here.

Honors ``.claude/rules/daemon-handling.md``: opens sqlite read-only
(``file:...?mode=ro``) and never boots the kernel — safe to call while the
daemon owns the same DB.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

_DEFAULT_EVENT_TYPES = ("think:executed",)


@dataclass
class WorkItem:
    """One normalized step in a stitched work trace."""

    seq: int
    kind: str  # "tool" | "think"
    name: str  # tool name, or an "<app>:<domain>" label for a think step
    input: dict
    output: str | None = None
    ts: str = ""
    source: str = ""  # "audit" | "event"
    ok: bool | None = None
    duration_ms: int | None = None


def read_trace_events(
    data_root,
    trace_id: str,
    *,
    types: tuple[str, ...] = _DEFAULT_EVENT_TYPES,
    per_type_limit: int = 2000,
) -> list[dict]:
    """Recent events of ``types`` whose ``data.trace_id == trace_id``.

    Returns ``[{type, data, ts}, ...]`` newest-first per type. Empty on any
    error or when ``trace_id`` is falsy / events.db is absent.
    """
    if not trace_id:
        return []
    db_path = Path(data_root) / "events.db"
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    out: list[dict] = []
    try:
        for t in types:
            try:
                rows = conn.execute(
                    "SELECT type, data, timestamp FROM events "
                    "WHERE type = ? ORDER BY timestamp DESC LIMIT ?",
                    (t, per_type_limit),
                ).fetchall()
            except sqlite3.Error:
                continue
            for typ, data_json, ts in rows:
                try:
                    data = json.loads(data_json or "{}")
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(data, dict) and data.get("trace_id") == trace_id:
                    out.append({"type": typ, "data": data, "ts": ts})
    finally:
        conn.close()
    return out


def read_trace_audit(data_root, trace_id: str) -> list[dict]:
    """tool-audit.jsonl lines whose ``trace_id`` matches. Empty if absent."""
    if not trace_id:
        return []
    p = Path(data_root) / "apps" / "agent" / "tool-audit.jsonl"
    if not p.exists():
        return []
    out: list[dict] = []
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("trace_id") == trace_id:
            out.append(row)
    return out


def stitch(events: list[dict], audit: list[dict]) -> list[WorkItem]:
    """PURE — merge raw event rows + audit lines into a ts-ordered WorkItem list.

    Feed the lists exactly as :func:`read_trace_events` / :func:`read_trace_audit`
    return them. Sorted by ``(ts, source)`` with audit before event at equal ts
    (a tool call precedes the think it feeds). ``seq`` is assigned 1..N after the
    sort. Malformed rows are skipped, never raised.
    """
    items: list[WorkItem] = []
    for row in audit or []:
        if not isinstance(row, dict):
            continue
        items.append(
            WorkItem(
                seq=0,
                kind="tool",
                name=str(row.get("tool") or ""),
                input=row["input"] if isinstance(row.get("input"), dict) else {},
                output=None,
                ts=str(row.get("ts") or ""),
                source="audit",
                ok=row["ok"] if isinstance(row.get("ok"), bool) else None,
                duration_ms=(
                    row["duration_ms"] if isinstance(row.get("duration_ms"), int) else None
                ),
            )
        )
    for ev in events or []:
        if not isinstance(ev, dict) or not isinstance(ev.get("data"), dict):
            continue
        data = ev["data"]
        app = str(data.get("app") or "")
        domain = str(data.get("domain") or "")
        label = f"{app}:{domain}" if app and domain else (app or domain or "think")
        items.append(
            WorkItem(
                seq=0,
                kind="think",
                name=label,
                input={
                    k: data[k]
                    for k in ("domain", "prompt_len", "provider", "model")
                    if k in data
                },
                output=None,
                ts=str(ev.get("ts") or ""),
                source="event",
                ok=None,
                duration_ms=(
                    data["latency_ms"] if isinstance(data.get("latency_ms"), int) else None
                ),
            )
        )
    items.sort(key=lambda w: (w.ts, 0 if w.source == "audit" else 1))
    for i, w in enumerate(items, start=1):
        w.seq = i
    return items


def work_trace(data_root, trace_id: str) -> list[dict]:
    """Convenience: read both sources for ``trace_id``, stitch, return as dicts."""
    events = read_trace_events(data_root, trace_id)
    audit = read_trace_audit(data_root, trace_id)
    return [asdict(w) for w in stitch(events, audit)]
