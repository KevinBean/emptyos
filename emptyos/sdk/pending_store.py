"""Per-app keyed JSON store for review-gate / pending-action lifecycles.

One file per entry under ``app.data_dir/<subdir>/<id>.json``. Used today by:

  - ``apps/rooms`` pending [DO:] action gate (room_review_gate pattern)
  - ``apps/haitao`` match-suggest pending matches

The shape is deliberately minimal — entries are arbitrary dicts the caller
owns, this helper only handles file paths, serialization, and listing.
Callers do their own status transitions (`pending` → `applied` / `rejected`)
by mutating the dict and re-saving.

Promoted from app-local helpers to SDK on the second consumer (CLAUDE.md
§Development Rules 9). Rooms still maintains its own local copy in
``apps/rooms/pending.py`` because the surrounding code (sandbox capture,
write-note diff preview, undo log) is tightly coupled — migrate that
later if a third consumer arrives or rooms touches that code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from emptyos.sdk.base_app import BaseApp


class PendingStore:
    """Keyed JSON file store rooted at ``app.data_dir/<subdir>/``."""

    def __init__(self, app: "BaseApp", subdir: str = "pending") -> None:
        self._app = app
        self._subdir = subdir

    @property
    def dir(self) -> Path:
        d = self._app.data_dir / self._subdir
        d.mkdir(parents=True, exist_ok=True)
        return d

    def path(self, entry_id: str) -> Path:
        return self.dir / f"{entry_id}.json"

    def save(self, entry: dict) -> None:
        if not isinstance(entry, dict) or not entry.get("id"):
            raise ValueError("entry must be a dict with non-empty 'id'")
        self.path(entry["id"]).write_text(
            json.dumps(entry, indent=2, ensure_ascii=False), encoding="utf-8",
        )

    def load(self, entry_id: str) -> dict | None:
        p = self.path(entry_id)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def all(self, *, sort_by: str = "ts", reverse: bool = True) -> list[dict]:
        """Return every entry. Bad-JSON files are skipped silently."""
        out: list[dict] = []
        for f in sorted(self.dir.glob("*.json")):
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue
        out.sort(key=lambda x: x.get(sort_by, ""), reverse=reverse)
        return out

    def delete(self, entry_id: str) -> bool:
        p = self.path(entry_id)
        if not p.exists():
            return False
        p.unlink()
        return True
