"""viz — auto-refine loop: a DecisionGraph driving a Pipeline (graph_pipeline #2).

The second *driver* consumer of `emptyos.sdk.graph_pipeline` (synth is the first),
the one that blesses the GraphRunner binding. Where synth generates data rows, this
generates a viz artifact and loops generate → critique → refine until an LLM critic
scores it ship-ready (or a round cap trips), then pauses for a human to *see* the
artifact and ship or send back.

Reuses viz's existing `generate()` + `api_iterate()` verbatim — no gen logic is
duplicated. The only new thing is the **critic** (an LLM judge of the rendered
HTML) + the graph orchestration.

Design note — the gate dodges the decision_graph var↔literal limitation that synth
hit: instead of comparing `score < threshold` (var↔var, unsupported), the critique
stage computes `passed = score >= threshold` against the run's own threshold and
emits the *boolean*; the gate routes on `passed` truthy/falsy. The round cap is the
only literal, baked at graph-build time.

Topology (one self-looping pipeline node + a gate + a human node):

    produce(pipeline) → gate ──passed─────────────→ human(see it)
        ↑                  ├ !passed & round<cap → produce (refine loop)
        └──────────────────┘  round≥cap          → escalate(end)
                            human: Ship → done(end) · Reject → produce (with note)

Bound onto VizApp; `_refine_setup` is called from VizApp.setup. Pure-ish: the only
I/O is via viz's own methods + `self.think`. Do not import from `.app`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from emptyos.sdk.decision_graph import DecisionGraph
from emptyos.sdk.graph_pipeline import GraphPipelineError, collect_inputs
from emptyos.sdk.graph_run import GraphRunService
from emptyos.sdk.pipeline import Pipeline, Stage
from emptyos.sdk.utils import FakeRequest, parse_llm_json

if TYPE_CHECKING:  # pragma: no cover
    from emptyos.sdk.pipeline import StageContext

    from .app import VizApp  # noqa: F401


# ─── Bind to VizApp class as ─────────────────────────────────────────────────
#   _refine_setup     = _refine._refine_setup     # call from setup()
#   _refine_dispatch  = _refine._refine_dispatch
#   api_refine_graph  = _refine.api_refine_graph
#   api_refine_start  = _refine.api_refine_start
#   api_refine_get    = _refine.api_refine_get
#   api_refine_list   = _refine.api_refine_list
#   api_refine_decision = _refine.api_refine_decision
# ─────────────────────────────────────────────────────────────────────────────

from emptyos.sdk import web_route  # noqa: E402  (after the binding banner)


CRITIQUE_SYSTEM = (
    "You are a strict reviewer of generated single-file HTML artifacts (Three.js "
    "scenes, SVG diagrams, Chart.js charts, network graphs, slide decks, etc.). "
    "Judge how well the artifact realises the brief: correctness, completeness, "
    "visual quality, and whether it would actually render without errors. Output "
    "ONLY JSON: {\"score\": <0..1>, \"issues\": [{\"problem\": \"<short, specific, "
    "fixable>\"}], \"summary\": \"<one line>\"}. score >= 0.8 means ship-ready. Be "
    "concrete in issues so a regenerator can act on them. Do NOT inflate the score; "
    "an artifact that ignores part of the brief or has obvious bugs is below 0.8."
)


def _build_tweak(issues: list, note: str) -> str:
    """Turn critic issues + an optional human note into an iterate change-request."""
    lines = []
    for it in (issues or [])[:8]:
        p = it.get("problem") if isinstance(it, dict) else str(it)
        if p:
            lines.append(f"- {p}")
    parts = []
    if lines:
        parts.append("Fix these issues from review:\n" + "\n".join(lines))
    if note:
        parts.append(f"Human direction: {note}")
    return "\n\n".join(parts) or "Improve overall quality and fidelity to the brief."


# ─────────────────────────────── pipeline stages ──────────────────────────────


async def _stage_render(ctx: "StageContext") -> dict:
    """Generate (first pass) or iterate (refine pass) the viz artifact."""
    inp = ctx.inputs
    rid = inp.get("rid")
    if rid:
        tweak = _build_tweak(inp.get("issues") or [], inp.get("calib_note") or "")
        res = await ctx.app.api_iterate(FakeRequest(body={"id": rid, "prompt": tweak}))
    else:
        res = await ctx.app.generate(inp.get("prompt", ""), shape=inp.get("shape") or "3d-scene")
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error", "generation failed"), "rid": rid or ""}
    await ctx.progress(1.0, res.get("id", ""))
    return {"ok": True, "rid": res["id"], "html_path": res.get("html_path", ""),
            "shape": res.get("shape", inp.get("shape"))}


async def _stage_critique(ctx: "StageContext") -> dict:
    """LLM-judge the rendered HTML; emit score + a threshold-relative `passed`."""
    r = ctx.result("render") or {}
    rid = r.get("rid") or ""
    threshold = float(ctx.inputs.get("threshold", 0.8))
    if not r.get("ok") or not rid:
        return {"score": 0.0, "passed": False, "rid": rid,
                "issues": [{"problem": r.get("error", "generation failed")}],
                "summary": "generation failed — will retry"}
    html_path = r.get("html_path") or ""
    try:
        html = (ctx.app.vault_root / html_path).read_text(encoding="utf-8")
    except Exception:
        html = ""
    user = (
        f"BRIEF: {ctx.inputs.get('prompt', '')}\n"
        f"SHAPE: {r.get('shape')}\n\n"
        f"ARTIFACT HTML (truncated):\n{html[:14000]}"
    )
    raw = await ctx.app.think(user, domain="code", system=CRITIQUE_SYSTEM, temperature=0.2)
    j = parse_llm_json(raw, {})
    if not isinstance(j, dict):
        j = {}
    try:
        score = max(0.0, min(1.0, float(j.get("score", 0) or 0)))
    except (TypeError, ValueError):
        score = 0.0
    issues = [x for x in (j.get("issues") or []) if isinstance(x, dict)]
    passed = score >= threshold
    summary = j.get("summary") or f"score {score:.2f}"
    summary = f"{summary} — score {score:.2f} vs threshold {threshold:.2f} → {'SHIP-READY' if passed else 'refine'}"
    return {"score": round(score, 3), "passed": passed, "rid": rid,
            "html_path": html_path, "issues": issues[:8], "summary": summary}


VIZ_REFINE_STAGES = [
    Stage("render", _stage_render, weight=4),
    Stage("critique", _stage_critique, weight=2),
]


# ──────────────────────────────────── graph ───────────────────────────────────


def build_refine_graph(round_cap: int = 5) -> DecisionGraph:
    """generate/refine → critique → gate(passed) loop, with a human ship gate."""
    return DecisionGraph.from_dict({
        "start": "produce",
        "vars": {"round": 0, "passed": False, "score": 0.0},
        "meta": {"title": "viz auto-refine loop", "round_cap": round_cap},
        "nodes": [
            {"id": "produce", "data": {
                "label": "Generate + critique", "pos": {"x": 0, "y": 1},
                "action": {"kind": "pipeline", "ref": "viz-produce",
                           "pass_vars": ["prompt", "shape", "threshold", "rid", "issues", "calib_note"]},
                "emit": [
                    {"var": "passed", "from": "results.critique.passed"},
                    {"var": "score", "from": "results.critique.score"},
                    {"var": "issues", "from": "results.critique.issues"},
                    {"var": "summary", "from": "results.critique.summary"},
                    {"var": "rid", "from": "results.critique.rid"},
                    {"var": "round", "op": "inc"},
                ]},
             "transitions": [{"to": "gate", "kind": "choice", "label": "→"}]},
            {"id": "gate", "data": {"label": "Ship-ready?", "pos": {"x": 1, "y": 1}},
             "transitions": [
                 {"to": "review", "kind": "auto", "when": {"var": "passed", "op": "truthy"}},
                 {"to": "produce", "kind": "auto", "when": {"all": [
                     {"var": "passed", "op": "falsy"},
                     {"var": "round", "op": "<", "value": round_cap}]}},
                 {"to": "escalate", "kind": "auto"},
             ]},
            {"id": "review", "data": {"label": "Human review (see it)", "pos": {"x": 2, "y": 1}, "human": True},
             "transitions": [
                 {"to": "done", "kind": "choice", "label": "Ship"},
                 {"to": "produce", "kind": "choice", "label": "Reject"},
             ]},
            {"id": "done", "kind": "end", "data": {"label": "Shipped ✓", "pos": {"x": 3, "y": 1}}},
            {"id": "escalate", "kind": "end", "data": {"label": "Gave up — round cap",
                                                       "pos": {"x": 1, "y": 2}, "fail": True}},
        ],
    })


# ─────────────────────────── setup / dispatch / driver ─────────────────────────


def _refine_detail(view, last_result) -> str:
    crit = ((last_result or {}).get("results") or {}).get("critique") or {}
    if crit.get("summary"):
        return f"round {view.variables.get('round')}: {crit['summary']}"
    return ""


def _refine_setup(self: "VizApp") -> None:
    """Build the refine pipeline + run service. Call from VizApp.setup()."""
    self._refine_pipe = Pipeline(app=self, name="viz-produce",
                                 stages=VIZ_REFINE_STAGES, registry_kind="viz-produce")
    self._refine_round_cap = int(self.app_config("refine_round_cap", 5))
    self._refine_svc = GraphRunService(
        self, registry_kind="viz-refine",
        graph_factory=lambda spec: build_refine_graph(self._refine_round_cap),
        dispatch=self._refine_dispatch,
        public_vars=("round", "passed", "score", "rid"),
        detail_fn=_refine_detail,
        # the human gate reads the critique verdict, not the whole pipeline summary
        display_fn=lambda lr: (lr.get("results") or {}).get("critique"),
        paused_event="viz:refine_paused", done_event="viz:refine_done",
    )


async def _refine_dispatch(self: "VizApp", action: dict, variables: dict) -> dict:
    kind = (action or {}).get("kind", "noop")
    if kind == "noop":
        return {}
    inputs = collect_inputs(action, variables)
    if kind == "pipeline":
        if action.get("ref") != "viz-produce":
            raise GraphPipelineError(f"no pipeline {action.get('ref')!r}")
        return await self._refine_pipe.start(inputs)
    raise GraphPipelineError(f"unsupported action kind {kind!r} in viz refine")


# ──────────────────── HTTP (run lifecycle via GraphRunService) ─────────────────


@web_route("GET", "/api/refine/graph")
async def api_refine_graph(self: "VizApp", request):
    return build_refine_graph(self._refine_round_cap).to_dict()


@web_route("POST", "/api/refine/runs")
async def api_refine_start(self: "VizApp", request):
    body = await request.json()
    prompt = str((body or {}).get("prompt", "")).strip()
    if not prompt:
        return {"error": "prompt is required"}
    spec = {
        "prompt": prompt,
        "shape": (body.get("shape") or self.app_config("default_shape", "3d-scene")),
        "threshold": float(body.get("threshold") or 0.8),
    }
    run_id = self._refine_svc.start(spec, dict(spec))
    return {"run_id": run_id, "status": "running"}


@web_route("GET", "/api/refine/runs")
async def api_refine_list(self: "VizApp", request):
    return {"runs": self._refine_svc.list()}


@web_route("GET", "/api/refine/runs/{rid}")
async def api_refine_get(self: "VizApp", request):
    return self._refine_svc.get(request.path_params.get("rid", "")) or {"error": "not found"}


@web_route("POST", "/api/refine/runs/{rid}/decision")
async def api_refine_decision(self: "VizApp", request):
    rid = request.path_params.get("rid", "")
    body = await request.json()
    decision = (body.get("decision") or "").lower()
    if decision == "ship":
        return self._refine_svc.decide(rid, "done")
    if decision == "reject":
        note = (body.get("note") or "").strip() or "Not good enough yet — refine further."
        return self._refine_svc.decide(rid, "produce", calib_note=note)
    return {"error": "decision must be 'ship' or 'reject'"}
