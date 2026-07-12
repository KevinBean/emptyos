"""Graph-pipeline binding — drive ``Pipeline`` runs + app verbs from a ``DecisionGraph``.

This is the seam between the two generation primitives:

- :class:`~emptyos.sdk.pipeline.Pipeline` is **linear-only** by design (its own
  docstring): ordered stages, per-stage persistence, resume-from-partial. It
  deliberately does NOT own loops, gates, or branches.
- :class:`~emptyos.sdk.decision_graph.DecisionGraph` is the **cyclic control
  topology**: nodes + ``auto`` transitions (gates that route on variable state)
  + ``choice`` transitions (human branches) + variable mutations. Pure, authored,
  JSON-round-trippable.

A real workflow — e.g. the LLM-data-synthesiser closed loop — is a *linear
executor wrapped in a loop with gates*. The prep chain and the small-batch QC
run are ``Pipeline`` work; the "did QC pass?" diamond, the inner fix loop, the
human approve/reject branch, and the outer calibration loop are ``DecisionGraph``
topology. This module composes them.

Purity boundary — ``decision_graph`` is UNTOUCHED and stays pure.
The trick: ``DecisionRun`` already halts at a choice point, so this layer uses a
node's single ``choice`` transition as an **orchestrator breakpoint**. The runner
lands on an *action node* (one choice), executes the node's ``action``, folds the
result into ``run.variables`` (public) via ``emit`` rules, then takes the one edge
onward into a *gate node* (``auto``-only), which the pure engine routes on the
freshly-updated variables inside its existing ``_settle()``. The orchestrator only
ever distinguishes two node shapes:

- **action node** — ``data.action`` present, exactly one (unconditional) ``choice``
  edge to a gate. Runner runs it, folds the result, auto-continues.
- **human node** — ``data.human`` truthy, two or more ``choice`` edges, no action.
  Runner hands the :class:`~emptyos.sdk.decision_graph.View` back to the caller and
  waits for a real :meth:`~emptyos.sdk.decision_graph.DecisionRun.choose`.

Gate nodes (``auto``-only, no choices, no action) are never *landed on* — the pure
engine walks straight through them. So this layer adds NO routing logic; it adds
*execution* + *result-folding* and lets the graph route.

Node ``data`` contract (everything the binding reads):

    {
      "label":  "Human-readable station name",            # display only
      "action": {                                          # action node only
          "kind":   "pipeline" | "verb" | "noop",
          "ref":    "<pipeline-name>" | "<app>.<method>",  # ignored for noop
          "inputs": {...},                                 # static inputs
          "pass_vars": ["round", "spec"],                  # vars to pass as inputs
      },
      "emit":   [                                          # fold result -> vars
          {"var": "qc_passed", "from": "results.aggregate.passed"},  # dotted path
          {"var": "round",     "op": "inc"},                         # a Mutation
      ],
      "human":  true,                                      # human node only
    }

``emit`` rules are either a *read* (``{"var", "from"}`` — dotted path into the
action result) or a :class:`~emptyos.sdk.decision_graph.Mutation` dict
(``{"var", "op", "value"}`` — reused verbatim, so counters/toggles come free).

Resume: a :class:`GraphStep` carries ``state`` (= ``run.to_state()``), so a caller
persists it at a human pause and later calls :meth:`GraphRunner.resume` with the
human's choice. Pipeline runs persist independently in their own RunRegistry, so
the whole graph-pipeline is resumable end-to-end.

Pure-ish: no kernel import, no randomness, no ``Date.now``. The only I/O is via
the injected ``dispatch`` callable (which is where a Pipeline/verb actually runs),
so the runner itself is unit-testable with a fake dispatch and no daemon.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from emptyos.sdk.decision_graph import (
    DecisionGraph,
    DecisionGraphError,
    DecisionRun,
    Mutation,
    View,
)

# A single auto-continue burst that never reaches a human node or an end is a
# runaway loop (a misauthored graph whose gates always route back to an action).
# This caps the runner's own walk, distinct from the engine's _MAX_AUTO_STEPS.
_MAX_RUNNER_STEPS = 500

# Dispatch: given a node's `action` dict + a snapshot of the run variables,
# execute it and return a JSON-able result dict. Injected so the runner stays
# testable; `make_app_dispatch` builds the real one over Pipelines + call_app.
Dispatch = Callable[[dict, dict], Awaitable[dict]]


class GraphPipelineError(Exception):
    """Misauthored graph or runner misuse (runaway walk, bad node shape)."""


# ─────────────────────────────── result folding ───────────────────────────────


def _dig(result: Any, path: str) -> Any:
    """Read a dotted path out of a (possibly nested) result dict. ``None`` if absent."""
    cur = result
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, (list, tuple)):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return cur


def apply_emit(emit_rules: list[dict], result: dict, variables: dict) -> None:
    """Fold an action result into the run's variables.

    Each rule is either a *read* (``{"var", "from"}`` — dotted path into the
    result) or a :class:`Mutation` dict (``{"var", "op", "value"}``). Reads run
    first-class; mutations reuse the decision-graph mutation engine so ``inc`` /
    ``toggle`` / ``append`` counters need no new code here."""
    for rule in emit_rules or []:
        if not isinstance(rule, dict) or "var" not in rule:
            continue
        if "from" in rule:
            variables[rule["var"]] = _dig(result, str(rule["from"]))
        else:
            Mutation.from_dict(rule).apply(variables)


def collect_inputs(action: dict, variables: dict) -> dict:
    """Merge an action's static ``inputs`` with the named ``pass_vars`` snapshot."""
    inputs = dict(action.get("inputs") or {})
    for name in action.get("pass_vars") or []:
        if name in variables:
            inputs[name] = variables[name]
    return inputs


