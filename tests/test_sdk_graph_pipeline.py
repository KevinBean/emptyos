"""Tests for emptyos.sdk.graph_pipeline — the DecisionGraph↔Pipeline binding.

Pure: no daemon, no kernel. Runs with `python -m pytest tests/test_sdk_graph_pipeline.py`.

The worked example here is the **LLM-data-synthesiser closed loop** — the redraw
of the workflow diagram as a DecisionGraph. It exercises every shape the binding
adds: an action chain, an auto-gate diamond, an inner fix loop, a human
approve/reject branch, an outer calibration loop, a convergence guard, and
resume-from-human-pause.
"""

from __future__ import annotations

import pytest

from emptyos.sdk.decision_graph import DecisionGraph, validate_graph
from emptyos.sdk.graph_pipeline import (
    GraphPipelineError,
    GraphRunner,
    apply_emit,
    collect_inputs,
)


# ─────────────────────── the redraw: synthesiser workflow ──────────────────────


def synthesiser_workflow() -> DecisionGraph:
    """The diagram, as a DecisionGraph.

    learn → design → implement → generate ↘
                                            qc_gate ──passed──────────→ human_review
        auto_fix ←──── (!passed & round<max) ┘                          │  ├ approve → large_scale → done(end)
            ↑                          └── (round>=max) → escalate(end)  │  └ reject  → calibrate → auto_fix
            └──────────────────── generate (inner loop) ────────────────┘
    """
    # NB: DecisionGraph leaf conditions compare a var to a *literal*, not to
    # another var — so the round cap (5) is authored as a literal in the gate,
    # not as `round < max_rounds`. (See response note: a `{"var": ...}` value
    # resolver would be a small, clean enhancement to decision_graph if per-run
    # configurable caps become common.)
    return DecisionGraph.from_dict(
        {
            "start": "learn",
            "vars": {"round": 0, "qc_passed": False, "coverage": 0.0},
            "meta": {"title": "LLM data-synthesiser closed loop", "round_cap": 5},
            "nodes": [
                # ── prep chain (stages 0–2) — action nodes, one edge each ──
                {"id": "learn", "data": {"label": "0. Project learning",
                                         "action": {"kind": "verb", "ref": "synth.learn"}},
                 "transitions": [{"to": "design", "kind": "choice", "label": "→"}]},
                {"id": "design", "data": {"label": "1. Design",
                                          "action": {"kind": "verb", "ref": "synth.design"}},
                 "transitions": [{"to": "implement", "kind": "choice", "label": "→"}]},
                {"id": "implement", "data": {"label": "2. Implementation",
                                             "action": {"kind": "verb", "ref": "synth.implement"}},
                 "transitions": [{"to": "generate", "kind": "choice", "label": "→"}]},

                # ── 3.1 + 3.2: small-batch synth + QC, as a pipeline ──
                {"id": "generate", "data": {
                    "label": "3. Small-scale synth + automated QC",
                    "action": {"kind": "pipeline", "ref": "synth-qc", "pass_vars": ["round"]},
                    "emit": [
                        {"var": "qc_passed", "from": "results.aggregate.passed"},
                        {"var": "coverage", "from": "results.aggregate.coverage"},
                        {"var": "round", "op": "inc"},
                    ]},
                 "transitions": [{"to": "qc_gate", "kind": "choice", "label": "→"}]},

                # ── the 质检通过? diamond — pure auto-gate, never landed on ──
                {"id": "qc_gate", "transitions": [
                    {"to": "human_review", "kind": "auto",
                     "when": {"var": "qc_passed", "op": "truthy"}},
                    {"to": "auto_fix", "kind": "auto", "when": {"all": [
                        {"var": "qc_passed", "op": "falsy"},
                        {"var": "round", "op": "<", "value": 5}]}},
                    {"to": "escalate", "kind": "auto"},  # round>=cap: did not converge
                ]},

                # ── 3.3 LLM fix — action node, loops back to generate ──
                {"id": "auto_fix", "data": {
                    "label": "3.3 LLM fix & adjust",
                    "action": {"kind": "verb", "ref": "synth.fix", "pass_vars": ["round"]}},
                 "transitions": [{"to": "generate", "kind": "choice", "label": "→"}]},

                # ── human review — the final gate, judgment stays human ──
                {"id": "human_review", "data": {
                    "label": "Human review", "human": True},
                 "transitions": [
                     {"to": "large_scale", "kind": "choice", "label": "Approve"},
                     {"to": "calibrate", "kind": "choice", "label": "Reject"},
                 ]},

                # ── human calibration — outer loop back through fix ──
                {"id": "calibrate", "data": {
                    "label": "Re-adjust direction (human calibration)",
                    "action": {"kind": "verb", "ref": "synth.calibrate"}},
                 "transitions": [{"to": "auto_fix", "kind": "choice", "label": "→"}]},

                # ── 4. large-scale generation — pipeline, then done ──
                {"id": "large_scale", "data": {
                    "label": "4. Large-scale generation / release",
                    "action": {"kind": "pipeline", "ref": "large-scale"}},
                 "transitions": [{"to": "done", "kind": "choice", "label": "→"}]},

                {"id": "done", "kind": "end", "data": {"label": "Released"}},
                {"id": "escalate", "kind": "end",
                 "data": {"label": "Escalate — did not converge"}},
            ],
        }
    )


# ───────────────────────────── structural validity ────────────────────────────


def test_graph_is_structurally_valid():
    g = synthesiser_workflow()
    errors = [e for e in validate_graph(g) if not e.startswith("warn:")]
    assert errors == [], errors


def test_graph_round_trips_json():
    g = synthesiser_workflow()
    again = DecisionGraph.from_dict(g.to_dict())
    assert again.start == g.start
    assert set(again.nodes) == set(g.nodes)


