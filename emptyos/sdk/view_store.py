"""Saved views — per-collection UI state snapshots.

A "view" captures the transient toolbar state a user curated (filter, search,
view type, sort, grouping, column visibility) so they can recall it by name.

Generic by design: any app that builds a multi-view collection (boards
today; tasks/projects tomorrow) can persist saved views here. Stored as
JSON under ``data/apps/<owner>/views/<collection_id>/`` — views are
per-machine UI config, not user knowledge, so they live in ``data/`` not the
vault (CLAUDE.md storage rule).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from emptyos.sdk.utils import slugify

# Stable across all known view-rich apps. Apps that need extra keys can
# subclass and extend ``ALLOWED_KEYS``; unknown keys are dropped on save.
ALLOWED_KEYS: set[str] = {
    "id",
    "name",
    "description",
    "view_type",
    "sort_col",
    "sort_desc",
    "group_by",
    "filters",  # column-level filter array [{col_id, op, value}, ...]
    "search",
    "person_filter",
    "visible_columns",
    "hidden_columns",
    "kanban_group_by",
    "created_at",
    "updated_at",
}


class ViewStore:
    """CRUD for saved views. One directory per collection (board / list / etc)."""

    # Subclasses may override or extend.
    ALLOWED_KEYS: set[str] = ALLOWED_KEYS

    def __init__(self, root: Path):
        self._root = Path(root)

    def _collection_dir(self, collection_id: str) -> Path:
        d = self._root / collection_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def list(self, collection_id: str) -> list[dict]:
        d = self._collection_dir(collection_id)
        out: list[dict] = []
        for f in sorted(d.glob("*.json")):
            try:
                v = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(v, dict):
                    v.setdefault("id", f.stem)
                    out.append(v)
            except Exception:
                continue
        out.sort(key=lambda v: (v.get("id") != "default", v.get("name", "").lower()))
        return out

    def get(self, collection_id: str, view_id: str) -> dict | None:
        target = self._collection_dir(collection_id) / f"{view_id}.json"
        if not target.exists():
            return None
        try:
            v = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(v, dict):
                v.setdefault("id", view_id)
                return v
        except Exception:
            return None
        return None

    def save(self, collection_id: str, view: dict) -> dict:
        clean = {k: v for k, v in (view or {}).items() if k in self.ALLOWED_KEYS}
        vid = clean.get("id") or (slugify(clean.get("name", "")) or "view")
        clean["id"] = vid
        clean.setdefault("name", vid.replace("-", " ").title())
        now = datetime.now().isoformat(timespec="seconds")
        clean.setdefault("created_at", now)
        clean["updated_at"] = now

        target = self._collection_dir(collection_id) / f"{vid}.json"
        target.write_text(
            json.dumps(clean, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return clean

    def delete(self, collection_id: str, view_id: str) -> bool:
        target = self._collection_dir(collection_id) / f"{view_id}.json"
        if target.exists():
            target.unlink()
            return True
        return False
