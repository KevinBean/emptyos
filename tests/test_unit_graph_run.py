"""Offline tests for emptyos.sdk.graph_run.GraphRunService.

Proves the run-driver contract that synth + viz + workflows depend on: start →
persist-each-step → pause at a human node → resume via decide → end, plus the
public-var projection, the trail detail, and JSON-coercion of a non-serializable
dispatch result. Uses the real RunRegistry over a tmp dir (pure file I/O, no
kernel) + a fake app.
"""

from __future__ import annotations

import asyncio

import pytest

from emptyos.sdk.decision_graph import DecisionGraph
from emptyos.sdk.graph_run import GraphRunService
from emptyos.sdk.run_registry import RunRegistry

_GRAPH = {
    "start": "act",
    "vars": {"n": 0},
    "nodes": [
        {"id": "act", "data": {"label": "Act", "action": {"kind": "verb", "ref": "x.y"},
                               "emit": [{"var": "n", "op": "inc"}]},
         "transitions": [{"to": "review", "kind": "choice", "label": "→"}]},
        {"id": "review", "data": {"label": "Review", "human": True},
         "transitions": [{"to": "done", "kind": "choice", "label": "Done"},
                         {"to": "act", "kind": "choice", "label": "Again"}]},
        {"id": "done", "kind": "end", "data": {"label": "Done"}},
    ],
}


class _Weird:                       # deliberately not JSON-serializable
    def __str__(self): return "weird-obj"


class _App:
    def __init__(self, tmp):
        self._tmp = tmp
        self._regs = {}
        self.emitted = []

    def runs(self, kind):
        if kind not in self._regs:
            self._regs[kind] = RunRegistry(self._tmp / kind)
        return self._regs[kind]

    async def emit(self, ev, payload):
        self.emitted.append((ev, payload))

    def log_warn(self, *a):
        pass


async def _settle(svc):
    """Let the background drive task(s) finish."""
    while svc._tasks:
        await asyncio.gather(*list(svc._tasks))


def _svc(app):
    return GraphRunService(
        app, registry_kind="t",
        graph_factory=lambda spec: DecisionGraph.from_dict(_GRAPH),
        dispatch=_dispatch,
        public_vars=("n",),
        detail_fn=lambda view, lr: f"at {view.node_id}",
        paused_event="t:paused", done_event="t:done",
    )


async def _dispatch(action, variables):
    # returns a non-jsonable object → exercises GraphRunService._jsonable coercion
    return {"obj": _Weird(), "ok": True}


@pytest.mark.asyncio
async def test_start_runs_to_human_pause(tmp_path):
    app = _App(tmp_path)
    svc = _svc(app)
    rid = svc.start({"title": "demo"}, {})
    await _settle(svc)
    st = svc.get(rid)
    assert st["status"] == "human" and st["current"] == "review"
    assert st["vars"] == {"n": 1}                       # public-var projection + emit
    assert "graph_state" not in st                      # internal plumbing hidden from the wire
    # non-serializable dispatch result coerced, not crashed
    assert st["last_result"]["obj"] == "weird-obj"
    assert any(s["detail"] == "at act" for s in st["log"])
    assert ("t:paused", {"run_id": rid, "node": "review"}) in app.emitted


@pytest.mark.asyncio
async def test_decide_resumes_to_end(tmp_path):
    app = _App(tmp_path)
    svc = _svc(app)
    rid = svc.start({"title": "demo"}, {})
    await _settle(svc)
    out = svc.decide(rid, "done")
    assert out.get("ok") is True
    await _settle(svc)
    st = svc.get(rid)
    assert st["status"] == "ended" and st["current"] == "done"
    assert ("t:done", {"run_id": rid, "node": "done"}) in app.emitted


@pytest.mark.asyncio
async def test_decide_loop_back_reruns_action(tmp_path):
    app = _App(tmp_path)
    svc = _svc(app)
    rid = svc.start({"title": "demo"}, {})
    await _settle(svc)
    svc.decide(rid, "act")                              # "Again" → loop through act
    await _settle(svc)
    st = svc.get(rid)
    assert st["status"] == "human" and st["vars"]["n"] == 2   # act ran a 2nd time


@pytest.mark.asyncio
async def test_decide_rejects_when_not_human(tmp_path):
    app = _App(tmp_path)
    svc = _svc(app)
    rid = svc.start({"title": "demo"}, {})
    await _settle(svc)
    svc.decide(rid, "done")
    await _settle(svc)
    assert svc.decide(rid, "done")["error"].startswith("run is")  # already ended


def test_list_and_get_missing(tmp_path):
    app = _App(tmp_path)
    svc = _svc(app)
    assert svc.get("nope") is None
    assert svc.list() == []
