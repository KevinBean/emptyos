"""Offline unit tests for viz's auto-refine loop (graph_pipeline driver #2).

No daemon, no LLM. Loads refine.py by path (absolute sdk imports only), fakes the
StageContext/app, and drives build_refine_graph with a fake dispatch to prove the
generate→critique→gate loop, the human ship/reject branch, and the convergence cap.
"""

from __future__ import annotations

import importlib.util
import os

import pytest

from emptyos.sdk.decision_graph import validate_graph
from emptyos.sdk.graph_pipeline import GraphRunner
from emptyos.sdk.pipeline import StageContext

_PATH = os.path.join(os.path.dirname(__file__), "..", "apps", "public", "standard", "viz", "refine.py")
_spec = importlib.util.spec_from_file_location("viz_refine_under_test", _PATH)
rf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rf)


# ───────────────────────────── graph + helpers ────────────────────────────────


def test_graph_valid_and_shaped():
    g = rf.build_refine_graph(5)
    assert [e for e in validate_graph(g) if not e.startswith("warn:")] == []
    assert set(g.nodes) == {"produce", "gate", "review", "done", "escalate"}
    gate = g.nodes["gate"]
    assert {t.to for t in gate.transitions} == {"review", "produce", "escalate"}
    assert all(t.kind == "auto" for t in gate.transitions)
    assert g.nodes["review"].data.get("human") is True


def test_build_tweak_merges_issues_and_note():
    t = rf._build_tweak([{"problem": "orbits invisible"}, {"problem": "no labels"}], "make it dark")
    assert "orbits invisible" in t and "no labels" in t and "make it dark" in t


def test_emit_paths_match_critique_output_keys():
    """produce's emit `from: results.critique.X` must name real critique keys."""
    g = rf.build_refine_graph()
    emit = {r["from"].split(".", 2)[2] for r in g.nodes["produce"].data["emit"]
            if r.get("from", "").startswith("results.critique.")}
    assert emit <= {"passed", "score", "rid", "issues", "summary", "html_path"}


# ───────────────────────────── critique stage ─────────────────────────────────


class _App:
    def __init__(self, think_returns, vault_root):
        self._r = list(think_returns)
        self.vault_root = vault_root

    async def think(self, prompt, domain=None, system=None, **k):
        return self._r.pop(0) if self._r else "{}"

    def app_config(self, k, d=None):
        return d


def _ctx(app, tmp, inputs, results):
    return StageContext(app=app, handle=None, inputs=inputs, results=results,
                        stage="critique", base_pct=0, span_pct=10, raw_progress=None)


@pytest.mark.asyncio
async def test_critique_passes_above_threshold(tmp_path):
    app = _App(['{"score": 0.9, "issues": [], "summary": "great"}'], tmp_path)
    ctx = _ctx(app, tmp_path, {"prompt": "x", "threshold": 0.8},
               {"render": {"ok": True, "rid": "viz-1", "html_path": "missing.html", "shape": "chart"}})
    out = await rf._stage_critique(ctx)
    assert out["passed"] is True and out["score"] == 0.9 and out["rid"] == "viz-1"


@pytest.mark.asyncio
async def test_critique_fails_below_threshold(tmp_path):
    app = _App(['{"score": 0.5, "issues": [{"problem": "wrong colors"}], "summary": "meh"}'], tmp_path)
    ctx = _ctx(app, tmp_path, {"prompt": "x", "threshold": 0.8},
               {"render": {"ok": True, "rid": "viz-1", "html_path": "missing.html"}})
    out = await rf._stage_critique(ctx)
    assert out["passed"] is False and out["issues"][0]["problem"] == "wrong colors"


@pytest.mark.asyncio
async def test_critique_scores_zero_on_failed_render(tmp_path):
    app = _App([], tmp_path)  # think never called
    ctx = _ctx(app, tmp_path, {"prompt": "x", "threshold": 0.8},
               {"render": {"ok": False, "error": "rejected", "rid": ""}})
    out = await rf._stage_critique(ctx)
    assert out["passed"] is False and out["score"] == 0.0


# ─────────────────────── driving the loop (fake dispatch) ──────────────────────


def _dispatch_factory(passes_on_round: int):
    calls = []

    async def dispatch(action, variables):
        calls.append(dict(variables))
        rnd = variables.get("round", 0)
        passed = rnd >= passes_on_round
        return {"results": {"critique": {
            "passed": passed, "score": 0.9 if passed else 0.4, "rid": "viz-1",
            "issues": [] if passed else [{"problem": "needs work"}],
            "summary": "ship" if passed else "refine"}}}
    return dispatch, calls


@pytest.mark.asyncio
async def test_loop_refines_then_human_then_ship():
    dispatch, calls = _dispatch_factory(passes_on_round=1)  # 1st produce fails, 2nd passes
    runner = GraphRunner(rf.build_refine_graph(5), dispatch=dispatch)
    step = await runner.run({"prompt": "p", "shape": "chart", "threshold": 0.8})
    assert step.status == "human" and step.node_id == "review"
    assert len(calls) == 2                      # produce ran twice (one refine)
    assert step.variables["passed"] is True
    final = await runner.resume(step.state, choice="done")
    assert final.status == "ended" and final.node_id == "done"


@pytest.mark.asyncio
async def test_human_reject_loops_back_to_produce():
    dispatch, calls = _dispatch_factory(passes_on_round=0)  # always ship-ready
    runner = GraphRunner(rf.build_refine_graph(5), dispatch=dispatch)
    step = await runner.run({"prompt": "p", "threshold": 0.8})
    assert step.status == "human"
    calls.clear()
    step2 = await runner.resume(step.state, choice="produce")  # reject → refine
    assert step2.status == "human" and len(calls) == 1
    final = await runner.resume(step2.state, choice="done")
    assert final.node_id == "done"


@pytest.mark.asyncio
async def test_convergence_cap_escalates():
    dispatch, calls = _dispatch_factory(passes_on_round=999)  # never passes
    runner = GraphRunner(rf.build_refine_graph(5), dispatch=dispatch)
    step = await runner.run({"prompt": "p", "threshold": 0.8})
    assert step.status == "ended" and step.node_id == "escalate"
    assert len(calls) == 5                       # produce ran exactly round_cap times
