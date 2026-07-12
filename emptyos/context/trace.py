"""Context Packing — local observability traces.

One JSONL record per packed call, daily-rotated under
``<store_root>/traces/pack-<YYYYMMDD>.jsonl``. Uses the fail-soft append idiom
from ``apps/public/standard/agent/routes.py`` (audit_log_hook): a trace must
NEVER raise into a packing path. The trace is the source of truth for packing
stats; ``think:executed`` can surface them later but doesn't own them.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

TRACES_DIR = "traces"


def _trace_path(store_root: Path) -> Path:
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    d = Path(store_root) / TRACES_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d / f"pack-{day}.jsonl"


def append_trace(store_root: Path | None, record: dict) -> None:
    """Append one trace record. Fail-soft — swallows every error."""
    if store_root is None:
        return
    try:
        entry = {"ts": datetime.now(timezone.utc).isoformat(), **record}
        with _trace_path(store_root).open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def trace_fail_open(store_root: Path | None, exc: Exception, *, where: str = "pack") -> None:
    """Record that packing failed open and returned the original input."""
    append_trace(
        store_root,
        {"fail_open": True, "where": where, "error": str(exc)[:300]},
    )
