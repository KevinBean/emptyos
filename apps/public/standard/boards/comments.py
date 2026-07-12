"""Boards — per-item comment threads (JSON sidecar store).

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: the comment sidecar store at
``data/apps/boards/comments/{board_id}/{safe_item_key}.json`` and its four
CRUD routes. A sidecar (not vault frontmatter, not a vault note) because it
must work uniformly for vault_tag items AND app-sourced items — the latter
have no boards-owned note — and vault frontmatter is flat-only, which can't
hold a thread. Comments are allowed on readonly (app-sourced) boards:
annotating system records is the feature.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: calls ``self._log_activity`` / ``self._actor_name``
(bound from activity.py) through the class.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from emptyos.sdk import web_route

from .shared import safe_segment as _safe_key

if TYPE_CHECKING:
    from .app import BoardsApp  # noqa: F401 — for type hints only


# ─── Bind to BoardsApp class as ──────────────────────────────────────
#   _comments_path      = _comments._comments_path
#   _load_comments      = _comments._load_comments
#   api_list_comments   = _comments.api_list_comments
#   api_add_comment     = _comments.api_add_comment
#   api_edit_comment    = _comments.api_edit_comment
#   api_delete_comment  = _comments.api_delete_comment
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────


def _comments_path(self, board_id: str, item_key: str):
    return self.data_subdir("comments", _safe_key(board_id)) / f"{_safe_key(item_key)}.json"


def _load_comments(self, board_id: str, item_key: str) -> dict:
    path = self._comments_path(board_id, item_key)
    if path.exists():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and isinstance(doc.get("comments"), list):
                return doc
        except Exception:
            pass
    return {"item": item_key, "comments": []}


def _save_comments(self, board_id: str, item_key: str, doc: dict) -> None:
    path = self._comments_path(board_id, item_key)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


@web_route("GET", "/api/boards/{id}/items/{file}/comments")
async def api_list_comments(self, request):
    board_id = request.path_params.get("id", "")
    item_key = request.path_params.get("file", "")
    return {"comments": self._load_comments(board_id, item_key)["comments"]}


@web_route("POST", "/api/boards/{id}/items/{file}/comments")
async def api_add_comment(self, request):
    board_id = request.path_params.get("id", "")
    item_key = request.path_params.get("file", "")
    data = await request.json()
    text = str(data.get("text") or "").strip()
    if not text:
        return {"error": "empty comment"}
    # Explicit author override kept for imports that preserve original
    # commenters; default is the boards.me setting.
    author = str(data.get("author") or "").strip() or self._actor_name()
    comment = {
        "id": f"c-{uuid.uuid4().hex[:10]}",
        "author": author,
        "text": text,
        "created": datetime.now().isoformat(timespec="seconds"),
        "edited": None,
    }
    async with self.write_lock(f"comments:{board_id}:{item_key}"):
        doc = self._load_comments(board_id, item_key)
        doc["comments"].append(comment)
        _save_comments(self, board_id, item_key, doc)
    self._log_activity(
        board_id, item_key, "board:comment_added", actor=author, comment_id=comment["id"]
    )
    await self.emit(
        "board:comment_added",
        {"board": board_id, "file": item_key, "comment_id": comment["id"], "author": author},
    )
    return {"ok": True, "comment": comment}


@web_route("PATCH", "/api/boards/{id}/items/{file}/comments/{cid}")
async def api_edit_comment(self, request):
    board_id = request.path_params.get("id", "")
    item_key = request.path_params.get("file", "")
    cid = request.path_params.get("cid", "")
    data = await request.json()
    text = str(data.get("text") or "").strip()
    if not text:
        return {"error": "empty comment"}
    async with self.write_lock(f"comments:{board_id}:{item_key}"):
        doc = self._load_comments(board_id, item_key)
        target = next((c for c in doc["comments"] if c.get("id") == cid), None)
        if not target:
            return {"error": "comment not found"}
        target["text"] = text
        target["edited"] = datetime.now().isoformat(timespec="seconds")
        _save_comments(self, board_id, item_key, doc)
    return {"ok": True, "comment": target}


@web_route("DELETE", "/api/boards/{id}/items/{file}/comments/{cid}")
async def api_delete_comment(self, request):
    board_id = request.path_params.get("id", "")
    item_key = request.path_params.get("file", "")
    cid = request.path_params.get("cid", "")
    async with self.write_lock(f"comments:{board_id}:{item_key}"):
        doc = self._load_comments(board_id, item_key)
        before = len(doc["comments"])
        doc["comments"] = [c for c in doc["comments"] if c.get("id") != cid]
        if len(doc["comments"]) == before:
            return {"error": "comment not found"}
        _save_comments(self, board_id, item_key, doc)
    self._log_activity(board_id, item_key, "board:comment_deleted", comment_id=cid)
    await self.emit(
        "board:comment_deleted", {"board": board_id, "file": item_key, "comment_id": cid}
    )
    return {"ok": True}
