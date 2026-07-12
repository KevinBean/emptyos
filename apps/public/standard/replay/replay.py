"""replay — the dynamic-pipeline runner that replays a recipe.

Each recipe step becomes a :class:`Stage` in a per-run :class:`Pipeline`
(``emptyos/sdk/pipeline.py``), giving resume/run-folder/progress for free. The
genuinely new control-flow is **pause-on-gate**: a state-changing verb step that
isn't auto-eligible proposes a pending action and raises :class:`GatePause`,
which the pipeline's existing error→resume path catches; the run survives with
its completed-step results, and :func:`resume_run` picks up once the user Applies.

Safety is the existing machinery, never bypassed (``.claude/rules/autopilot-grants.md``):
  * ``never`` verbs hard-refuse (the run blocks — "do it by hand, then resume").
  * ``gated`` verbs propose to the rooms review gate and pause.
  * ``stable`` verbs auto-run only when the system's stable-default / grant policy
    says so (the same ``autopilot.decide`` the rooms gate uses), audited.
A recipe is an appearance, not ground truth: every step re-derives against
current state; ``recorded_output`` is provenance shown in the dry-run, never
replayed (three-natures lens).

Bound to ReplayApp as:
    dry_run             = _replay.dry_run
    run                 = _replay.run
    resume_run          = _replay.resume_run
    _build_pipeline     = _replay._build_pipeline
    _make_stage_runner  = _replay._make_stage_runner
    _eligibility_class  = _replay._eligibility_class
    _decide_verb        = _replay._decide_verb
    _bind_inputs        = _replay._bind_inputs
    _load_recipe        = _replay._load_recipe
    _get_pending        = _replay._get_pending
    _translate          = _replay._translate
    _post_run_emit      = _replay._post_run_emit
Adding a method here? Add a matching binding line in app.py.

Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from emptyos.sdk import autopilot as _autopilot
from emptyos.sdk import on_event
from emptyos.sdk.pipeline import Pipeline, PipelineError, Stage
from emptyos.sdk.utils import config_flag, parse_llm_json

from .shared import DRY_PLACEHOLDER, parse_recipe, resolve_template, safe_resolve

if TYPE_CHECKING:  # noqa: F401 — type hints only
    from .app import ReplayApp

GATE_PREFIX = "GATE_PAUSE:"
NEVER_MARK = "is never auto-runnable"
RUN_KIND = "replay-runs"


class GatePause(Exception):
    """Raised inside a verb stage that proposed a pending action and must wait
    for the user to Apply. Carries the step name + action id in its message so
    the pipeline's error→resume path preserves them (str is ``GATE_PAUSE:<step>:<id>``)."""

    def __init__(self, step: str, action_id: str):
        self.step = step
        self.action_id = action_id
        super().__init__(f"{GATE_PREFIX}{step}:{action_id}")


# ── eligibility + decision ──────────────────────────────────────────────────


def _eligibility_class(self, verb: str) -> str:
    """The verb's live registry eligibility (stable|gated|never); gated if unknown."""
    try:
        entry = self.kernel.apps.get_verbs().get(verb)
        return entry.eligibility if entry else "gated"
    except Exception:
        return "gated"


def _decide_verb(self, recipe_id: str, verb: str) -> dict:
    """The same auto-vs-gate decision the rooms gate makes, with a replay actor
    + scope. Honors the system's stable-default / budget flags (default off)."""
    data_root = self.kernel.config.data_dir
    elig_fn = getattr(self.kernel, "autopilot_eligible_set", None)
    eligible = elig_fn() if callable(elig_fn) else None
    return _autopilot.decide(
        data_root,
        actor_type="replay",
        actor_id=recipe_id,
        verb=verb,
        scope_candidates=[f"replay:{recipe_id}", "global"],
        eligible=eligible,
        auto_stable=config_flag(self.kernel.config, "autopilot.auto_stable_default"),
        enforce_budget=config_flag(self.kernel.config, "autopilot.enforce_budget_caps"),
    )


async def _get_pending(self, action_id: str):
    try:
        return await self.call_app("rooms", "get_pending", action_id=action_id)
    except Exception:
        return None


# ── pipeline construction ───────────────────────────────────────────────────


