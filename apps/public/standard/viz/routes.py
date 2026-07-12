"""viz — CRUD + non-streaming web/CLI surface.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: The read/list/get/html/delete REST surface, the non-streaming generate + iterate endpoints, the shapes+ability endpoint, the timeline entity_source, and the CLI commands..

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self.generate / self._think_html / self._persist (generation); self._record_dir / self._rel_record (generation).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import json
from emptyos.sdk import cli_command, web_route
from .shared import PRESETS, SHAPE_META, SHAPE_SUPPORTS_RECORD, _rewrite_user_msg, _shape_max_tokens
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import VizApp  # noqa: F401 — for type hints only


# ─── Bind to VizApp class as ────────────────────────────────
#   api_shapes           = _routes.api_shapes
#   api_generate         = _routes.api_generate
#   api_iterate          = _routes.api_iterate
#   api_rendered         = _routes.api_rendered
#   list_items           = _routes.list_items
#   api_list             = _routes.api_list
#   api_get              = _routes.api_get
#   api_html             = _routes.api_html
#   api_delete           = _routes.api_delete
#   api_export_mp4       = _routes.api_export_mp4
#   api_video            = _routes.api_video
#   artifact_path        = _routes.artifact_path
#   _html_record_enabled = _routes._html_record_enabled
#   _shape_supports_record = _routes._shape_supports_record
#   _output_review_enabled = _routes._output_review_enabled
#   cli_list             = _routes.cli_list
#   cli_generate         = _routes.cli_generate
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


def _html_record_enabled(self) -> bool:
    """True when HTML→MP4 export is on for this machine.

    Dark default (per project_feature_pipeline_flag_default_dark) — the export
    endpoint + button only act when explicitly flipped in
    `[apps.viz] feature.html-record.enabled`. The html-video recorder borrow.
    """
    return bool(self.app_config("feature.html-record.enabled", False))


def _shape_supports_record(self, shape: str) -> bool:
    return shape in SHAPE_SUPPORTS_RECORD


def _output_review_enabled(self) -> bool:
    """True when the recorded MP4 gets a validity self-check (frozen/black
    frame sampling via emptyos.sdk.media.review). Dark default; same flag name
    as podcast's audio gate."""
    return bool(self.app_config("feature.output-review.enabled", False))


@web_route("GET", "/api/shapes")
async def api_shapes(self, request) -> dict:
    """Shapes + their min_ability + the active model's ability.

    Lets the UI disable shapes the current model can't do well
    (degrade-and-explain). See .claude/rules/model-ability.md.
    """
    info = await self.model_ability(domain=self.app_config("think_domain", "code"))
    shapes = [
        {
            "shape": s,
            "label": SHAPE_META[s]["label"],
            "min_ability": SHAPE_META[s]["min_ability"],
            "supports_edit": self._shape_supports_edit(s),
            "supports_record": self._shape_supports_record(s),
        }
        for s in PRESETS
        if s in SHAPE_META
    ]
    return {
        "shapes": shapes,
        "active_ability": info.get("ability", "standard"),
        "provider": info.get("provider", ""),
        "model": info.get("model", ""),
        "edit_enabled": self._edit_enabled(),
        "record_enabled": self._html_record_enabled(),
        "embed_enabled": self._embed_enabled(),
    }


@web_route("POST", "/api/generate")
async def api_generate(self, request) -> dict:
    body = await request.json()
    return await self.generate(
        body.get("prompt", ""),
        shape=body.get("shape"),
        examples=body.get("examples") or [],
    )


@web_route("POST", "/api/iterate")
async def api_iterate(self, request) -> dict:
    body = await request.json()
    return await self.iterate((body.get("id") or "").strip(), (body.get("prompt") or "").strip())


