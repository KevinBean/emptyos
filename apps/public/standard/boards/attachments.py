"""Boards — per-item file attachments (directory-listing-as-index).

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: attachment bytes under
``{vault}/30_Resources/EmptyOS/boards-data/_attachments/{board_id}/{item_key}/``
and the upload/list/serve/delete routes plus the per-board count index.
The directory listing IS the index — no attachment list is written into item
frontmatter or pushed through set_field, so attachments work for app-sourced
items too and never fight the flat-frontmatter constraint. Bytes live in the
vault (user content, visible in the vault viewer, synced), not data/.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: calls ``self._log_activity`` (bound from
activity.py) through the class.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import mimetypes
import re
from typing import TYPE_CHECKING

from emptyos.sdk import web_route

if TYPE_CHECKING:
    from .app import BoardsApp  # noqa: F401 — for type hints only


# ─── Bind to BoardsApp class as ──────────────────────────────────────
#   _attachments_dir       = _attachments._attachments_dir
#   api_list_attachments   = _attachments.api_list_attachments
#   api_upload_attachment  = _attachments.api_upload_attachment
#   api_serve_attachment   = _attachments.api_serve_attachment
#   api_delete_attachment  = _attachments.api_delete_attachment
#   api_attachments_index  = _attachments.api_attachments_index
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────

_ATTACH_DIR_KEY = "boards_attachments_dir"
_ATTACH_DIR_DEFAULT = "30_Resources/EmptyOS/boards-data/_attachments"


def _safe_segment(raw: str) -> str | None:
    """One path segment from a hostile path param. None = reject the request.

    Collapses anything outside [A-Za-z0-9._-] (kills separators + traversal),
    then refuses empty and dot-leading results.
    """
    seg = re.sub(r"[^A-Za-z0-9._-]+", "-", raw or "")
    seg = seg.strip()
    if not seg or seg.lstrip(".") == "" or seg.startswith("."):
        return None
    return seg


def _attachments_dir(self, board_id: str, item_key: str, *, create: bool = False):
    """Resolved directory for one item's attachments, or None on bad segments."""
    b = _safe_segment(board_id)
    k = _safe_segment(item_key)
    if not b or not k:
        return None
    root = self.vault_config_path(_ATTACH_DIR_KEY, _ATTACH_DIR_DEFAULT)
    if root is None:
        return None
    d = root / b / k
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


@web_route("GET", "/api/boards/{id}/items/{file}/attachments")
async def api_list_attachments(self, request):
    board_id = request.path_params.get("id", "")
    item_key = request.path_params.get("file", "")
    d = self._attachments_dir(board_id, item_key)
    if d is None or not d.exists():
        return {"attachments": []}
    out = []
    for p in sorted(d.iterdir()):
        if not p.is_file():
            continue
        st = p.stat()
        out.append(
            {
                "name": p.name,
                "size": st.st_size,
                "mtime": int(st.st_mtime),
                "url": f"/boards/api/boards/{board_id}/items/{item_key}/attachments/{p.name}",
            }
        )
    return {"attachments": out}


@web_route("POST", "/api/boards/{id}/items/{file}/attachments")
async def api_upload_attachment(self, request):
    """Multipart upload — field name ``file`` (reports api_upload_figure pattern)."""
    board_id = request.path_params.get("id", "")
    item_key = request.path_params.get("file", "")
    d = self._attachments_dir(board_id, item_key, create=True)
    if d is None:
        return {"error": "invalid board or item key"}

    form = await request.form()
    upload = form.get("file")
    if upload is None or not getattr(upload, "filename", ""):
        return {"error": "no file uploaded"}

    safe = _safe_segment(upload.filename)
    if not safe:
        return {"error": "invalid filename"}

    data = await upload.read()
    max_mb = float(self.app_config("max_attachment_mb", 20))
    if len(data) > max_mb * 1024 * 1024:
        return {"error": f"file too large (max {max_mb:g} MB)"}
    if not data:
        return {"error": "empty file"}

    # Dedupe by suffixing -2, -3, … before the extension.
    target = d / safe
    if target.exists():
        stem, dot, ext = safe.partition(".")
        n = 2
        while target.exists():
            target = d / (f"{stem}-{n}{dot}{ext}" if dot else f"{stem}-{n}")
            n += 1
    target.write_bytes(data)

    self._log_activity(
        board_id, item_key, "board:attachment_added", name=target.name, size=len(data)
    )
    await self.emit(
        "board:attachment_added",
        {"board": board_id, "file": item_key, "name": target.name, "size": len(data)},
    )
    return {"ok": True, "name": target.name, "size": len(data)}


@web_route("GET", "/api/boards/{id}/items/{file}/attachments/{name}")
async def api_serve_attachment(self, request):
    board_id = request.path_params.get("id", "")
    item_key = request.path_params.get("file", "")
    name = _safe_segment(request.path_params.get("name", ""))
    d = self._attachments_dir(board_id, item_key)
    if d is None or not name:
        return {"error": "invalid path"}
    p = d / name
    if not p.is_file():
        return {"error": "not found"}
    from starlette.responses import FileResponse

    media, _ = mimetypes.guess_type(name)
    return FileResponse(str(p), media_type=media or "application/octet-stream", filename=name)


@web_route("DELETE", "/api/boards/{id}/items/{file}/attachments/{name}")
async def api_delete_attachment(self, request):
    board_id = request.path_params.get("id", "")
    item_key = request.path_params.get("file", "")
    name = _safe_segment(request.path_params.get("name", ""))
    d = self._attachments_dir(board_id, item_key)
    if d is None or not name:
        return {"error": "invalid path"}
    p = d / name
    if not p.is_file():
        return {"error": "not found"}
    p.unlink()
    self._log_activity(board_id, item_key, "board:attachment_deleted", name=name)
    await self.emit(
        "board:attachment_deleted", {"board": board_id, "file": item_key, "name": name}
    )
    return {"ok": True}


@web_route("GET", "/api/boards/{id}/attachments-index")
async def api_attachments_index(self, request):
    """{item_key: count} in one dir walk — feeds the 📎N column renderer."""
    board_id = request.path_params.get("id", "")
    b = _safe_segment(board_id)
    root = self.vault_config_path(_ATTACH_DIR_KEY, _ATTACH_DIR_DEFAULT)
    if not b or root is None:
        return {"counts": {}}
    board_dir = root / b
    if not board_dir.exists():
        return {"counts": {}}
    counts: dict[str, int] = {}
    for item_dir in board_dir.iterdir():
        if item_dir.is_dir():
            n = sum(1 for p in item_dir.iterdir() if p.is_file())
            if n:
                counts[item_dir.name] = n
    return {"counts": counts}