# ─────────────────────────────── emit folding ─────────────────────────────────


def test_apply_emit_reads_dotted_path():
    v: dict = {}
    apply_emit([{"var": "ok", "from": "results.aggregate.passed"}],
               {"results": {"aggregate": {"passed": True}}}, v)
    assert v["ok"] is True


def test_apply_emit_missing_path_is_none():
    v: dict = {}
    apply_emit([{"var": "ok", "from": "a.b.c"}], {"a": {}}, v)
    assert v["ok"] is None


def test_apply_emit_reuses_mutation_for_counters():
    v = {"round": 2}
    apply_emit([{"var": "round", "op": "inc"}], {}, v)
    assert v["round"] == 3


def test_collect_inputs_merges_static_and_passvars():
    out = collect_inputs({"inputs": {"a": 1}, "pass_vars": ["round", "missing"]},
                         {"round": 4})
    assert out == {"a": 1, "round": 4}


# ────────────────────────── driving the workflow ──────────────────────────────


def _dispatch_factory(passes_on_round: int):
    """Fake dispatch: the QC pipeline returns passed=True once `round` reaches
    `passes_on_round`. Verbs/large-scale are no-op success."""
    calls: list[tuple[str, dict]] = []

    async def dispatch(action: dict, variables: dict) -> dict:
        kind = action.get("kind")
        ref = action.get("ref", "")
        calls.append((ref, dict(variables)))
        if kind == "pipeline" and ref == "synth-qc":
            cur_round = variables.get("round", 0)
            passed = cur_round >= passes_on_round
            return {"status": "complete",
                    "results": {"aggregate": {"passed": passed, "coverage": 0.7 if passed else 0.3}}}
        if kind == "pipeline" and ref == "large-scale":
            return {"status": "complete", "results": {"final": {"rows": 200_000}}}
        return {"ok": True}

    return dispatch, calls


@pytest.mark.asyncio
async def test_inner_loop_then_human_pause_then_approve():
    g = synthesiser_workflow()
    # QC passes once round reaches 2 → generate runs at round 0 (→1) fail,
    # round 1 (→2) ... the check is on round BEFORE inc, so passes_on_round=1
    # means: first generate sees round 0 (fail), fix, second sees round 1 (pass).
    dispatch, calls = _dispatch_factory(passes_on_round=1)
    runner = GraphRunner(g, dispatch=dispatch)

    step = await runner.run()
    assert step.status == "human"
    assert step.node_id == "human_review"
    # one fix happened (round 0 failed), then round 1 passed
    refs = [c[0] for c in calls]
    assert refs.count("synth.fix") == 1
    assert refs.count("synth-qc") == 2
    assert step.last_result["results"]["aggregate"]["passed"] is True
    # human approves
    final = await runner.resume(step.state, choice="large_scale")
    assert final.status == "ended"
    assert final.node_id == "done"
    assert "large_scale" in final.history


@pytest.mark.asyncio
async def test_human_reject_runs_calibration_outer_loop():
    g = synthesiser_workflow()
    dispatch, calls = _dispatch_factory(passes_on_round=0)  # passes immediately
    runner = GraphRunner(g, dispatch=dispatch)

    step = await runner.run()
    assert step.status == "human"
    calls.clear()
    # reject → calibrate → auto_fix → generate (passes again) → human again
    step2 = await runner.resume(step.state, choice="calibrate")
    assert step2.status == "human"
    refs = [c[0] for c in calls]
    assert "synth.calibrate" in refs
    assert "synth.fix" in refs
    assert "synth-qc" in refs
    # now approve
    final = await runner.resume(step2.state, choice="large_scale")
    assert final.status == "ended"
    assert final.node_id == "done"


@pytest.mark.asyncio
async def test_convergence_guard_escalates_when_qc_never_passes():
    g = synthesiser_workflow()
    dispatch, calls = _dispatch_factory(passes_on_round=999)  # never passes
    runner = GraphRunner(g, dispatch=dispatch)

    step = await runner.run()
    assert step.status == "ended"
    assert step.node_id == "escalate"
    # generate ran exactly max_rounds(5) times before the gate gave up
    assert [c[0] for c in calls].count("synth-qc") == 5


@pytest.mark.asyncio
async def test_action_node_with_two_eligible_choices_is_rejected():
    """An action node must route via a gate, not branch itself."""
    g = DecisionGraph.from_dict({
        "start": "a",
        "nodes": [
            {"id": "a", "data": {"action": {"kind": "noop"}},
             "transitions": [
                 {"to": "b", "kind": "choice", "label": "x"},
                 {"to": "c", "kind": "choice", "label": "y"}]},
            {"id": "b", "kind": "end"},
            {"id": "c", "kind": "end"},
        ],
    })

    async def dispatch(action, variables):
        return {}

    runner = GraphRunner(g, dispatch=dispatch)
    with pytest.raises(GraphPipelineError, match="exactly one eligible choice"):
        await runner.run()


@pytest.mark.asyncio
async def test_runaway_loop_is_capped():
    """A gate that always routes back to an action with no convergence guard."""
    g = DecisionGraph.from_dict({
        "start": "spin",
        "nodes": [
            {"id": "spin", "data": {"action": {"kind": "noop"}},
             "transitions": [{"to": "gate", "kind": "choice", "label": "→"}]},
            {"id": "gate", "transitions": [{"to": "spin", "kind": "auto"}]},
        ],
    })

    async def dispatch(action, variables):
        return {}

    runner = GraphRunner(g, dispatch=dispatch, max_steps=20)
    with pytest.raises(GraphPipelineError, match="exceeded 20 steps"):
        await runner.run()