def _make_stage_runner(self, step: dict, recipe: dict):
    n = step.get("n")
    stage_name = f"step-{n}"

    async def run(ctx):
        if step.get("kind") == "think":
            prompt = resolve_template(step.get("prompt", ""), ctx.inputs, ctx.results)
            out = await self.think(prompt, domain=step.get("domain", "text"), temperature=0.3)
            if step.get("expects_json"):
                return parse_llm_json(out, fallback={"_raw": out})
            return out

        verb = step.get("verb", "")
        app_id, _, method = verb.partition(".")
        args = resolve_template(step.get("args") or {}, ctx.inputs, ctx.results)
        if not isinstance(args, dict):
            args = {}

        cls = self._eligibility_class(verb)
        if cls == "never":
            raise PipelineError(
                f"{stage_name} `{verb}` {NEVER_MARK} — run it by hand, then resume the run"
            )

        # Re-entry after a pause: a gate marker was injected on resume.
        gate = ctx.results.get(f"{stage_name}__gate")
        if isinstance(gate, dict) and gate.get("action_id"):
            pend = await self._get_pending(gate["action_id"])
            status = (pend or {}).get("status")
            if status == "applied":
                return {"applied_via_gate": gate["action_id"], "result": str((pend or {}).get("result"))[:500]}
            if status in ("rejected", "failed"):
                raise PipelineError(f"{stage_name} was {status} at the review gate")
            raise GatePause(stage_name, gate["action_id"])  # still pending → re-pause

        decision = self._decide_verb(recipe["id"], verb)
        if decision.get("action") == "auto":
            res = await self.call_app(app_id, method, **args)
            try:
                _autopilot.append_audit(
                    self.kernel.config.data_dir,
                    actor={"type": "replay", "id": recipe["id"]},
                    app=app_id,
                    method=method,
                    args=args,
                    grant_id=decision.get("grant_id"),
                    ok=True,
                )
            except Exception:
                pass
            return {"auto": True, "result": str(res)[:500]}

        # gated → propose + pause
        act = await self.propose_action(
            app=app_id, method=method, args=args, source_actor={"type": "replay", "id": recipe["id"]}
        )
        raise GatePause(stage_name, act["id"])

    return run


def _build_pipeline(self, recipe: dict) -> Pipeline:
    stages = [
        Stage(
            name=f"step-{s.get('n')}",
            run=self._make_stage_runner(s, recipe),
            weight=2 if s.get("kind") == "think" else 1,
        )
        for s in recipe.get("steps") or []
    ]
    return Pipeline(name=f"recipe:{recipe['id']}", stages=stages, app=self, registry_kind=RUN_KIND)


# ── helpers ─────────────────────────────────────────────────────────────────


def _load_recipe(self, recipe_id: str) -> dict | None:
    detail = self.recipes.detail(f"{recipe_id}.md")
    return parse_recipe(detail) if detail else None


def _bind_inputs(self, recipe: dict, provided: dict) -> dict:
    bound = {}
    for i in recipe.get("inputs") or []:
        name = i.get("name")
        if not name:
            continue
        bound[name] = provided.get(name, i.get("default", ""))
    # Allow extra provided keys (forward-compatible), provided wins.
    for k, v in (provided or {}).items():
        bound[k] = v
    return bound


def _translate(self, summary: dict) -> dict:
    """Map a pipeline summary to a replay status the UI understands."""
    status = summary.get("status")
    out = dict(summary)
    if status == "error":
        err = summary.get("error") or ""
        if err.startswith(GATE_PREFIX):
            rest = err[len(GATE_PREFIX):]
            step, _, action_id = rest.partition(":")
            out["replay_status"] = "awaiting-gate"
            out["gate_step"] = step
            out["gate_action_id"] = action_id
        elif NEVER_MARK in err:
            out["replay_status"] = "blocked"
        else:
            out["replay_status"] = "error"
    elif status == "complete":
        out["replay_status"] = "complete"
    else:
        out["replay_status"] = status or "running"
    return out


async def _post_run_emit(self, out: dict, recipe_id: str) -> None:
    rs = out.get("replay_status")
    ev = {
        "awaiting-gate": "replay:run_paused",
        "blocked": "replay:run_blocked",
        "complete": "replay:run_completed",
    }.get(rs)
    if ev:
        await self.emit(ev, {"recipe": recipe_id, "run_id": out.get("run_id"), "status": rs})


# ── public entrypoints ──────────────────────────────────────────────────────


