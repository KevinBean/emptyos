"""Rooms — snippet CRUD (text shortcuts attachable to messages).

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: JSON-backed snippet store + add/remove/list/get + 4 API endpoints.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: no cross-module reach — fully self-contained.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import web_route

if TYPE_CHECKING:
    from .app import RoomsApp  # noqa: F401 — for type hints only


# ─── Bind to RoomsApp class as ───────────────────────────────
#   _snippets_path      = _snippets._snippets_path
#   _load_snippets      = _snippets._load_snippets
#   _save_snippets      = _snippets._save_snippets
#   add_snippet         = _snippets.add_snippet
#   remove_snippet      = _snippets.remove_snippet
#   list_snippets       = _snippets.list_snippets
#   get_snippet         = _snippets.get_snippet
#   api_list_snippets   = _snippets.api_list_snippets
#   api_get_snippet     = _snippets.api_get_snippet
#   api_add_snippet     = _snippets.api_add_snippet
#   api_remove_snippet  = _snippets.api_remove_snippet
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


def _snippets_path(self) -> Path:
    return self.data_dir / "snippets.json"


def _load_snippets(self) -> dict:
    p = self._snippets_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_snippets(self, snippets: dict) -> None:
    try:
        self._snippets_path().write_text(
            json.dumps(snippets, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


async def add_snippet(self, name: str, body: str) -> dict:
    name = (name or "").strip().lower()
    body = (body or "").strip()
    if not name or not body:
        return {"error": "name and body required"}
    # Lowercase, alphanumeric+dash only — keeps slash-recall predictable.
    if not re.match(r"^[a-z0-9][a-z0-9_\-]*$", name):
        return {"error": "name must be lowercase alphanumeric (with - or _)"}
    snippets = self._load_snippets()
    existed = name in snippets
    snippets[name] = {
        "body": body,
        "created": (snippets.get(name, {}).get("created")
                    or datetime.now(timezone.utc).isoformat()),
        "updated": datetime.now(timezone.utc).isoformat(),
        "used_count": snippets.get(name, {}).get("used_count", 0),
    }
    self._save_snippets(snippets)
    return {"ok": True, "name": name, "updated": existed}


async def remove_snippet(self, name: str) -> dict:
    snippets = self._load_snippets()
    if name not in snippets:
        return {"error": "snippet not found"}
    del snippets[name]
    self._save_snippets(snippets)
    return {"ok": True, "name": name}


def list_snippets(self) -> list[dict]:
    snippets = self._load_snippets()
    out = []
    for name, s in snippets.items():
        out.append({
            "name": name,
            "body": s.get("body", ""),
            "created": s.get("created", ""),
            "updated": s.get("updated", ""),
            "used_count": s.get("used_count", 0),
        })
    # Most-used first, then most-recently-updated.
    out.sort(key=lambda s: (-s["used_count"], s["updated"]), reverse=False)
    return out


async def get_snippet(self, name: str) -> dict:
    snippets = self._load_snippets()
    s = snippets.get((name or "").strip().lower())
    if not s:
        return {"error": "not found"}
    # Bump usage counter on retrieval so the listing reflects what's hot.
    s["used_count"] = int(s.get("used_count", 0)) + 1
    self._save_snippets(snippets)
    return {"name": name, "body": s.get("body", ""),
            "used_count": s["used_count"]}


@web_route("GET", "/api/snippets")
async def api_list_snippets(self, request):
    return self.list_snippets()


@web_route("GET", "/api/snippets/{name}")
async def api_get_snippet(self, request):
    return await self.get_snippet(request.path_params["name"])


@web_route("POST", "/api/snippets")
async def api_add_snippet(self, request):
    data = await self.safe_json(request)
    return await self.add_snippet(
        (data.get("name") or "").strip(),
        (data.get("body") or "").strip(),
    )


@web_route("DELETE", "/api/snippets/{name}")
async def api_remove_snippet(self, request):
    return await self.remove_snippet(request.path_params["name"])
