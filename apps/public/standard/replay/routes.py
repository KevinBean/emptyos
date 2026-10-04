"""replay — HTTP surface (bound to ReplayApp).

Bound in app.py:
    api_config          = _routes.api_config
    api_distill_session = _routes.api_distill_session
    api_distill_trace   = _routes.api_distill_trace
    api_recipes         = _routes.api_recipes
    api_recipe          = _routes.api_recipe
Replay/run routes (dry-run, run, resume) land in Phase 2 (replay.py + here).
"""

from __future__ import annotations

from emptyos.sdk import web_route

from .shared import parse_recipe


@web_route("GET", "/api/config")
async def api_config(self, request):
    """Dark-default flag the page reads on load."""
    return {"enabled": self._enabled()}


@web_route("POST", "/api/distill/session")
async def api_distill_session(self, request):
    if not self._enabled():
        return {"ok": False, "error": "disabled"}
    body = await request.json()
    sid = (body.get("sid") or "").strip()
    if not sid:
        return {"ok": False, "error": "sid required"}
    target = body.get("target") or "recipe"
    return await self.distill_from_session(sid, target=target)


@web_route("POST", "/api/distill/trace")
async def api_distill_trace(self, request):
    if not self._enabled():
        return {"ok": False, "error": "disabled"}
    body = await request.json()
    trace_id = (body.get("trace_id") or "").strip()
    if not trace_id:
        return {"ok": False, "error": "trace_id required"}
    target = body.get("target") or "recipe"
    return await self.distill_from_trace(trace_id, target=target)


@web_route("GET", "/api/recipes")
async def api_recipes(self, request):
    if not self._enabled():
        return {"recipes": []}
    # List uses frontmatter only (steps_count) — no body parse needed, and it
    # avoids the index/scan parser split: machine steps live in the body json
    # fence, surfaced per-recipe by api_recipe (which reads body via detail()).
    out = []
    for item in self.recipes.list():
        try:
            steps = int(item.get("steps_count") or 0)
        except (TypeError, ValueError):
            steps = 0
        out.append(
            {
                "id": item.get("id") or "",
                "name": item.get("name") or "",
                "goal": item.get("goal") or "",
                "status": item.get("status") or "draft",
                "steps": steps,
                "source_session": item.get("source_session") or "",
                "updated": item.get("updated") or "",
            }
        )
    return {"recipes": out}


@web_route("GET", "/api/recipes/{rid}")
async def api_recipe(self, request):
    if not self._enabled():
        return {"error": "disabled"}
    rid = request.path_params.get("rid", "")
    detail = self.recipes.detail(f"{rid}.md")
    if not detail:
        return {"error": "not found"}
    return {"recipe": parse_recipe(detail)}


# ── Replay (replay.py) ───────────────────────────────────────────────────────


@web_route("POST", "/api/recipes/{rid}/dry-run")
async def api_dry_run(self, request):
    if not self._enabled():
        return {"ok": False, "error": "disabled"}
    rid = request.path_params.get("rid", "")
    body = await request.json()
    return await self.dry_run(rid, body.get("inputs") or {})


@web_route("POST", "/api/recipes/{rid}/run")
async def api_run(self, request):
    if not self._enabled():
        return {"ok": False, "error": "disabled"}
    rid = request.path_params.get("rid", "")
    body = await request.json()
    return await self.run(rid, body.get("inputs") or {})


@web_route("GET", "/api/runs/{run_id}")
async def api_run_status(self, request):
    if not self._enabled():
        return {"ok": False, "error": "disabled"}
    run_id = request.path_params.get("run_id", "")
    handle = self.runs("replay-runs").get(run_id)
    if handle is None:
        return {"ok": False, "error": "run not found"}
    state = handle.read_state() or {}
    summary = {
        "run_id": run_id,
        "status": state.get("status"),
        "error": state.get("error"),
        "stage": state.get("stage"),
        "completed": state.get("completed") or [],
        "progress": state.get("progress", 0),
        "results": state.get("results") or {},
        "failed_stage": state.get("failed_stage"),
    }
    return {"ok": True, **self._translate(summary)}


@web_route("POST", "/api/runs/{run_id}/resume")
async def api_resume(self, request):
    if not self._enabled():
        return {"ok": False, "error": "disabled"}
    run_id = request.path_params.get("run_id", "")
    return await self.resume_run(run_id)
