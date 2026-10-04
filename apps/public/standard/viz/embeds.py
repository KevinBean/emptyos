"""viz — durable artifact embedding into notes (snapshot + reference).

Extracted from app.py to keep the spine atomic (CLAUDE.md rule 4). Owns the
*artifact* side of the note-embed feature — the note side (writing the marker +
frontmatter into a note) is ``BaseApp.embed_viz_into_note``, which KB / note /
journal all call. This module owns:

  - ``bake_embed`` — prepare an existing artifact for embedding. Snapshot mode
    copies the current scene.html into a flat, rename-proof sidecar
    (``viz/embeds/<embed_id>.html``) so the source artifact stays freely
    deletable; reference mode copies nothing and the note renders the live
    artifact (and viz gates its deletion).
  - ``api_embed_serve`` — serve a snapshot sidecar by id (path-guarded).
  - ``_referencing_notes`` — the reverse index, computed fresh from the vault
    at delete time (never a cached index — see .claude/rules/three-natures-lens.md),
    backing the delete gate wired into ``routes.api_delete``.
  - ``api_embed_resync_*`` — re-sync a snapshot from its (possibly-changed)
    source via SandboxedWrite (propose/preview/confirm; never autopilot-eligible).

Dark flag ``[apps.viz] feature.artifact-embed.enabled`` (default off). When off,
``bake_embed`` and every endpoint short-circuit and the delete gate is a no-op,
so behaviour is byte-identical to pre-feature.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._record_dir / self._rel_record (generation).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from emptyos.sdk import web_route
from emptyos.sdk.html_artifact import new_artifact_id as _new_id, now_iso as _now_iso
from emptyos.sdk.viz_embed import embed_marker

if TYPE_CHECKING:
    from .app import VizApp  # noqa: F401 — for type hints only


# ─── Bind to VizApp class as ──────────────────────────────────────────
#   _embed_enabled            = _embeds._embed_enabled
#   _embeds_root              = _embeds._embeds_root
#   _embed_sidecar            = _embeds._embed_sidecar
#   _embed_sidecar_rel        = _embeds._embed_sidecar_rel
#   _resync_root              = _embeds._resync_root
#   bake_embed                = _embeds.bake_embed
#   _referencing_notes        = _embeds._referencing_notes
#   api_embed_bake            = _embeds.api_embed_bake
#   api_embed_serve           = _embeds.api_embed_serve
#   api_embed_refs            = _embeds.api_embed_refs
#   api_embed_resync_propose  = _embeds.api_embed_resync_propose
#   api_embed_resync_apply    = _embeds.api_embed_resync_apply
# Adding a new method here? Add a matching binding line in app.py.
# ──────────────────────────────────────────────────────────────────────


# Heavy shapes draw to <canvas> / pull large libs — the note view renders them
# as a click-to-load poster rather than an eager iframe. Mirrors the canvas
# exclusion set in SHAPE_SUPPORTS_ELEMENT_EDIT (shared.py).
HEAVY_EMBED_SHAPES = frozenset({"3d-scene", "game-2d", "network-graph", "anim-explainer"})


def _embed_enabled(self) -> bool:
    """Dark default (project_feature_pipeline_flag_default_dark)."""
    return bool(self.app_config("feature.artifact-embed.enabled", False))


def _embeds_root(self):
    rel = self.vault_config("path", "30_Resources/EmptyOS/viz") + "/embeds"
    return self.vault_root / rel


def _embed_sidecar(self, embed_id: str):
    return self._embeds_root() / f"{embed_id}.html"


def _embed_sidecar_rel(self, embed_id: str) -> str:
    base = self.vault_config("path", "30_Resources/EmptyOS/viz")
    return f"{base}/embeds/{embed_id}.html"


def _resync_root(self):
    return self.data_dir / "embed-resync"


async def bake_embed(self, viz_id: str = "", mode: str = "snapshot", height=360) -> dict:
    """Prepare an embed of an existing artifact. Returns the embed record + the
    body marker for the caller to write. Does NOT touch any note.

    Snapshot: copies the current scene.html into a frozen sidecar — the note
    owns its bytes and the source artifact stays freely deletable. Reference:
    no copy — the note renders the live artifact (``/viz/api/html/<id>``) and
    viz gates the source's deletion. Public verb — called via
    ``call_app("viz", "bake_embed", ...)`` from BaseApp.embed_viz_into_note.
    """
    if not self._embed_enabled():
        return {"ok": False, "error": "artifact embed is disabled"}
    viz_id = (viz_id or "").strip()
    mode = mode if mode in ("snapshot", "reference") else "snapshot"
    if not viz_id:
        return {"ok": False, "error": "viz_id is required"}

    src_html = self._record_dir(viz_id) / "scene.html"
    if not src_html.exists():
        return {"ok": False, "error": f"no artifact with id '{viz_id}'"}

    fm = self.vault_get_properties(self._rel_record(viz_id)) or {}
    shape = fm.get("shape", "")
    heavy = shape in HEAVY_EMBED_SHAPES

    if mode == "snapshot":
        embed_id = _new_id()
        try:
            content = src_html.read_text(encoding="utf-8")
        except Exception:
            return {"ok": False, "error": "source artifact could not be read"}
        sidecar = self._embed_sidecar(embed_id)
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(content, encoding="utf-8")
    else:  # reference — key by the source id so a re-reference is idempotent
        embed_id = viz_id

    try:
        h = int(height or 360)
    except (TypeError, ValueError):
        h = 360

    return {
        "ok": True,
        "embed_id": embed_id,
        "mode": mode,
        "source_viz_id": viz_id,
        "shape": shape,
        "heavy": heavy,
        "height": h,
        "ts": _now_iso(),
        "marker": embed_marker(embed_id, mode=mode, shape=shape),
    }


def _referencing_notes(self, source_viz_id: str) -> list[str]:
    """Vault-relative paths of notes holding a *reference*-mode embed of this
    artifact. Computed fresh from the vault (the index matches by equality, not
    list membership) so the delete gate can't trust a stale cache."""
    source_viz_id = (source_viz_id or "").strip()
    if not source_viz_id:
        return []
    out: list[str] = []
    for e in self.vault_query():  # all indexed notes
        refs = (e.get("properties", {}) or {}).get("viz_ref_ids")
        if isinstance(refs, str):
            refs = [refs]
        if refs and source_viz_id in refs:
            p = e.get("path", "")
            if p:
                out.append(p)
    return out


