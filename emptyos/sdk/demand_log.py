"""Demand log — append-only record of low/empty-confidence retrievals.

Inspired by Raj Navakoti's Demand-Driven Context talk
(2026-05-05 Raj Navakoti - Demand-driven context.md): every agent failure
is data. When BaseApp.search() or BaseApp.vault_query() comes back empty,
or self.think(with_confidence=True) self-rates below threshold, we append
one line here. A periodic classifier over the log buckets entries into
clean / stale / duplicated / missing / tribal and feeds the journal as
#vault-gap tasks.

JSONL on purpose — grep-able, tail-able, no schema migration cost. SQLite
earns its keep when we need indexed queries; the log is append-mostly,
scan-rarely.

File: {data_dir}/demand_log.jsonl
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from emptyos.sdk.utils import now_iso

LOG_FILENAME = "demand_log.jsonl"


def append(data_dir: Path, entry: dict[str, Any]) -> None:
    """Append one entry to demand_log.jsonl. Never raises — logging must
    not break the caller. `entry` is merged with {ts: now_iso()} if ts
    is not already set."""
    try:
        if "ts" not in entry:
            entry = {"ts": now_iso(), **entry}
        path = Path(data_dir) / LOG_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def read_all(data_dir: Path) -> list[dict]:
    """Read every entry. Skips malformed lines. Empty list if missing."""
    path = Path(data_dir) / LOG_FILENAME
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception:
        return out
    return out


def summarize(data_dir: Path, *, limit: int = 10) -> dict[str, Any]:
    """Small aggregate view for growth tooling.

    The raw log stays JSONL for debugging; agents and audits need a compact
    summary that names repeated gaps instead of asking an LLM to scan every
    line. Returns only counts and recent samples, so it is cheap to include in
    integrity/reflect context.
    """
    entries = read_all(data_dir)
    by_kind: Counter[str] = Counter()
    by_app: Counter[str] = Counter()
    missing: Counter[str] = Counter()

    for entry in entries:
        kind = str(entry.get("kind") or "unknown")
        by_kind[kind] += 1
        app = entry.get("app")
        if app:
            by_app[str(app)] += 1
        for item in entry.get("missing") or []:
            missing[str(item)] += 1

    top_missing = [
        {"item": item, "count": count}
        for item, count in missing.most_common(limit)
    ]
    recent_unmet = [
        {
            "ts": entry.get("ts"),
            "app": entry.get("app"),
            "missing": entry.get("missing") or [],
        }
        for entry in reversed(entries)
        if entry.get("kind") == "unmet_dependency"
    ][:limit]

    return {
        "total": len(entries),
        "by_kind": dict(by_kind.most_common(limit)),
        "by_app": dict(by_app.most_common(limit)),
        "top_missing": top_missing,
        "recent_unmet_dependencies": recent_unmet,
    }
