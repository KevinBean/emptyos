"""Assistant — vault attachment endpoints.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: the vault-file picker (`/api/vault-files`), the in-chat
image-fetch endpoint (`/api/image`), and the multipart upload endpoint
(`/api/upload`) that lands files in the configured attachments dir.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: none beyond ``self.kernel`` / ``self.vault_config``.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import web_route

if TYPE_CHECKING:
    from .app import AssistantApp  # noqa: F401 — for type hints only


# ─── Bind to AssistantApp class as ──────────────────────────────────
#   api_vault_files = _attachments.api_vault_files
#   api_image       = _attachments.api_image
#   api_upload      = _attachments.api_upload
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────


@web_route("GET", "/api/vault-files")
async def api_vault_files(self, request):
    """Search vault notes for the attachment picker.

    Query params:
      q     — substring match on note name (case-insensitive)
      tag   — tag filter (hierarchical via VaultIndex)
      limit — cap results (default 50)
    """
    q = (request.query_params.get("q") or "").strip().lower()
    tag = (request.query_params.get("tag") or "").strip()
    try:
        limit = max(1, min(200, int(request.query_params.get("limit") or 50)))
    except ValueError:
        limit = 50

    vi = self.kernel.services.get_optional("vault_index")
    if not vi:
        return {"files": []}

    entries = vi.find(tags=[tag] if tag else None)
    if q:
        entries = [
            e
            for e in entries
            if q in (e.get("name") or "").lower() or q in (e.get("path") or "").lower()
        ]
    entries.sort(key=lambda e: e.get("modified", 0), reverse=True)
    files = [
        {
            "path": e.get("path", ""),
            "name": e.get("name", ""),
            "folder": e.get("folder", ""),
            "tags": list(e.get("tags", []))[:6],
            "modified": e.get("modified", 0),
        }
        for e in entries[:limit]
    ]
    return {"files": files, "total": len(entries)}


@web_route("GET", "/api/image")
async def api_image(self, request):
    """Serve a vault-resident image as raw bytes for in-chat thumbnails.

    Accepts ``?path=<vault-relative>``. Refuses non-images and any path
    that escapes the configured vault root — keeps this endpoint from
    being a generic file-read backdoor.
    """
    from starlette.responses import FileResponse, JSONResponse

    rel = (request.query_params.get("path") or "").strip()
    if not rel:
        return JSONResponse({"error": "path required"}, status_code=400)
    vault_root = self.kernel.config.notes_path
    if not vault_root:
        return JSONResponse({"error": "no vault configured"}, status_code=500)
    try:
        full = (Path(vault_root) / rel).resolve()
        vroot = Path(vault_root).resolve()
        full.relative_to(vroot)  # raises if `full` escapes the vault
    except (ValueError, OSError):
        return JSONResponse({"error": "path outside vault"}, status_code=400)
    if not full.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    import mimetypes as _mt

    mime, _ = _mt.guess_type(str(full))
    if not mime or not mime.startswith("image/"):
        return JSONResponse({"error": "not an image"}, status_code=415)
    return FileResponse(str(full), media_type=mime)


@web_route("POST", "/api/upload")
async def api_upload(self, request):
    """Accept a file upload, save to vault inbox attachments, return its vault path.

    Pattern mirrors apps/reports/app.py api_upload_figure: starlette form parsing.
    """
    import re

    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        return {"error": "no file in 'file' form field"}

    max_mb = int(self.app_config("upload_max_mb", 50))
    data = await upload.read()
    if not data:
        return {"error": "empty file"}
    if len(data) > max_mb * 1024 * 1024:
        return {"error": f"file too large ({len(data) // (1024 * 1024)}MB > {max_mb}MB cap)"}

    vault_root = self.kernel.config.notes_path
    if not vault_root:
        return {"error": "no vault configured"}

    rel_dir = self.vault_config("attachments", "00_Inbox/_attachments")
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    raw_name = upload.filename or "upload.bin"
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", raw_name).strip("-") or "upload.bin"
    rel_path = f"{rel_dir}/{ts}-{safe_name}"

    abs_path = Path(vault_root) / rel_path
    try:
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(data)
    except Exception as e:
        return {"error": f"write failed: {e}"}

    # Re-index if it's markdown so VaultIndex picks it up immediately.
    if rel_path.endswith(".md"):
        vi = self.kernel.services.get_optional("vault_index")
        if vi:
            try:
                vi.index_file(rel_path)
            except Exception:
                pass

    return {
        "path": rel_path,
        "name": raw_name,
        "size": len(data),
        "mime": getattr(upload, "content_type", "") or "",
    }