async def dry_run(self, recipe_id: str, inputs: dict | None = None) -> dict:
    """Resolve every step's args + classify disposition WITHOUT dispatching."""
    if not self._enabled():
        return {"ok": False, "error": "replay disabled"}
    recipe = self._load_recipe(recipe_id)
    if not recipe:
        return {"ok": False, "error": "recipe not found"}
    bound = self._bind_inputs(recipe, inputs or {})
    sim: dict = {}
    plan = []
    blocked = False
    for s in recipe.get("steps") or []:
        n = s.get("n")
        if s.get("kind") == "think":
            plan.append(
                {
                    "n": n,
                    "kind": "think",
                    "domain": s.get("domain", "text"),
                    "prompt": safe_resolve(s.get("prompt", ""), bound, sim),
                    "why": s.get("why", ""),
                    "disposition": "run",
                }
            )
            sim[f"step-{n}"] = DRY_PLACEHOLDER
        else:
            verb = s.get("verb", "")
            args = safe_resolve(s.get("args") or {}, bound, sim)
            cls = self._eligibility_class(verb)
            if cls == "never":
                disp = "blocked"
                blocked = True
            else:
                disp = "auto" if self._decide_verb(recipe["id"], verb).get("action") == "auto" else "review"
            plan.append(
                {
                    "n": n,
                    "kind": "verb",
                    "verb": verb,
                    "args": args,
                    "why": s.get("why", ""),
                    "eligibility": cls,
                    "disposition": disp,
                }
            )
            sim[f"step-{n}"] = s.get("recorded_output") or DRY_PLACEHOLDER
    return {
        "ok": True,
        "recipe": recipe_id,
        "name": recipe.get("name"),
        "inputs": bound,
        "plan": plan,
        "blocked": blocked,
    }


async def run(self, recipe_id: str, inputs: dict | None = None) -> dict:
    if not self._enabled():
        return {"ok": False, "error": "replay disabled"}
    recipe = self._load_recipe(recipe_id)
    if not recipe:
        return {"ok": False, "error": "recipe not found"}
    if not (recipe.get("steps") or []):
        return {"ok": False, "error": "recipe has no steps"}
    bound = self._bind_inputs(recipe, inputs or {})
    pipe = self._build_pipeline(recipe)
    summary = await pipe.start(inputs=bound)
    out = self._translate(summary)
    await self.emit(
        "replay:run_started",
        {"recipe": recipe_id, "run_id": out.get("run_id"), "status": out.get("replay_status")},
    )
    await self._post_run_emit(out, recipe_id)
    return {"ok": True, **out}


@on_event("rooms:action_applied")
async def _on_action_applied(self, event) -> None:
    """Auto-resume a gate-paused run when its pending action is Applied — only
    when the ``replay.feature.auto_resume`` sub-flag is on (default off; manual
    Resume otherwise)."""
    try:
        if not self._enabled() or not self.setting("replay.feature.auto_resume", False):
            return
        aid = (getattr(event, "data", None) or {}).get("action_id")
        if not aid:
            return
        for handle, state in self.runs(RUN_KIND).recent_states(50):
            err = state.get("error") or ""
            if err.startswith(GATE_PREFIX) and err.endswith(":" + aid):
                await self.resume_run(handle.run_id)
                return
    except Exception:
        return


async def resume_run(self, run_id: str) -> dict:
    if not self._enabled():
        return {"ok": False, "error": "replay disabled"}
    reg = self.runs(RUN_KIND)
    handle = reg.get(run_id)
    if handle is None:
        return {"ok": False, "error": "run not found"}
    state = handle.read_state()
    if not state:
        return {"ok": False, "error": "run has no state"}
    recipe_id = (state.get("pipeline") or "").split(":", 1)[-1]
    recipe = self._load_recipe(recipe_id)
    if not recipe:
        return {"ok": False, "error": "recipe for this run is gone"}
    # If paused at a gate, inject the gate marker so the stage re-checks status.
    err = state.get("error") or ""
    if err.startswith(GATE_PREFIX):
        rest = err[len(GATE_PREFIX):]
        step, _, action_id = rest.partition(":")
        results = dict(state.get("results") or {})
        results[f"{step}__gate"] = {"action_id": action_id}
        reg.update_state(run_id, results=results)
    pipe = self._build_pipeline(recipe)
    summary = await pipe.resume(run_id)
    out = self._translate(summary)
    await self._post_run_emit(out, recipe_id)
    return {"ok": True, **out}
