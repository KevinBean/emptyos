"""Unit tests for the synth app's real pipeline stages + graph/emit consistency.

Offline: no daemon, no LLM. Loads workflow.py by path (it has no relative runtime
imports — `.app` is TYPE_CHECKING only), fakes `StageContext`/`app.think`, and
exercises the deterministic stages plus the synth/agent stages with canned think
output. The load-bearing guard is `test_generate_emit_paths_match_aggregate`: it
ties the graph's `emit: {from: results.aggregate.X}` paths to the actual keys
`_stage_aggregate` returns, catching drift between graph authoring and stage code.
"""

from __future__ import annotations

import importlib.util
import json
import os

import pytest

from emptyos.sdk.pipeline import StageContext

_WF_PATH = os.path.join(os.path.dirname(__file__), "..", "apps", "public", "labs", "synth", "workflow.py")
_spec = importlib.util.spec_from_file_location("synth_workflow_under_test", _WF_PATH)
wf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wf)


class _FakeHandle:
    def __init__(self, tmp):
        self.dir = tmp

    def write_artifact(self, name, content):
        p = self.dir / name
        p.write_text(content if isinstance(content, str) else content.decode(), encoding="utf-8")
        return p


class _FakeApp:
    """Minimal app surface the stages/verbs touch: think + app_config."""

    def __init__(self, think_returns):
        self._think_returns = list(think_returns)
        self.think_calls = []

    async def think(self, prompt, domain=None, system=None, **kw):
        self.think_calls.append({"prompt": prompt, "domain": domain})
        return self._think_returns.pop(0) if self._think_returns else "[]"

    def app_config(self, key, default=None):
        return default


def _ctx(app, tmp, inputs, results=None):
    return StageContext(app=app, handle=_FakeHandle(tmp), inputs=inputs,
                        results=results or {}, stage="t", base_pct=0, span_pct=10, raw_progress=None)


# ─────────────────────────── deterministic stages ─────────────────────────────


@pytest.mark.asyncio
async def test_rule_check_flags_missing_and_dups(tmp_path):
    rows = [
        {"q": "a", "ans": "1"},        # valid
        {"q": "b"},                    # missing 'ans'
        {"q": "a", "ans": "1"},        # exact duplicate of row 0
    ]
    app = _FakeApp([])
    ctx = _ctx(app, tmp_path, {"spec": {"schema": {"q": "string", "ans": "string"}}},
               results={"synthesise": {"rows": rows}})
    out = await wf._stage_rule_check(ctx)
    assert out["total"] == 3
    assert out["valid_count"] == 2          # rows 0 and 2 have all fields
    assert out["dup_count"] == 1            # row 2 duplicates row 0
    kinds = {v["kind"] for v in out["violations"]}
    assert "missing_fields" in kinds and "duplicate" in kinds


@pytest.mark.asyncio
async def test_aggregate_pass_and_fail(tmp_path):
    app = _FakeApp([])
    spec = {"spec": {"threshold": 0.8, "small_n": 4}}
    # PASS: 4/4 valid, no dups, no review issues
    ctx = _ctx(app, tmp_path, spec, results={
        "synthesise": {"rows": [{"x": i} for i in range(4)]},
        "rule_check": {"total": 4, "valid_count": 4, "dup_count": 0, "violations": []},
        "agent_review": {"issue_count": 0, "issues": []},
    })
    passed = await wf._stage_aggregate(ctx)
    assert passed["passed"] is True
    assert passed["coverage"] == 1.0
    assert "PASS" in passed["summary"]

    # FAIL: only 2/4 valid → ratio 0.5 < 0.8
    ctx2 = _ctx(app, tmp_path, spec, results={
        "synthesise": {"rows": [{"x": i} for i in range(4)]},
        "rule_check": {"total": 4, "valid_count": 2, "dup_count": 0, "violations": []},
        "agent_review": {"issue_count": 0, "issues": []},
    })
    failed = await wf._stage_aggregate(ctx2)
    assert failed["passed"] is False


# ───────────────────────────── LLM-shaped stages ──────────────────────────────


@pytest.mark.asyncio
async def test_synthesise_parses_rows_from_think(tmp_path):
    canned = json.dumps([{"q": "Capital of France?", "a": "Paris"},
                         {"q": "Capital of Japan?", "a": "Tokyo"}])
    app = _FakeApp([canned])
    ctx = _ctx(app, tmp_path, {"spec": {"schema": {"q": "string", "a": "string"}, "small_n": 2},
                               "gen_instruction": "trivia"})
    out = await wf._stage_synthesise(ctx)
    assert out["count"] == 2
    assert out["rows"][0]["a"] == "Paris"
    assert (tmp_path / "batch.jsonl").exists()


@pytest.mark.asyncio
async def test_agent_review_collects_issues(tmp_path):
    review = json.dumps({"issues": [{"row": 0, "problem": "off-topic"}], "notes": "ok"})
    app = _FakeApp([review])
    ctx = _ctx(app, tmp_path, {"spec": {"description": "trivia"}},
               results={"synthesise": {"rows": [{"q": "x", "a": "y"}]}})
    out = await wf._stage_agent_review(ctx)
    assert out["issue_count"] == 1
    assert out["issues"][0]["problem"] == "off-topic"


@pytest.mark.asyncio
async def test_design_verb_returns_instruction(tmp_path):
    app = _FakeApp(["Generate diverse trivia rows, vary difficulty."])
    out = await wf.design(app, spec={"description": "trivia", "schema": {"q": "string"}})
    assert "gen_instruction" in out
    assert out["gen_instruction"].startswith("Generate diverse")


# ─────────────────── the load-bearing consistency guard ───────────────────────


@pytest.mark.asyncio
async def test_generate_emit_paths_match_aggregate(tmp_path):
    """Every `generate` emit `from: results.aggregate.X` must name a real key that
    `_stage_aggregate` returns — otherwise the gate vars silently go None."""
    graph = wf.build_synth_graph()
    gen = graph.nodes["generate"]
    emit_keys = set()
    for rule in gen.data.get("emit", []):
        src = rule.get("from")
        if src and src.startswith("results.aggregate."):
            emit_keys.add(src.split(".", 2)[2])

    app = _FakeApp([])
    ctx = _ctx(app, tmp_path, {"spec": {"threshold": 0.8, "small_n": 1}}, results={
        "synthesise": {"rows": [{"x": 1}]},
        "rule_check": {"total": 1, "valid_count": 1, "dup_count": 0, "violations": []},
        "agent_review": {"issue_count": 0, "issues": []},
    })
    agg = await wf._stage_aggregate(ctx)
    missing = emit_keys - set(agg.keys())
    assert not missing, f"generate emits unknown aggregate keys: {missing}"
    # and the gate var (qc_passed) is sourced from a key aggregate actually sets
    assert "passed" in agg and "coverage" in agg