# ─────────────────────────────── default dispatch ─────────────────────────────


def make_app_dispatch(
    *,
    app: Any = None,
    pipelines: dict[str, Any] | None = None,
    verbs: Callable[[str, str, dict], Awaitable[dict]] | None = None,
) -> Dispatch:
    """Build the real dispatch over named ``Pipeline`` objects + app verbs.

    - ``pipelines``: ``{name -> Pipeline}``. A ``{"kind": "pipeline", "ref": name}``
      action calls ``pipe.start(inputs)`` and returns its summary dict (which
      carries ``results`` / ``status`` / ``run_id`` — emit dotted paths read into
      ``results.<stage>.<field>``).
    - ``verbs``: optional ``async (app_id, method, inputs) -> dict``. Defaults to
      ``app.call_app(app_id, method, **inputs)`` when ``app`` is given.

    ``noop`` actions return ``{}`` (a station that only mutates vars via ``emit``)."""
    pipelines = pipelines or {}

    async def _verbs(app_id: str, method: str, inputs: dict) -> dict:
        if verbs is not None:
            return await verbs(app_id, method, inputs)
        if app is None:
            raise GraphPipelineError("verb action needs either app= or verbs=")
        return (await app.call_app(app_id, method, **inputs)) or {}

    async def dispatch(action: dict, variables: dict) -> dict:
        kind = (action or {}).get("kind", "noop")
        if kind == "noop":
            return {}
        inputs = collect_inputs(action, variables)
        if kind == "pipeline":
            name = action.get("ref")
            pipe = pipelines.get(name)
            if pipe is None:
                raise GraphPipelineError(f"no pipeline registered as {name!r}")
            return await pipe.start(inputs)
        if kind == "verb":
            app_id, _, method = str(action.get("ref", "")).partition(".")
            if not app_id or not method:
                raise GraphPipelineError(f"verb action ref must be '<app>.<method>', got {action.get('ref')!r}")
            return await _verbs(app_id, method, inputs)
        raise GraphPipelineError(f"unknown action kind {kind!r}")

    return dispatch


# ──────────────────────────────────── runner ──────────────────────────────────


