"""Episodic memory — agents remember their own past sessions.

Closes the write-only gap in ``agent_loop._archive_compacted`` (the
``compaction-archive/<session>.jsonl`` files that *nothing read*). An **episode**
is a distilled one-paragraph digest of a finished session — task + outcome + key
decisions — appended to a per-app JSONL log so a worker can rank its *own*
history by similarity + recency (via ``BaseApp.recall_episodes``, which reuses
``BaseApp.recall``).

Two stores, one module:
  • ``EpisodicStore``          — the distilled episode log (the primary recall surface).
  • ``read_compaction_archive`` — the missing *reader* for the raw elided tool_result
    originals ``agent_loop`` already writes, so they become recoverable.

Pure: stdlib only, no kernel, no I/O beyond the given ``data_dir``. **Fail-soft** —
a memory read or write must never break the agent that called it. This is agent
telemetry (high-frequency operational bookkeeping), so it lives under ``data/``,
never the vault (CLAUDE.md § Storage & Vault).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(session_id: Any) -> str:
    """Match the sanitization ``agent_loop._archive_compacted`` uses for filenames."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in str(session_id))


def episode_record(
    *,
    session_id: str,
    task: str,
    outcome: str,
    decisions: "list | tuple" = (),
    salience: float = 0.0,
    app: str = "",
    ts: str | None = None,
) -> dict:
    """Build the canonical, normalized episode dict. One shape, shared by the
    writer and any consumer, so the JSONL layout never drifts.

    ``created``/``updated`` mirror ``ts`` so the record drops straight into
    ``BaseApp.recall(items=...)`` (which reads recency from those keys).
    """
    stamp = ts or _now_iso()
    return {
        "ts": stamp,
        "created": stamp,
        "updated": stamp,
        "session_id": str(session_id),
        "task": str(task or "")[:2000],
        "outcome": str(outcome or "")[:2000],
        "decisions": [str(d)[:500] for d in (decisions or [])][:20],
        "salience": float(salience or 0.0),
        "app": str(app or ""),
    }


class EpisodicStore:
    """Append-only per-app episode log at ``<data_dir>/episodic/episodes.jsonl``.

    One JSON line per finished session-episode. Reads tolerate malformed lines
    (a truncated write from a crashed process is skipped, not fatal).
    """

    def __init__(self, data_dir: str | Path):
        self.dir = Path(data_dir) / "episodic"
        self.path = self.dir / "episodes.jsonl"

    def write_episode(self, episode: dict) -> bool:
        """Append one episode. Returns True on success, False fail-soft.

        Accepts either a raw dict or the output of ``episode_record``; missing
        ``ts``/``created``/``updated`` are backfilled so recency ranking works.
        """
        try:
            rec = dict(episode or {})
            stamp = rec.get("ts") or _now_iso()
            rec.setdefault("ts", stamp)
            rec.setdefault("created", stamp)
            rec.setdefault("updated", stamp)
            self.dir.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            return True
        except Exception:
            return False

    def read_episodes(self, limit: int | None = None) -> list[dict]:
        """Return episodes, most-recent first. ``limit`` caps the candidate set
        to the ``limit`` newest (bounds recall cost). Malformed lines are skipped.
        """
        try:
            if not self.path.exists():
                return []
            out: list[dict] = []
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(rec, dict):
                        out.append(rec)
            out.reverse()  # newest first
            return out[:limit] if limit else out
        except Exception:
            return []


def read_compaction_archive(data_dir: str | Path, session_id: str) -> list[dict]:
    """Read the raw compaction-elided tool_result originals that
    ``agent_loop._archive_compacted`` writes at
    ``<data_dir>/compaction-archive/<session_id>.jsonl``.

    Returns the list of archive records (each ``{ts, session_id, items:[...]}``),
    oldest first. This is the reader the archive's own docstring said didn't
    exist — the raw originals are now recoverable. Fail-soft: returns ``[]`` on
    any error or a missing file.
    """
    try:
        path = Path(data_dir) / "compaction-archive" / f"{_safe_id(session_id)}.jsonl"
        if not path.exists():
            return []
        out: list[dict] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
        return out
    except Exception:
        return []