async def iterate(self, rid: str, prompt: str) -> dict:
    """Whole-file iterate on an existing artifact. Public verb — backs
    api_iterate AND cross-app refine (work app's [[contributes.work.deliverable]]
    `iterate` hook)."""
    if not rid or not prompt:
        return {"ok": False, "error": "id and prompt are required"}

    record_dir = self._record_dir(rid)
    html_path = record_dir / "scene.html"
    if not html_path.exists():
        return {"ok": False, "error": f"no artifact with id '{rid}'"}

    existing = self.vault_get_properties(self._rel_record(rid)) or {}
    shape = existing.get("shape", "3d-scene")
    prior_html = html_path.read_text(encoding="utf-8")
    prior_prompt = existing.get("prompt", "")

    user_msg = _rewrite_user_msg(prior_prompt, prompt, prior_html)

    html = await self._think_html(self._system_for(shape), user_msg, max_tokens=_shape_max_tokens(shape))
    reason = self._reject_reason(html)
    if reason:
        return {"ok": False, "error": reason}

    meta = await self._persist(rid, html, prompt, shape, is_update=True)
    await self.emit("viz:updated", {"id": rid, "shape": shape})
    return {"ok": True, **meta}


def _work_artifact_result(res: dict) -> dict:
    """Shape a viz ``generate`` result into the work-deliverable return contract
    ({id, preview_url, artifact_path, open_url}) — or an {error}. Shared by both
    work deliverable handlers (deliver_work + deliver_work_infographic)."""
    if not res.get("ok"):
        return {"error": res.get("error") or "viz generate failed"}
    rid = res["id"]
    return {
        "id": rid,
        "preview_url": f"/viz/api/html/{rid}",
        "artifact_path": res.get("record_path", ""),
        "open_url": f"/viz/#{rid}",
    }


async def deliver_work(self, brief: dict) -> dict:
    """[[contributes.work.deliverable]] handler — slide-deck from a work brief.

    `brief`: {ask, title, brief, outline: [str], sources: str, run_id}.
    Returns {id, preview_url, artifact_path, open_url} or {error}."""
    brief = brief or {}
    outline = "\n".join(f"- {s}" for s in brief.get("outline") or [])
    prompt = (
        f"Create a slide deck titled: {brief.get('title') or brief.get('ask') or 'Untitled'}\n"
        f"Brief: {brief.get('brief') or ''}\nAsk: {brief.get('ask') or ''}\n"
        f"Sections:\n{outline}\n\nSource material:\n{brief.get('sources') or ''}"
    )
    res = await self.generate(prompt, shape="slide-deck")
    return _work_artifact_result(res)


# An infographic is concept-level: it needs the *ideas*, not the full text of
# every gathered note. Work hands deliverables up to ~12.8 KB of source excerpts
# (`_sources_block`), and feeding all of it to a single svg-diagram makes the
# model attempt a sprawling poster that overruns viz's one-shot token ceiling
# and truncates. Cap hard here so rich grounding can't blow the budget — the
# slide-deck / report deliverables keep the full context.
INFOGRAPHIC_SOURCE_CAP = 1400


async def deliver_work_infographic(self, brief: dict) -> dict:
    """[[contributes.work.deliverable]] handler — single-page SVG infographic
    poster from a work brief.

    Same brief/return contract as ``deliver_work`` but generates the
    ``svg-diagram`` shape: one explorable gold-on-dark poster (click-to-highlight
    via ``data-net``) rather than a multi-slide deck. The brief's outline becomes
    the poster's labelled sections. Source text is capped at
    ``INFOGRAPHIC_SOURCE_CAP`` chars (concept-level grounding only) so rich
    gather results can't push the single-shot SVG past viz's token ceiling.

    `brief`: {ask, title, brief, outline: [str], sources: str, run_id}.
    Returns {id, preview_url, artifact_path, open_url} or {error}."""
    brief = brief or {}
    outline = "\n".join(f"- {s}" for s in brief.get("outline") or [])
    sources = (brief.get("sources") or "")[:INFOGRAPHIC_SOURCE_CAP]
    prompt = (
        f"Create a single-page infographic poster titled: "
        f"{brief.get('title') or brief.get('ask') or 'Untitled'}\n"
        f"Brief: {brief.get('brief') or ''}\nAsk: {brief.get('ask') or ''}\n"
        f"Lay the poster out as titled sections, one per outline item, top to "
        f"bottom:\n{outline}\n\nKeep it a SINGLE, COMPACT explorable poster that "
        f"fits one screen — concise labels, clear visual hierarchy, "
        f"click-to-highlight related parts. Use the source material only to get "
        f"the concepts and terms right; do NOT transcribe it or add a section per "
        f"source. Prefer a small, complete SVG over an exhaustive one.\n\n"
        f"Source material (for context only):\n{sources}"
    )
    res = await self.generate(prompt, shape="svg-diagram")
    return _work_artifact_result(res)


