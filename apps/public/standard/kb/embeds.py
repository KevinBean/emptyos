"""kb — viz-artifact embed wiring (note side of the artifact-embed feature).

Thin by design: resolve a slug → vault path and delegate the marker +
frontmatter write to ``BaseApp.embed_viz_into_note`` (the viz app owns the bytes,
the reverse-index, and the delete gate — see ``apps/public/standard/viz/embeds.py``).
The note view substitutes the marker for a sandboxed iframe via
``EOS_UI.renderMarkdownWithEmbeds``.

Dark flag ``[apps.kb] feature.artifact-embed.enabled`` (default off).

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._slug_to_path (docs).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from emptyos.sdk import web_route

if TYPE_CHECKING:
    from .app import KBApp  # noqa: F401 — for type hints only


# ─── Bind to KBApp class as ───────────────────────────────────────────
#   _embed_enabled     = _embeds._embed_enabled
#   api_embed_enabled  = _embeds.api_embed_enabled
#   api_embed          = _embeds.api_embed
# Adding a new method here? Add a matching binding line in app.py.
# ──────────────────────────────────────────────────────────────────────


def _embed_enabled(self) -> bool:
    """Dark default (project_feature_pipeline_flag_default_dark)."""
    return bool(self.app_config("feature.artifact-embed.enabled", False))


@web_route("GET", "/api/embed/enabled")
async def api_embed_enabled(self, request) -> dict:
    """Lets the UI show/hide the Embed-viz affordance without a failing click."""
    return {"enabled": self._embed_enabled()}


@web_route("POST", "/api/embed")
async def api_embed(self, request) -> dict:
    """Embed an existing viz artifact into a KB note (snapshot | reference)."""
    if not self._embed_enabled():
        return {"ok": False, "error": "artifact embed is disabled"}
    body = await request.json()
    slug = (body.get("slug") or "").strip()
    viz_id = (body.get("viz_id") or "").strip()
    mode = body.get("mode") or "snapshot"
    height = body.get("height") or 360
    if not slug or not viz_id:
        return {"ok": False, "error": "slug and viz_id are required"}

    path = self._slug_to_path().get(slug)
    if not path:
        return {"ok": False, "error": f"no KB note with slug '{slug}'"}

    res = await self.embed_viz_into_note(path, viz_id=viz_id, mode=mode, height=height)
    if res.get("ok"):
        await self.emit("kb:embed_added", {
            "slug": slug, "embed_id": res.get("embed_id"), "mode": res.get("mode"),
        })
    return res
