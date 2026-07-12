"""GraphRunService — background run lifecycle for a GraphRunner-driven workflow.

Extracts the start → persist-each-step → resume-at-human → decide driver that
synth (`apps/public/labs/synth`) and viz refine (`apps/public/standard/viz/refine.py`)
each hand-rolled. An app supplies a graph factory + a dispatch + a few small
projectors; the service owns minting a run in a ``RunRegistry``, spawning the
background drive, persisting the step-by-step state the UI polls (current node,
public vars, a trail with per-step detail, the human-gate ``last_result``), and
resuming at human pauses.

Third consumer of `emptyos.sdk.graph_pipeline` (synth + viz are the latent two it
generalises; both are candidates to migrate onto this). Keep it pure-ish: the only
I/O is via the app's own ``runs()``/``emit()`` + the injected dispatch.

Usage::

    self._svc = GraphRunService(
        self, registry_kind="myapp-runs",
        graph_factory=lambda spec: build_graph(),     # spec-aware: per-run graph
        dispatch=self._dispatch,                       # async (action, vars) -> dict
        public_vars=("round", "passed"),
        detail_fn=lambda view, lr: ...,                # trail line per step
        display_fn=lambda lr: (lr.get("results") or {}).get("aggregate"),  # gate payload
    )
    run_id = self._svc.start({"title": "..."}, {"spec": spec})   # from POST /runs
    self._svc.get(run_id) / self._svc.list() / self._svc.decide(run_id, choice, note=...)
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from emptyos.sdk.graph_pipeline import GraphRunner


def _jsonable(value: Any) -> Any:
    """Coerce a dispatch result to something ``json.dumps`` accepts before it
    lands in run state. A verb's ``call_app`` may return a domain object (e.g. a
    ``Task``); ``default=str`` turns those into strings rather than crashing the
    run on ``write_state``."""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return str(value)


class GraphRunService:
    def __init__(
        self,
        app: Any,
        *,
        registry_kind: str,
        graph_factory: Callable[[dict], Any],
        dispatch: Callable[[dict, dict], Awaitable[dict]],
        public_vars: tuple[str, ...] = (),
        detail_fn: Callable[[Any, dict | None], str] | None = None,
        display_fn: Callable[[dict], Any] | None = None,
        calib_var: str = "calib_note",
        paused_event: str | None = None,
        done_event: str | None = None,
    ):
        self.app = app
        self.kind = registry_kind
        self.graph_factory = graph_factory      # (spec) -> DecisionGraph
        self.dispatch = dispatch
        self.public_vars = tuple(public_vars)
        self.detail_fn = detail_fn
        self.display_fn = display_fn
        self.calib_var = calib_var
        self.paused_event = paused_event
        self.done_event = done_event
        self._tasks: set[asyncio.Task] = set()

    # -- helpers --

    def _pub(self, variables: dict) -> dict:
        return {k: variables.get(k) for k in self.public_vars if k in variables}

    def _display(self, last_result):
        if last_result is None:
            return None
        val = last_result
        if self.display_fn is not None:
            try:
                val = self.display_fn(last_result) or last_result
            except Exception:
                val = last_result
        return _jsonable(val)   # verb results may be domain objects (e.g. Task)

    def _spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    # -- public lifecycle --

    def start(self, spec: dict, start_vars: dict | None = None) -> str:
        graph = self.graph_factory(spec)
        handle = self.app.runs(self.kind).new()
        handle.write_state({
            "status": "running", "spec": spec, "current": graph.start,
            "node_label": "starting", "vars": {}, "log": [], "history": [],
        })
        self._spawn(self._drive(handle.run_id, start_vars=start_vars))
        return handle.run_id

    def get(self, run_id: str) -> dict | None:
        h = self.app.runs(self.kind).get(run_id)
        st = h.read_state() if h else None
        if not st:
            return None
        return {k: v for k, v in st.items() if k != "graph_state"} | {"run_id": run_id}

    def list(self, n: int = 30) -> list[dict]:
        out = []
        for h, st in self.app.runs(self.kind).recent_states(n):
            spec = st.get("spec") or {}
            out.append({
                "run_id": h.run_id, "status": st.get("status"), "current": st.get("current"),
                "title": spec.get("title") or spec.get("prompt") or spec.get("workflow_id") or h.run_id,
            })
        return out

    def decide(self, run_id: str, choice, *, calib_note: str | None = None) -> dict:
        h = self.app.runs(self.kind).get(run_id)
        st = h.read_state() if h else None
        if not st:
            return {"error": "not found"}
        if st.get("status") != "human":
            return {"error": f"run is {st.get('status')}, not awaiting a decision"}
        self._spawn(self._drive(run_id, resume_choice=choice, calib_note=calib_note))
        st["status"] = "running"
        h.write_state(st)
        return {"ok": True, "status": "running"}

    # -- the background driver --

    async def _drive(self, run_id: str, *, start_vars: dict | None = None,
                     resume_choice=None, calib_note: str | None = None) -> None:
        reg = self.app.runs(self.kind)
        handle = reg.get(run_id)
        if handle is None:
            return
        spec = (handle.read_state() or {}).get("spec") or {}
        graph = self.graph_factory(spec)
        svc = self

        async def on_step(step_kind, view, last_result):
            st = handle.read_state() or {}
            st["current"] = view.node_id
            st["node_label"] = view.data.get("label", "")
            st["vars"] = svc._pub(view.variables)
            disp = svc._display(last_result)
            if disp is not None:
                st["last_result"] = disp
            log = st.get("log") or []
            detail = ""
            if svc.detail_fn is not None:
                try:
                    detail = svc.detail_fn(view, last_result) or ""
                except Exception:
                    detail = ""
            log.append({"node": view.node_id, "label": view.data.get("label", ""),
                        "kind": step_kind, "detail": detail})
            st["log"] = log[-60:]
            handle.write_state(st)

        runner = GraphRunner(graph, dispatch=self.dispatch, on_step=on_step)
        try:
            if resume_choice is not None:
                st = handle.read_state() or {}
                gstate = st.get("graph_state") or {}
                if calib_note is not None:
                    gstate.setdefault("variables", {})[self.calib_var] = calib_note
                step = await runner.resume(gstate, choice=resume_choice)
            else:
                step = await runner.run(start_vars or {})
            st = handle.read_state() or {}
            st["status"] = "human" if step.status == "human" else "ended"
            st["graph_state"] = step.state
            st["current"] = step.node_id
            st["vars"] = self._pub(step.variables)
            st["history"] = step.history
            disp = self._display(step.last_result)
            if disp is not None:
                st["last_result"] = disp
            handle.write_state(st)
            ev = self.paused_event if step.status == "human" else self.done_event
            if ev:
                await self.app.emit(ev, {"run_id": run_id, "node": step.node_id})
        except Exception as e:  # noqa: BLE001
            st = handle.read_state() or {}
            st["status"] = "error"
            st["error"] = str(e)[:500]
            handle.write_state(st)
            try:
                self.app.log_warn(f"graph run {run_id} failed: {e}")
            except Exception:
                pass
