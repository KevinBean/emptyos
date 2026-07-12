"""EmptyOS web — vault file/query/map HTTP API routes.

Extracted verbatim from ``emptyos/web/server.py`` to keep the server spine
atomic (P4 Atomic, CLAUDE.md rule 4). Owns: /api/vault/{reconcile,enrich,
query} (VaultIndex reads + the fail-closed MCP-foundry tag allowlist gate),
/api/vault-map{,/rescan} (vault-map read/heal), and /api/vault/{read,file,
write} (raw vault note/file access with vault-root containment). Also owns
the pure allowlist predicates ``_vault_query_restricted`` /
``_vault_query_visible`` (unit-tested in tests/test_unit_vault_query_gate.py;
server.py re-exports them for backwards-compatible imports).

Both register functions are called by ``create_server`` at the source
positions the inline decorators previously occupied. All paths here are
static and distinct, so relative route order carries no matching semantics.

Do not import from ``emptyos.web.server`` (it imports us — that would cycle).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

if TYPE_CHECKING:
    from emptyos.kernel import Kernel


def register_vault_query_routes(server: FastAPI, kernel: Kernel) -> None:
    """/api/vault/{reconcile,enrich,query} (moved verbatim from create_server)."""
    @server.get("/api/vault/reconcile")
    async def vault_reconcile(folder: str = "", tags: str = "", fields: str = ""):
        """Check vault notes against expected data structure. Read-only."""
        vi = kernel.services.get("vault_index")
        if not vi:
            return {"error": "VaultIndex not available"}
        if not folder:
            return {"error": "folder parameter required (e.g. ?folder=30_Resources/Books)"}
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
        field_list = [f.strip() for f in fields.split(",") if f.strip()] if fields else None
        return vi.reconcile(folder, tag_list, field_list)

    @server.post("/api/vault/enrich")
    async def vault_enrich(request: Request):
        """Add missing tags/defaults to vault notes. Only adds, never overwrites."""
        vi = kernel.services.get("vault_index")
        if not vi:
            return {"error": "VaultIndex not available"}
        data = await request.json()
        paths = data.get("paths", [])
        add_tags = data.get("tags", [])
        defaults = data.get("defaults", {})
        if not paths:
            return {"error": "paths required"}
        modified = 0
        for path in paths:
            if vi.enrich(path, add_tags or None, defaults or None):
                modified += 1
        return {"ok": True, "modified": modified, "total": len(paths)}

    @server.get("/api/vault/query")
    async def vault_query(tags: str = "", folder: str = "", limit: int = 200):
        """Query the vault index by tags/folder. Read-only, FRONTMATTER ONLY —
        never returns note bodies (CLAUDE.md rule 19). Behind the existing
        auth_token network gate.

        Backs the outbound MCP foundry's ``eos://vault/query`` resource. The
        ``[apps.agent] feature.mcp-foundry.vault_tags_allow`` allowlist gates
        which tags an external agent may ever surface: a query is answered only
        if the allowlist is non-empty AND the query names at least one tag AND
        every requested tag is on the allowlist. Anything else returns
        ``{"count": 0, "notes": [], "restricted": true}``.

        **An empty/unset allowlist therefore returns nothing at all** — this
        endpoint fails CLOSED, so an unconfigured deployment can never expose
        the whole vault (pinned by
        ``tests/test_unit_vault_query_gate.py::test_empty_allowlist_fails_closed``).
        Opt tags in explicitly to use it. The allowlist exists to keep sensitive
        notes (salary, coordinates, contacts) invisible to a connected agent
        unless their tag is opted in.
        """
        vi = kernel.services.get_optional("vault_index")
        if not vi:
            return JSONResponse({"error": "VaultIndex not available"}, status_code=503)
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        fld = folder.strip() or None
        try:
            limit = max(1, min(int(limit), 500))
        except (TypeError, ValueError):
            limit = 200

        allow = kernel.config.get("apps.agent.feature.mcp-foundry.vault_tags_allow", []) or []
        if isinstance(allow, str):
            allow = [t.strip() for t in allow.split(",") if t.strip()]
        # Fail CLOSED via the shared pure predicates (unit-tested): refuse
        # outright unless the allowlist opts the query in, and drop any matched
        # note carrying a non-allowlisted tag so a sensitive sibling can't leak.
        if _vault_query_restricted(tag_list, allow):
            return {"count": 0, "notes": [], "restricted": True}

        rows = vi.find(tags=tag_list or None, folder=fld)
        rows = [r for r in rows if _vault_query_visible(r.get("tags", []), allow)]
        rows = sorted(rows, key=lambda r: r.get("modified", 0) or 0, reverse=True)[:limit]
        notes = []
        for r in rows:
            path = r.get("path", "")
            notes.append({
                "path": path,
                "name": r.get("name") or (path.rsplit("/", 1)[-1] if path else ""),
                "folder": r.get("folder", ""),
                "tags": r.get("tags", []),
                "frontmatter": r.get("properties", {}),
                "sections": r.get("sections", []),
                "modified": r.get("modified"),
            })
        return {"count": len(notes), "notes": notes}


def register_vault_file_routes(server: FastAPI, kernel: Kernel) -> None:
    """/api/vault-map* + /api/vault/{read,file,write} (moved verbatim)."""
    # --- Vault Map API ---
    @server.get("/api/vault-map")
    async def vault_map_get():
        return kernel.vault_map.all()

    @server.post("/api/vault-map")
    async def vault_map_set(request: Request):
        data = await request.json()
        app_id = data.get("app", "")
        key = data.get("key", "")
        value = data.get("value", "")
        if not app_id or not key:
            return JSONResponse({"error": "app and key required"}, status_code=400)
        kernel.vault_map.set(app_id, key, value)
        return {"ok": True, "app": app_id, "key": key, "value": value}

    @server.post("/api/vault-map/rescan")
    async def vault_map_rescan():
        """Rescan vault structure, heal broken paths, detect new patterns."""
        changes = kernel.vault_map.rescan()
        return {"changes": changes, "map": kernel.vault_map.all()}

    # --- Vault file API (read/write any vault note) ---
    @server.get("/api/vault/read")
    async def vault_read(path: str):
        """Read a vault file by relative or absolute path."""
        vault = kernel.config.notes_path
        if not vault:
            return JSONResponse({"error": "No vault configured"}, status_code=500)
        full = Path(path) if Path(path).is_absolute() else vault / path
        if not full.exists():
            return JSONResponse({"error": f"File not found: {path}"}, status_code=404)
        try:
            content = full.read_text(encoding="utf-8")
            rel = (
                str(full.relative_to(vault)).replace("\\", "/")
                if str(full).startswith(str(vault))
                else path.replace("\\", "/")
            )
            # Surface decoded viz_embeds so the universal note viewer can render
            # embedded artifacts (EOS_UI.renderMarkdownWithEmbeds). Best-effort —
            # absent / malformed → []. See viz/embeds.py + the artifact-embed plan.
            viz_embeds = []
            try:
                vi = kernel.services.get_optional("vault_index")
                if vi:
                    raw = (vi.get_properties(rel) or {}).get("viz_embeds")
                    from emptyos.sdk.base_app import BaseApp as _BA
                    viz_embeds = _BA.vault_decode_json(raw, []) or []
            except Exception:
                viz_embeds = []
            return {"path": str(full).replace("\\", "/"), "relative": rel,
                    "content": content, "viz_embeds": viz_embeds}
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @server.get("/api/vault/file")
    async def vault_file(path: str):
        """Serve a vault file as bytes with content-type by extension.

        For `<img src>` / `<object>` / `<video>` embeds of vault attachments
        (PNG, SVG, JPG, MP4, PDF, …). Vault-rooted only — refuses paths that
        escape the vault root via `..`.
        """
        vault = kernel.config.notes_path
        if not vault:
            return JSONResponse({"error": "No vault configured"}, status_code=500)
        candidate = Path(path) if Path(path).is_absolute() else (vault / path)
        try:
            full = candidate.resolve()
            vault_resolved = Path(vault).resolve()
            full.relative_to(vault_resolved)  # raises if escape attempt
        except (ValueError, OSError):
            return JSONResponse({"error": "Path outside vault"}, status_code=403)
        if not full.exists() or not full.is_file():
            return JSONResponse({"error": f"File not found: {path}"}, status_code=404)
        ext = full.suffix.lower().lstrip(".")
        media_types = {
            "svg": "image/svg+xml",
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "gif": "image/gif",
            "webp": "image/webp",
            "avif": "image/avif",
            "bmp": "image/bmp",
            "ico": "image/x-icon",
            "mp4": "video/mp4",
            "webm": "video/webm",
            "mov": "video/quicktime",
            "mp3": "audio/mpeg",
            "wav": "audio/wav",
            "ogg": "audio/ogg",
            "pdf": "application/pdf",
        }
        return FileResponse(str(full), media_type=media_types.get(ext, "application/octet-stream"))

    @server.post("/api/vault/write")
    async def vault_write(request: Request):
        """Write/update a vault file."""
        data = await request.json()
        file_path = data.get("path", "")
        content = data.get("content", "")
        if not file_path:
            return JSONResponse({"error": "path is required"}, status_code=400)
        vault = kernel.config.notes_path
        if not vault:
            return JSONResponse({"error": "No vault configured"}, status_code=500)
        full = Path(file_path) if Path(file_path).is_absolute() else vault / file_path
        # Safety: must be inside vault
        try:
            full.resolve().relative_to(vault.resolve())
        except ValueError:
            return JSONResponse({"error": "Path outside vault"}, status_code=403)
        try:
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")
            await kernel.events.emit("vault:edited", {"path": str(full)}, source="web")
            return {"ok": True, "path": str(full)}
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)


def _vault_query_restricted(tag_list: list[str], allow: list[str]) -> bool:
    """Fail-closed gate for the MCP-foundry ``eos://vault/query`` resource:
    return True (refuse, return nothing) unless the allowlist is non-empty AND
    the query names tags AND every requested tag is opted in. An unset/empty
    allowlist must never expose the whole vault. Pure — unit-tested."""
    allow_set = set(allow or [])
    return (not allow_set) or (not tag_list) or any(t not in allow_set for t in tag_list)


def _vault_query_visible(note_tags, allow) -> bool:
    """True if a matched note may be returned: every tag it carries is on the
    allowlist, so a sensitive sibling tag (e.g. [kb, salary] when only 'kb' is
    opted in) cannot leak its frontmatter via a co-tag match. Pure."""
    return set(note_tags or []).issubset(set(allow or []))
