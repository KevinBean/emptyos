"""Offline unit tests for the generic Workflows app.

No daemon. Loads defs.py by path; fakes the kernel verb-registry + call_app to
prove (a) the eligibility gate refuses non-reversible verbs — the north-star
safety rule — and (b) the template graph drives end-to-end through the human
branch via GraphRunner.
"""

from __future__ import annotations

import importlib.util
import os

import pytest

from emptyos.sdk.decision_graph import DecisionGraph, validate_graph
from emptyos.sdk.graph_pipeline import GraphPipelineError, GraphRunner

_PATH = os.path.join(os.path.dirname(__file__), "..", "apps", "public", "labs", "workflows", "defs.py")
_spec = importlib.util.spec_from_file_location("workflows_defs_under_test", _PATH)
defs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(defs)


# ── fakes ──

class _Entry:
    def __init__(self, app_id, method, eligibility):
        self.app_id, self.method, self.eligibility = app_id, method, eligibility
        self.verb = f"{app_id}.{method}"


class _Reg:
    def __init__(self):
        self._m = {"task.add": _Entry("task", "add", "stable"),
                   "publish.deploy": _Entry("publish", "deploy", "gated")}

    def get(self, v):
        return self._m.get(v)

    def eligible_verbs(self):
        return {v for v, e in self._m.items() if e.eligibility == "stable"}

    def menu(self):
        return [{"verb": v, "app": e.app_id, "method": e.method, "summary": "",
                 "eligibility": e.eligibility, "surfaces": []} for v, e in self._m.items()]


class _Apps:
    def __init__(self): self._r = _Reg()
    def get_verbs(self): return self._r


class _Kernel:
    def __init__(self): self.apps = _Apps()
    def autopilot_eligible_set(self): return None   # force fallback to registry.eligible_verbs()


class _App:
    def __init__(self):
        self.kernel = _Kernel()
        self.calls = []

    async def call_app(self, app_id, method, **kw):
        self.calls.append((app_id, method, kw))
        return {"ok": True}


# ── eligibility gate ──

def test_eligible_verbs_falls_back_to_registry():
    assert defs._eligible_verbs(_App()) == {"task.add"}


@pytest.mark.asyncio
async def test_dispatch_runs_stable_verb():
    app = _App()
    out = await defs._wf_dispatch(app, {"kind": "verb", "ref": "task.add", "pass_vars": ["text"]}, {"text": "hi"})
    assert out == {"ok": True}
    assert app.calls == [("task", "add", {"text": "hi"})]


@pytest.mark.asyncio
async def test_dispatch_refuses_gated_verb():
    app = _App()
    with pytest.raises(GraphPipelineError, match="not reversible"):
        await defs._wf_dispatch(app, {"kind": "verb", "ref": "publish.deploy"}, {})
    assert app.calls == []        # never called the irreversible verb


@pytest.mark.asyncio
async def test_dispatch_rejects_unknown_verb_and_pipeline_kind():
    app = _App()
    with pytest.raises(GraphPipelineError, match="unknown verb"):
        await defs._wf_dispatch(app, {"kind": "verb", "ref": "nope.x"}, {})
    with pytest.raises(GraphPipelineError, match="verb'/'noop"):
        await defs._wf_dispatch(app, {"kind": "pipeline", "ref": "x"}, {})


@pytest.mark.asyncio
async def test_dispatch_noop_is_empty():
    assert await defs._wf_dispatch(_App(), {"kind": "noop"}, {}) == {}


# ── path-traversal guard (security review) ──

class _StoreApp:
    def __init__(self, root):
        self.data_dir = root

    def data_subdir(self, *parts):
        p = self.data_dir.joinpath(*parts)
        p.mkdir(parents=True, exist_ok=True)
        return p


def test_safe_wid_allows_good_rejects_bad():
    for ok in ("capture-triage", "my_wf123", "a"):
        assert defs._safe_wid(ok), ok
    for bad in ("../etc", "a/b", "..", "a.b", "", "x" * 65, "../../x", "a\\b", "/abs", ".hidden"):
        assert not defs._safe_wid(bad), bad


def test_def_path_blocks_traversal(tmp_path):
    app = _StoreApp(tmp_path)
    assert defs._def_path(app, "ok-1") is not None
    for bad in ("../../secret", "a/b", "..", "x/../../y", "a\\b"):
        assert defs._def_path(app, bad) is None, bad


def test_store_ops_refuse_unsafe_wid(tmp_path):
    app = _StoreApp(tmp_path)
    with pytest.raises(ValueError):
        defs._save_def(app, "../evil", {"start": "a", "nodes": [{"id": "a", "kind": "end"}]})
    assert defs._load_def(app, "../evil") is None
    assert defs._delete_def(app, "../evil") is False
    # the matching traversal file was never created
    assert not (tmp_path / "evil.json").exists()
    # a safe id still round-trips
    defs._save_def(app, "good", {"start": "a", "nodes": [{"id": "a", "kind": "end"}]})
    assert defs._load_def(app, "good")["id"] == "good"


# ── template validity + validate_def ──

def test_template_is_valid():
    g = DecisionGraph.from_dict(defs.TEMPLATE_CAPTURE_TRIAGE)
    assert [e for e in validate_graph(g) if not e.startswith("warn:")] == []


def test_validate_def_warns_on_non_eligible_verb():
    bad = {"id": "x", "start": "a", "nodes": [
        {"id": "a", "data": {"action": {"kind": "verb", "ref": "publish.deploy"}},
         "transitions": [{"to": "end", "kind": "choice"}]},
        {"id": "end", "kind": "end"}]}
    res = defs.validate_def(bad, {"task.add"})
    assert res["ok"] is True   # structurally fine
    assert any("not reversible" in w for w in res["warnings"])


# ── full drive of the template ──

@pytest.mark.asyncio
async def test_template_drives_through_human_branch():
    app = _App()
    graph = DecisionGraph.from_dict(defs.TEMPLATE_CAPTURE_TRIAGE)
    runner = GraphRunner(graph, dispatch=lambda a, v: defs._wf_dispatch(app, a, v))

    step = await runner.run({"text": "buy milk"})
    assert step.status == "human" and step.node_id == "confirm"
    assert app.calls == [("task", "add", {"text": "buy milk"})]   # first task added

    # choose "Yes" → followup adds a second task → done
    final = await runner.resume(step.state, choice="followup")
    assert final.status == "ended" and final.node_id == "done"
    assert app.calls[-1] == ("task", "add", {"text": "Follow up on the captured item"})


@pytest.mark.asyncio
async def test_template_no_branch_skips_followup():
    app = _App()
    graph = DecisionGraph.from_dict(defs.TEMPLATE_CAPTURE_TRIAGE)
    runner = GraphRunner(graph, dispatch=lambda a, v: defs._wf_dispatch(app, a, v))
    step = await runner.run({"text": "x"})
    final = await runner.resume(step.state, choice="done")
    assert final.node_id == "done"
    assert len(app.calls) == 1    # only the first task, no follow-up
