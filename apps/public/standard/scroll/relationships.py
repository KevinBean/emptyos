"""Inter-persona state for scroll.

Relationships: 6-axis (affinity, familiarity, trust, dislike, attraction,
awkwardness) + derived status. Stored as one JSON file with ordered-pair
keys.

Memories: per-persona append-only JSONL log of events the persona has
participated in (clip published, liked, skipped, …). Used as director
context when generating future clips.

Lives in `data/apps/scroll/` because this is event-derived telemetry,
not user-authored knowledge (CLAUDE.md vault/data split rule).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from threading import Lock
from typing import Iterable

AXES = ("affinity", "familiarity", "trust", "dislike", "attraction", "awkwardness")


def status_from_axes(axes: dict) -> str:
    """Derive a status label from axis values. Cheap heuristic, not a state machine yet."""
    aff = float(axes.get("affinity", 0.0))
    fam = float(axes.get("familiarity", 0.0))
    dis = float(axes.get("dislike", 0.0))
    atr = float(axes.get("attraction", 0.0))
    if dis >= 0.6:
        return "estranged" if fam > 0.5 else "conflict"
    if atr >= 0.7 and aff >= 0.5:
        return "dating" if fam >= 0.6 else "crush"
    if aff >= 0.6 and fam >= 0.5:
        return "close"
    if aff >= 0.3 or fam >= 0.3:
        return "friend"
    if fam >= 0.1:
        return "acquaintance"
    return "stranger"


def _key(a: str, b: str) -> str:
    """Ordered-pair key — the lexicographically smaller id comes first."""
    return f"{a}__{b}" if a <= b else f"{b}__{a}"


class RelationshipStore:
    """JSON-backed relationship table. Single file, mutated under a lock."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict) -> None:
        self.path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )

    def get(self, a: str, b: str) -> dict:
        rec = self._read().get(_key(a, b))
        if not rec:
            return {axis: 0.0 for axis in AXES} | {"status": "stranger", "a": a, "b": b}
        return rec

    def update(self, a: str, b: str, deltas: dict) -> dict:
        with self._lock:
            data = self._read()
            key = _key(a, b)
            rec = data.get(key) or {axis: 0.0 for axis in AXES}
            rec["a"], rec["b"] = sorted([a, b])
            for axis in AXES:
                if axis in deltas:
                    rec[axis] = max(-1.0, min(1.0, float(rec.get(axis, 0.0)) + float(deltas[axis])))
            rec["status"] = status_from_axes(rec)
            rec["updated_at"] = time.time()
            data[key] = rec
            self._write(data)
            return rec

    def all(self) -> list[dict]:
        return list(self._read().values())


class MemoryStore:
    """Append-only JSONL per persona. One event per line."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, persona_id: str) -> Path:
        return self.root / f"{persona_id}.jsonl"

    def add(self, persona_id: str, event: dict) -> None:
        event = {"ts": time.time(), **event}
        with self._path(persona_id).open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def recent(self, persona_id: str, k: int = 5) -> list[dict]:
        p = self._path(persona_id)
        if not p.exists():
            return []
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: list[dict] = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
            if len(out) >= k:
                break
        return out

    def add_for_each(self, persona_ids: Iterable[str], event: dict) -> None:
        for pid in persona_ids:
            self.add(pid, dict(event))