@web_route("POST", "/api/rendered")
async def api_rendered(self, request) -> dict:
    """Client tells us the iframe finished rendering — fires viz:rendered."""
    body = await request.json()
    rid = (body.get("id") or "").strip()
    if not rid:
        return {"ok": False, "error": "id is required"}
    await self.emit("viz:rendered", {"id": rid})
    return {"ok": True}


async def list_items(self) -> list[dict]:
    """List all artifact records, newest first. Public verb — consumed
    by api_list AND cli_list AND any future cross-app caller."""
    rows = []
    root = self._outputs_root()
    if not root.exists():
        return []
    for sub in sorted(root.iterdir(), reverse=True):
        if not sub.is_dir():
            continue
        rec_rel = self._rel_record(sub.name)
        fm = self.vault_get_properties(rec_rel) or {}
        if not fm:
            continue
        rows.append({
            "id": fm.get("viz_id") or sub.name,
            "shape": fm.get("shape", ""),
            "prompt": fm.get("prompt", ""),
            "created": fm.get("created", ""),
            "updated": fm.get("updated", ""),
            "size_kb": fm.get("size_kb", 0),
        })
    return rows


@web_route("GET", "/api/list")
async def api_list(self, request) -> list[dict]:
    return await self.list_items()


@web_route("GET", "/api/get/{rid}")
async def api_get(self, request) -> dict:
    rid = request.path_params.get("rid", "")
    rec_rel = self._rel_record(rid)
    fm = self.vault_get_properties(rec_rel)
    if not fm:
        return {"ok": False, "error": f"no artifact with id '{rid}'"}
    return {
        "ok": True,
        "id": rid,
        "shape": fm.get("shape", ""),
        "prompt": fm.get("prompt", ""),
        "created": fm.get("created", ""),
        "updated": fm.get("updated", ""),
        "size_kb": fm.get("size_kb", 0),
        "history": fm.get("history", []),
        "html_path": self._rel_html(rid),
    }


