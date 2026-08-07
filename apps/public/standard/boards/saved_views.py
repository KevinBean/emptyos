"""boards — per-board saved view CRUD.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: List/save/get/delete of a board's saved views. Named saved_views rather than views because views.py already re-exports the SDK ViewStore this module drives.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ViewStore (from .views, the SDK re-export).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from .views import ViewStore
from typing import TYPE_CHECKING

from emptyos.sdk import web_route

if TYPE_CHECKING:
    from .app import BoardsApp  # noqa: F401 — for type hints only


# ─── Bind to BoardsApp class as ────────────────────────────────
#   api_list_views   = _saved_views.api_list_views
#   api_save_view    = _saved_views.api_save_view
#   api_get_view     = _saved_views.api_get_view
#   api_delete_view  = _saved_views.api_delete_view
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


@web_route("GET", "/api/boards/{id}/views")
async def api_list_views(self, request):
    board_id = request.path_params.get("id", "")
    return {"views": self._views.list(board_id)}


@web_route("POST", "/api/boards/{id}/views")
async def api_save_view(self, request):
    board_id = request.path_params.get("id", "")
    data = await request.json()
    saved = self._views.save(board_id, data)
    await self.emit("board:view_saved", {"board": board_id, "view": saved["id"]})
    return {"ok": True, "view": saved}


@web_route("GET", "/api/boards/{id}/views/{vid}")
async def api_get_view(self, request):
    board_id = request.path_params.get("id", "")
    vid = request.path_params.get("vid", "")
    v = self._views.get(board_id, vid)
    if not v:
        return {"error": "View not found"}
    return v


@web_route("DELETE", "/api/boards/{id}/views/{vid}")
async def api_delete_view(self, request):
    board_id = request.path_params.get("id", "")
    vid = request.path_params.get("vid", "")
    ok = self._views.delete(board_id, vid)
    if ok:
        await self.emit("board:view_deleted", {"board": board_id, "view": vid})
    return {"ok": ok}