@dataclass
class GraphStep:
    """Where a runner drive halted, and enough state to resume.

    ``status``:
      - ``"human"`` — paused on a ``data.human`` node; ``view.choices`` are the
        options. Persist ``state``, get the human's pick, call
        :meth:`GraphRunner.resume`.
      - ``"ended"`` — reached an end node (or a content dead-end).
    ``last_result`` is the most recent action's raw result (handy for surfacing
    the QC summary on a human node)."""

    status: str
    view: View
    node_id: str
    variables: dict
    state: dict
    history: list[str] = field(default_factory=list)
    last_result: dict | None = None


@dataclass
class GraphRunner:
    """Drives a :class:`DecisionGraph`, executing action nodes via ``dispatch``.

    Usage::

        runner = GraphRunner(graph, dispatch=make_app_dispatch(app=self, pipelines={...}))
        step = await runner.run({"spec": spec})
        if step.status == "human":
            # show step.last_result (QC summary), get approve/reject from the user
            step = await runner.resume(step.state, choice="large_scale")
        # step.status == "ended"
    """

    graph: DecisionGraph
    dispatch: Dispatch
    on_step: Callable[[str, View, dict | None], Awaitable[None]] | None = None
    max_steps: int = _MAX_RUNNER_STEPS

    async def run(self, variables: dict | None = None) -> GraphStep:
        run = DecisionRun(self.graph, variables)
        run.start()
        return await self._drive(run)

    async def resume(self, state: dict, *, choice: int | str | None = None) -> GraphStep:
        """Continue a persisted run. At a human pause, pass the human's ``choice``
        (eligible-index or target node id); the runner takes it, then drives on."""
        run = DecisionRun.from_state(self.graph, state)
        if run.current is None:
            raise GraphPipelineError("cannot resume a run that never started")
        if choice is not None:
            if run.status != "running":
                raise GraphPipelineError(f"cannot apply choice: run status is {run.status!r}")
            run.choose(choice)
        return await self._drive(run)

    async def _drive(self, run: DecisionRun) -> GraphStep:
        last_result: dict | None = None
        steps = 0
        while run.status == "running":
            view = run.view()
            data = view.data

            # Human node — hand back for a real decision.
            if data.get("human"):
                if self.on_step is not None:
                    await self.on_step("human", view, last_result)
                return self._step("human", run, last_result)

            # Action node — run it, fold the result, take the single edge onward.
            action = data.get("action")
            if action and action.get("kind") != "noop":
                last_result = await self.dispatch(action, dict(run.variables)) or {}
                apply_emit(data.get("emit") or [], last_result, run.variables)
            elif action:  # explicit noop — emit-only station
                apply_emit(data.get("emit") or [], {}, run.variables)

            if self.on_step is not None:
                await self.on_step("action", view, last_result)

            # The action node must have exactly one eligible breakpoint choice
            # (its single edge into the gate). Routing is the gate's job, not ours.
            eligible = view.choices
            if len(eligible) != 1:
                raise GraphPipelineError(
                    f"action node {view.node_id!r} must have exactly one eligible "
                    f"choice (the gate edge); found {len(eligible)}. Put branching "
                    f"on an auto-only gate node, not on the action node."
                )
            try:
                run.choose(0)
            except DecisionGraphError as e:  # pragma: no cover — defensive
                raise GraphPipelineError(str(e)) from e

            steps += 1
            if steps > self.max_steps:
                raise GraphPipelineError(
                    f"runner exceeded {self.max_steps} steps without reaching a human "
                    f"node or an end — a gate is routing back to an action forever "
                    f"(near {run.current!r}). Add a convergence guard (round counter)."
                )

        if self.on_step is not None:
            await self.on_step(run.status, run.view(), last_result)
        return self._step(run.status, run, last_result)

    def _step(self, status: str, run: DecisionRun, last_result: dict | None) -> GraphStep:
        view = run.view()
        return GraphStep(
            status=status,
            view=view,
            node_id=view.node_id,
            variables=dict(run.variables),
            state=run.to_state(),
            history=list(run.history),
            last_result=last_result,
        )