@web_route("GET", "/api/html/{rid}")
async def api_html(self, request):
    """Serve the raw scene.html for iframe consumption.

    Uses FileResponse so the browser streams from disk rather than us
    slurping into memory. Cache-Control no-store so the iframe always
    sees the latest version after an iterate call.

    ?edit=1 injects the element-edit shim so clicks become edit picks (only
    when feature.element-edit.enabled is on — mirrors designer).
    """
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

    rid = request.path_params.get("rid", "")
    html_path = self._record_dir(rid) / "scene.html"
    if not html_path.exists():
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)

    edit = request.query_params.get("edit") == "1" and self._edit_enabled()
    if not edit:
        return FileResponse(
            html_path,
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    html = html_path.read_text(encoding="utf-8")
    shim = '<script src="/static/eos-edit-shim.js"></script>'
    html = html.replace("</body>", shim + "</body>", 1) if "</body>" in html else html + shim
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@web_route("POST", "/api/export-mp4")
async def api_export_mp4(self, request) -> dict:
    """Record an artifact's HTML animation to scene.mp4 (html-video borrow).

    Dark-flagged + shape-gated: returns ``{ok: False}`` unless
    `feature.html-record.enabled` is on AND the artifact's shape is in
    SHAPE_SUPPORTS_RECORD (anim-explainer). Uses the playwright plugin for
    deterministic frame capture + ffmpeg for encode — no new dependency.
    """
    if not self._html_record_enabled():
        return {"ok": False, "error": "html→mp4 export is disabled"}

    body = await request.json()
    rid = (body.get("id") or "").strip()
    if not rid:
        return {"ok": False, "error": "id is required"}

    html_path = self._record_dir(rid) / "scene.html"
    if not html_path.exists():
        return {"ok": False, "error": f"no artifact with id '{rid}'"}

    fm = self.vault_get_properties(self._rel_record(rid)) or {}
    shape = fm.get("shape", "")
    if not self._shape_supports_record(shape):
        return {"ok": False, "error": f"shape '{shape}' cannot be recorded (anim-explainer only)"}

    plugin = self.service("playwright")
    if plugin is None:
        return {"ok": False, "error": "playwright plugin not installed"}

    from emptyos.sdk.media import record_html_to_mp4

    out_mp4 = self._record_dir(rid) / "scene.mp4"
    try:
        fps = int(body.get("fps") or self.app_config("record_fps", 24) or 24)
    except (TypeError, ValueError):
        fps = 24
    duration = body.get("duration_s")
    res = await record_html_to_mp4(
        plugin, html_path, out_mp4,
        fps=fps,
        duration_s=float(duration) if duration else None,
        context_id=f"viz-record-{rid}",
    )
    if not res.get("ok"):
        return res

    # Validity self-check (dark flag): a recording the seek couldn't step is a
    # frozen clip — catch it here instead of shipping it. Hard findings fail
    # the export (the mp4 stays on disk for inspection); soft findings ride
    # along on the ok response. Fails open when ffmpeg is absent.
    review_out = None
    if self._output_review_enabled():
        from emptyos.sdk.media import review_video

        verdict = await review_video(out_mp4, require_audio=False)
        review_out = {"ok": verdict.ok, "hard": list(verdict.hard),
                      "soft": list(verdict.soft), "metrics": verdict.metrics}
        if not verdict.ok:
            return {
                "ok": False,
                "id": rid,
                "error": "recorded video failed self-review: " + "; ".join(verdict.hard),
                "review": review_out,
            }

    await self.emit("viz:recorded", {"id": rid, "shape": shape, "frames": res.get("frames")})
    # Return a vault-relative path (viz convention; never leak the absolute
    # machine path the SDK result carries).
    base = self.vault_config("path", "30_Resources/EmptyOS/viz")
    return {
        "ok": True,
        "id": rid,
        "frames": res.get("frames"),
        "fps": res.get("fps"),
        "duration_s": res.get("duration_s"),
        "video_url": f"/viz/api/video/{rid}",
        "mp4_path": f"{base}/outputs/{rid}/scene.mp4",
        **({"review": review_out} if review_out else {}),
    }


@web_route("GET", "/api/video/{rid}")
async def api_video(self, request):
    """Serve the recorded scene.mp4 for a <video> tag / download."""
    from fastapi.responses import FileResponse, JSONResponse

    rid = request.path_params.get("rid", "")
    mp4 = self._record_dir(rid) / "scene.mp4"
    if not mp4.exists():
        return JSONResponse({"ok": False, "error": "no recording for this artifact"}, status_code=404)
    return FileResponse(
        mp4,
        media_type="video/mp4",
        filename=f"{rid}.mp4",
        headers={"Cache-Control": "no-store"},
    )


@web_route("DELETE", "/api/items/{rid}")
async def api_delete(self, request) -> dict:
    import shutil

    rid = request.path_params.get("rid", "")
    record_dir = self._record_dir(rid)
    if not record_dir.exists():
        return {"ok": False, "error": f"no artifact with id '{rid}'"}

    # Reference-aware delete gate: refuse when this artifact is a LIVE (reference)
    # embed in any note, unless ?force=1. Snapshot embeds own their own bytes and
    # never pin the source, so they don't appear here. No-op when the embed
    # feature is off (no note carries viz_ref_ids). See embeds.py + the plan.
    refs = self._referencing_notes(rid) if self._embed_enabled() else []
    if refs and request.query_params.get("force") != "1":
        return {
            "ok": False,
            "reason": "referenced",
            "error": f"used as a live embed in {len(refs)} note(s) — pass force=1 to delete anyway",
            "notes": refs,
        }

    shutil.rmtree(record_dir)
    return {"ok": True, "id": rid, "broke_refs": refs}


async def artifact_path(self, id: str = "") -> str:
    """Vault-relative path for the timeline aggregator's entity_source.

    Returns "" when the record is gone so the timeline drawer doesn't
    render a dead entity (existence-guard via ``vault_rel_if_exists``).
    """
    return self.vault_rel_if_exists(self._rel_record(id)) if id else ""


@cli_command("list")
async def cli_list(self) -> None:
    for r in await self.list_items():
        print(f"{r['id']} [{r['shape']}] {r['prompt'][:60]}")


@cli_command("generate")
async def cli_generate(self, prompt: str, shape: str = "3d-scene") -> None:
    """eos viz generate "<prompt>" [shape]"""
    result = await self.generate(prompt, shape=shape)
    print(json.dumps(result, indent=2))
