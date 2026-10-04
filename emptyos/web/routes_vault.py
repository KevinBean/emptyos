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

import asyncio
import base64
import binascii
import hashlib
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from emptyos.runtime.atomic_io import atomic_write_bytes

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


_FALLBACK_NOTE_LOCKS: dict[str, asyncio.Lock] = {}


def _note_lock(kernel: Kernel, rel: str):
    """Kernel-wide lock for one note. ``rel`` MUST be vault-relative.

    That is the whole contract, and the only way this route and an app writing
    the same note end up on the same lock object — which is the entire reason
    for going through VaultIndex instead of a local dict. Passing an absolute
    path here is not a formatting slip: it produces a *different, private* lock
    that excludes nobody (it did, until 9728f122).

    Callers do not pre-normalize. ``VaultIndex.note_lock`` already runs
    ``_normalize_rel`` on whatever it is handed, and BaseApp.note_lock relies on
    the same thing — a second normalisation at each call site is what let the
    two drift apart in the first place. The fallback below mirrors that one
    rule so the two branches cannot disagree either.

    The relative path must be derived WITHOUT ``Path.resolve()``: on Windows
    resolve() opens the file, so it would touch a path another writer may be
    mid-``os.replace`` on — the very contention this lock exists to remove,
    needed before the lock can be taken.
    """
    index = kernel.services.get_optional("vault_index")
    if index is not None:
        return index.note_lock(rel)
    # No VaultIndex (unit tests, no-vault mode). Cross-app contention does not
    # exist there, but apply the same normalisation so behaviour is identical.
    key = str(rel).replace("\\", "/").lstrip("/")
    lock = _FALLBACK_NOTE_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _FALLBACK_NOTE_LOCKS[key] = lock
    return lock


def _size_and_hash(full: Path) -> tuple[int, str]:
    """Read a file and return (size, sha256). Blocking — call via to_thread.

    Returns the size rather than the bytes so a 50 MB payload isn't held alive
    past the comparison it was read for.
    """
    data = full.read_bytes()
    return len(data), hashlib.sha256(data).hexdigest()


