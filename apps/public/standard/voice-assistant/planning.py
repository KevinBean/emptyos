"""Voice-assistant — plan-then-execute dispatcher + confirm gate + debug surface.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: the Phase-1 plan/execute pair (LLM plans intent calls,
caller previews, then runs), the one-shot /api/dispatch path, the
confirm-gated intent replay (/api/confirm-intent), and /debug/intents.
Voice keeps its inline streaming dispatcher in chat_pipeline.py — these
methods serve quick-action and any caller that wants preview-then-run.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: no cross-module reach (scope + validate shims
resolve on self).
Do not import from ``.app`` (it imports us, which would cycle).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from emptyos.sdk import web_route

from .intents import MAX_INTENTS_IN_PROMPT, build_plan_dict
from .prompts import PROMPTS

if TYPE_CHECKING:
    from .app import VoiceAssistantApp  # noqa: F401 — type hints only


# ─── Bind to VoiceAssistantApp class as ─────────────────────────────────
#   plan_actions       = _planning.plan_actions
#   execute_plan       = _planning.execute_plan
#   api_plan           = _planning.api_plan
#   api_execute_plan   = _planning.api_execute_plan
#   api_dispatch       = _planning.api_dispatch
#   api_confirm_intent = _planning.api_confirm_intent
#   debug_intents      = _planning.debug_intents
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────────


async def plan_actions(self, user_text: str, scope: str = "full", limit: int = 20) -> dict:
    """LLM-plan a sequence of intent calls for *user_text*. Does NOT execute.

    scope='full' surfaces the entire registry (cap *limit*, default 20).
    scope='voice' uses the same _scope_intents logic the voice path uses.
    """
    if not user_text or not user_text.strip():
        return {"raw_reply": "", "say": "", "calls": [], "error": "empty input"}

    if scope == "voice":
        scoped = self._scope_intents(None)
    else:
        scoped = list(self._intents.values())[: max(1, int(limit))]

    intent_block = self._intent_prompt_block(scoped)
    system = PROMPTS.aura_system + intent_block + PROMPTS.plan_system_foot
    try:
        reply = await self.think(user_text, system=system, domain="text", temperature=0.2)
    except Exception as e:
        return {"raw_reply": "", "say": "", "calls": [], "error": f"think failed: {e}"}
    return build_plan_dict(reply or "", scoped)


async def execute_plan(self, plan: dict, only_indices: list[int] | None = None) -> list[dict]:
    """Run plan calls serially. Returns one result dict per requested step.

    Each step is `{"index", "verb", ok|error|skipped, "result"?}`.
    Failed steps are isolated — execution continues. Recent-apps deque is
    updated on each successful call so subsequent voice turns inherit scope.

    **The plan is untrusted.** ``POST /api/execute-plan`` (and quick-action's
    forwarder) accept a caller-supplied plan dict, and validation used to live
    only in ``build_plan_dict`` — which those endpoints never call. So every
    step is re-resolved here against the intent registry:

    * the verb must be a known, in-scope intent;
    * ``app``/``method`` are **derived from the registry entry**, never read
      from the plan — otherwise a step could name a benign verb and dispatch
      something else entirely;
    * args are re-validated against the entry's schema.

    ``build_plan_dict`` sets ``app``/``method`` from the same registry, so the
    legitimate path is unchanged; this is the defense-in-depth pass
    ``/api/confirm-intent`` already does.
    """
    calls = plan.get("calls") or []
    indices = (
        list(range(len(calls)))
        if only_indices is None
        else [i for i in only_indices if 0 <= i < len(calls)]
    )
    idx_set = set(indices)
    results: list[dict] = []
    for i, call in enumerate(calls):
        if i not in idx_set:
            results.append({"index": i, "verb": call.get("verb"), "skipped": True})
            continue
        if call.get("error"):
            results.append({"index": i, "verb": call.get("verb"), "error": call["error"]})
            continue
        verb = call.get("verb") or ""
        entry = (getattr(self, "_intents", None) or {}).get(verb)
        if entry is None:
            results.append(
                {"index": i, "verb": verb, "error": f"unknown or out-of-scope intent: {verb}"}
            )
            continue
        # Derived, not read from the plan — see the docstring.
        app_id = entry.get("_app_id")
        method = entry.get("method")
        if not app_id or not method:
            results.append({"index": i, "verb": verb, "error": "missing app/method"})
            continue
        args = call.get("args") or {}
        ok, msg = self._validate_args(entry.get("args") or {}, args)
        if not ok:
            results.append({"index": i, "verb": verb, "error": msg})
            continue
        try:
            res = await self.call_app(app_id, method, **args)
        except Exception as e:
            results.append({"index": i, "verb": call.get("verb"), "error": str(e)})
            continue
        if app_id in self._recent_apps:
            self._recent_apps.remove(app_id)
        self._recent_apps.append(app_id)
        self._persist_recent_apps()
        results.append(
            {
                "index": i,
                "verb": call.get("verb"),
                "ok": True,
                "result": res if isinstance(res, dict) else {"value": res},
            }
        )
    return results


@web_route("POST", "/api/plan")
async def api_plan(self, request):
    """Plan-only — LLM produces a list of intent calls, nothing executes."""
    data = await request.json()
    text = (data.get("text") or "").strip()
    if not text:
        return {"error": "text required"}
    scope = data.get("scope") or "full"
    limit = data.get("limit") or 20
    return await self.plan_actions(text, scope=scope, limit=limit)


@web_route("POST", "/api/execute-plan")
async def api_execute_plan(self, request):
    """Run a plan returned by /api/plan. Optional `only_indices` to skip steps."""
    data = await request.json()
    plan = data.get("plan")
    if not isinstance(plan, dict):
        return {"error": "plan required"}
    only = data.get("only_indices")
    if only is not None and not isinstance(only, list):
        return {"error": "only_indices must be a list"}
    results = await self.execute_plan(plan, only_indices=only)
    return {"results": results}


@web_route("POST", "/api/dispatch")
async def api_dispatch(self, request):
    """One-shot: plan + execute in a single call. The "do it for me" path
    once a caller is confident enough not to need the preview step."""
    data = await request.json()
    text = (data.get("text") or "").strip()
    if not text:
        return {"error": "text required"}
    scope = data.get("scope") or "full"
    limit = data.get("limit") or 20
    plan = await self.plan_actions(text, scope=scope, limit=limit)
    if plan.get("error") or not plan.get("calls"):
        return {"plan": plan, "results": []}
    results = await self.execute_plan(plan)
    return {"plan": plan, "results": results}


@web_route("POST", "/api/confirm-intent")
async def api_confirm_intent(self, request):
    """Run an intent that was previously gated by `confirm = true`.

    Frontend calls this after the user approves the confirm dialog. Re-validates
    args (defense-in-depth) and runs the same call_app + recent-apps logic the
    inline dispatcher uses. Returns the intent's `say` / `card` for the UI to
    speak / render.
    """
    data = await request.json()
    verb = (data.get("verb") or "").strip()
    args = data.get("args") or {}
    if not verb or verb not in self._intents:
        return {"error": f"unknown intent: {verb}"}
    entry = self._intents[verb]
    ok, msg = self._validate_args(entry.get("args") or {}, args)
    if not ok:
        return {"error": msg}
    app_id = entry.get("_app_id")
    method = entry.get("method")
    try:
        result = await self.call_app(app_id, method, **args)
    except Exception as e:
        return {"error": str(e)}
    if app_id:
        if app_id in self._recent_apps:
            self._recent_apps.remove(app_id)
        self._recent_apps.append(app_id)
        self._persist_recent_apps()
    # Record to the shared undo log so a confirmed intent is reversible too.
    from emptyos.sdk.actions_log import lookup_inverse, record_action
    inverse = lookup_inverse(self.kernel, app_id, method) if app_id else ""
    if inverse:
        record_action(self._actions_log_path(), app=app_id, method=method,
                      args=args, result=result, inverse=inverse)
    out: dict = {"ok": True, "verb": verb, "reversible": bool(inverse)}
    if isinstance(result, dict):
        if result.get("say"):
            out["say"] = result["say"]
        card = result.get("card")
        if isinstance(card, dict) and card.get("renderer"):
            out["card"] = {
                "renderer": card["renderer"],
                "data": card.get("data"),
                "title": card.get("title"),
            }
        # Forward the deep-link so a confirmed creator / form submit still gives
        # the "go check what you just did" affordance (parity with the turn path).
        link = result.get("link")
        if isinstance(link, dict) and link.get("href"):
            out["link"] = {"text": link.get("text") or "Open", "href": link["href"]}
    return out


@web_route("GET", "/debug/intents")
async def debug_intents(self, request):
    """Debug surface — full registry plus what's currently in scope.

    Mirrors /hub/debug/panels. Use ?companion=<id> to test the scope window
    for a specific companion.
    """
    companion_id = request.query_params.get("companion") or None
    if companion_id == "":
        companion_id = None
    scoped = self._scope_intents(companion_id)
    scoped_verbs = {e.get("verb") for e in scoped}
    return {
        "registry": [
            {
                "verb": e.get("verb"),
                "app": e.get("_app_id"),
                "method": e.get("method"),
                "example": e.get("example"),
                "always": bool(e.get("always")),
                "description": e.get("description"),
                "args": e.get("args") or {},
                "card": e.get("card"),
            }
            for e in self._intents.values()
        ],
        "scoped": sorted(scoped_verbs),
        "companion": companion_id,
        "recent_apps": list(self._recent_apps),
        "max_in_prompt": MAX_INTENTS_IN_PROMPT,
        "narrators": [
            {"intent": e.get("intent"), "app": e.get("_app_id"), "method": e.get("method")}
            for e in self._narrators
        ],
    }