@web_route("POST", "/api/embed/bake")
async def api_embed_bake(self, request) -> dict:
    body = await request.json()
    return await self.bake_embed(
        viz_id=(body.get("viz_id") or "").strip(),
        mode=body.get("mode") or "snapshot",
        height=body.get("height") or 360,
    )


@web_route("GET", "/api/embed/{embed_id}")
async def api_embed_serve(self, request):
    """Serve a snapshot sidecar for iframe consumption. Path-containment guarded
    so a crafted id can't escape the embeds dir."""
    from fastapi.responses import FileResponse, JSONResponse

    embed_id = request.path_params.get("embed_id", "")
    sidecar = self._embed_sidecar(embed_id)
    try:
        root = self._embeds_root().resolve()
        resolved = sidecar.resolve()
        if root != resolved.parent:
            return JSONResponse({"ok": False, "error": "invalid id"}, status_code=404)
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid id"}, status_code=404)
    if not sidecar.exists():
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    return FileResponse(sidecar, media_type="text/html", headers={"Cache-Control": "no-store"})


@web_route("GET", "/api/embed/refs/{rid}")
async def api_embed_refs(self, request) -> dict:
    """Which notes hold a live (reference) embed of this artifact — drives the
    delete-confirm dialog."""
    rid = request.path_params.get("rid", "")
    notes = self._referencing_notes(rid)
    return {"ok": True, "id": rid, "notes": notes, "count": len(notes)}


@web_route("POST", "/api/embed/resync/propose")
async def api_embed_resync_propose(self, request) -> dict:
    """Propose re-syncing a snapshot from its current source. Returns a diff +
    a token; nothing is written until /apply. Source deleted → unavailable."""
    if not self._embed_enabled():
        return {"ok": False, "error": "artifact embed is disabled"}
    body = await request.json()
    embed_id = (body.get("embed_id") or "").strip()
    source_viz_id = (body.get("source_viz_id") or "").strip()
    if not embed_id:
        return {"ok": False, "error": "embed_id is required"}
    if not self._embed_sidecar(embed_id).exists():
        return {"ok": False, "error": "no snapshot for this embed_id"}

    src_html = self._record_dir(source_viz_id) / "scene.html" if source_viz_id else None
    if not source_viz_id or src_html is None or not src_html.exists():
        return {"ok": False, "reason": "source-deleted",
                "error": "source artifact no longer exists; the embed stays frozen"}

    try:
        new_content = src_html.read_text(encoding="utf-8")
    except Exception:
        return {"ok": False, "error": "source artifact could not be read"}

    from emptyos.sdk.sandbox import SandboxedWrite

    token = f"vizresync-{_new_id()}"
    sw = SandboxedWrite(token, self.vault_root, self._embed_sidecar_rel(embed_id),
                        new_content, self._resync_root())
    sw.capture()
    diff = sw.diff_lines()
    return {"ok": True, "token": token, "diff": diff, "unchanged": not diff}


@web_route("POST", "/api/embed/resync/apply")
async def api_embed_resync_apply(self, request) -> dict:
    """Apply a proposed re-sync by token (staleness-checked)."""
    if not self._embed_enabled():
        return {"ok": False, "error": "artifact embed is disabled"}
    body = await request.json()
    token = (body.get("token") or "").strip()
    if not token:
        return {"ok": False, "error": "token is required"}

    from emptyos.sdk.sandbox import StaleSandbox, load_sandbox

    sw = load_sandbox(token, self._resync_root())
    if sw is None:
        return {"ok": False, "error": "no such resync proposal (expired?)"}
    try:
        sw.apply()
    except StaleSandbox:
        return {"ok": False, "reason": "stale",
                "error": "the snapshot changed since preview — re-propose"}
    sw.discard()
    await self.emit("viz:embed_resynced", {"token": token})
    return {"ok": True}