def _write_bytes_atomic(full: Path, content: bytes) -> str:
    """Write bytes atomically via a sibling temp file; return the readback hash.

    Blocking, and *unboundedly* so: mkdir + write + fsync + rename + a full
    re-read. An fsync of tens of megabytes is seconds of hard block, during
    which nothing else on the event loop runs — not the event bus, not another
    request, not a scheduled job. Callers must reach this through
    ``asyncio.to_thread`` (`.claude/rules/debugging.md` § sync-call-in-async).
    """
    atomic_write_bytes(full, content)
    return hashlib.sha256(full.read_bytes()).hexdigest()


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
        """Read a vault file by relative or absolute path.

        Vault-rooted only — refuses paths that escape the vault root via `..`
        or an out-of-vault absolute path (mirrors /api/vault/file + /write).
        """
        vault = kernel.config.notes_path
        if not vault:
            return JSONResponse({"error": "No vault configured"}, status_code=500)
        candidate = Path(path) if Path(path).is_absolute() else (vault / path)
        try:
            full = candidate.resolve()
            full.relative_to(Path(vault).resolve())  # raises if escape attempt
        except (ValueError, OSError):
            return JSONResponse({"error": "Path outside vault"}, status_code=403)
        if not full.exists():
            return JSONResponse({"error": f"File not found: {path}"}, status_code=404)
        try:
            content = full.read_text(encoding="utf-8")
            # full is resolved + validated inside the vault above, so this
            # relative_to always succeeds (no startswith/casing fallback needed).
            rel = str(full.relative_to(Path(vault).resolve())).replace("\\", "/")
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
            # Return the vault-relative path, never the host-absolute one — a
            # signed-in web user in a hosted edition has no business learning
            # the container's filesystem layout. `relative` kept for callers
            # that read it explicitly.
            return {"path": rel, "relative": rel,
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
        # SVG can carry a <script> that executes when this response is opened
        # as a top-level navigation (e.g. window.open on a vault attachment
        # chip) — same-origin stored XSS. Forcing a download instead of a
        # render neutralizes it; `<img src>` embeds still work fine (a
        # download-disposition resource still rasterizes in <img>/<object>
        # contexts, it just won't execute if navigated to directly).
        headers = {"Content-Disposition": "attachment"} if ext == "svg" else None
        return FileResponse(
            str(full), media_type=media_types.get(ext, "application/octet-stream"), headers=headers
        )

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

    @server.post("/api/vault/write-bytes")
    async def vault_write_bytes(request: Request):
        """Write a binary Vault file with immutable-by-default hash checks.

        JSON body:

        - ``path``: vault-relative (or in-vault absolute) destination
        - ``content_base64``: standard base64 payload
        - ``content_sha256``: required SHA-256 of the decoded bytes
        - ``overwrite``: optional, false by default

        Existing identical bytes are reused. Existing different bytes return
        409 unless overwrite is explicitly true. This is the binary peer of
        ``/api/vault/write`` for evidence attachments and other Vault assets.
        """
        max_bytes = 50 * 1024 * 1024
        # Refuse on the header, before reading. `await request.json()` buffers
        # and parses the WHOLE body first, so checking len(content) afterwards
        # has already cost raw body + parsed str + decoded bytes — roughly 185 MB
        # of peak RSS for a 50 MB attachment, and unbounded for a body that
        # never had to be honest about its size.
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > max_bytes * 2:
            return JSONResponse(
                {"error": "binary Vault write exceeds the 50 MB limit"},
                status_code=413,
            )
        data = await request.json()
        file_path = str(data.get("path") or "")
        encoded = data.get("content_base64")
        claimed_sha256 = str(data.get("content_sha256") or "").lower()
        overwrite = data.get("overwrite") is True
        if not file_path:
            return JSONResponse({"error": "path is required"}, status_code=400)
        if not isinstance(encoded, str):
            return JSONResponse(
                {"error": "content_base64 is required"}, status_code=400
            )
        if not re.fullmatch(r"[0-9a-f]{64}", claimed_sha256):
            return JSONResponse(
                {"error": "content_sha256 must be a lowercase SHA-256 hex digest"},
                status_code=400,
            )
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            return JSONResponse(
                {"error": "content_base64 is not valid base64"}, status_code=400
            )
        if len(content) > max_bytes:
            return JSONResponse(
                {"error": "binary Vault write exceeds the 50 MB limit"},
                status_code=413,
            )
        actual_sha256 = hashlib.sha256(content).hexdigest()
        if actual_sha256 != claimed_sha256:
            return JSONResponse(
                {
                    "error": "content_sha256 does not match decoded bytes",
                    "actual_sha256": actual_sha256,
                },
                status_code=400,
            )

        vault = kernel.config.notes_path
        if not vault:
            return JSONResponse({"error": "No vault configured"}, status_code=500)
        vault_root = Path(vault).resolve()
        candidate = Path(file_path) if Path(file_path).is_absolute() else vault_root / file_path
        # Lock key from pure string normalisation — no filesystem touch, so it
        # is available before the contested path is safe to resolve.
        # Vault-relative, and derived without resolve() — see _note_lock.
        # Normalising the separators is left to VaultIndex, which does it to
        # whatever it is handed; doing it here as well is how this and
        # BaseApp.note_lock drifted onto separate locks once already.
        try:
            lock_key = os.path.relpath(os.path.normpath(str(candidate)), vault_root)
        except ValueError:  # different drive — containment rejects it below
            lock_key = os.path.normpath(str(candidate))
        # Serialize the whole check-and-write on this note. Without it the
        # immutable-by-default guarantee is a TOCTOU, not an invariant: exists()
        # and os.replace are separated by a mkdir, a write and an fsync, so two
        # concurrent posts of DIFFERENT bytes both see "not there", both write,
        # and replace silently last-wins — no 409, no corruption, no contract.
        # This is the kernel-wide per-note lock, so a concurrent writer in any
        # app excludes this one too (CLAUDE.md, vault read-modify-write races).
        # Cross-process is out of scope by design: one vault is one daemon.
        async with _note_lock(kernel, lock_key):
            # Resolve INSIDE the lock. On Windows resolve() opens the file, so
            # doing it while another writer is mid-os.replace raises OSError —
            # which this check would have reported as "Path outside vault", a
            # security verdict for what was only a filesystem race. Separating
            # the two exceptions is not enough on its own: the honest fix is to
            # not resolve a path somebody else is replacing.
            try:
                full = candidate.resolve()
            except OSError as exc:
                return JSONResponse(
                    {"error": f"Could not resolve destination: {exc}"},
                    status_code=503,
                )
            try:
                full.relative_to(vault_root)
            except ValueError:
                return JSONResponse({"error": "Path outside vault"}, status_code=403)

            if full.exists():
                if not full.is_file():
                    return JSONResponse(
                        {"error": f"Destination is not a file: {file_path}"},
                        status_code=409,
                    )
                existing_size, existing_sha256 = await asyncio.to_thread(_size_and_hash, full)
                if existing_sha256 == actual_sha256:
                    rel = str(full.relative_to(vault_root)).replace("\\", "/")
                    return {
                        "ok": True,
                        "status": "reused",
                        "path": str(full),
                        "relative": rel,
                        "size": existing_size,
                        "content_sha256": existing_sha256,
                    }
                if not overwrite:
                    return JSONResponse(
                        {
                            "error": "Immutable binary conflict",
                            "existing_sha256": existing_sha256,
                        },
                        status_code=409,
                    )

            try:
                readback_sha256 = await asyncio.to_thread(_write_bytes_atomic, full, content)
                if readback_sha256 != actual_sha256:
                    return JSONResponse(
                        {"error": "Binary Vault write readback failed"}, status_code=500
                    )
                await kernel.events.emit(
                    "vault:edited", {"path": str(full)}, source="web"
                )
                rel = str(full.relative_to(vault_root)).replace("\\", "/")
                return {
                    "ok": True,
                    "status": "overwritten" if overwrite else "written",
                    "path": str(full),
                    "relative": rel,
                    "size": len(content),
                    "content_sha256": readback_sha256,
                }
            except Exception as error:
                return JSONResponse({"error": str(error)}, status_code=500)


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
