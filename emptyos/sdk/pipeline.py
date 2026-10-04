"""Pipeline — ordered, resumable, previewable multi-stage generation.

A thin phase-orchestration layer on top of :class:`RunRegistry`
(``emptyos/sdk/run_registry.py``). RunRegistry already owns "a run is a stable
id + a directory of artifacts + a JSON state file"; this adds the three things
its docstring deferred until a 4th consumer needed them — **phase
orchestration, stage-preview, and resume-from-phase**.

The discipline is lifted from MoneyPrinterTurbo's ``task.py`` (the
study that motivated this module) with one deliberate improvement: MPT's
``stop_at`` only *stops early*; re-invoking re-runs from stage 1. Here a run
persists each completed stage's result in ``run.json`` so :meth:`Pipeline.resume`
skips finished stages — turning coarse "stop early" into true resume-from-partial.

What this module owns:
    - ordered ``Stage`` list, each producing one artifact
    - per-stage persistence to the run folder (state + whatever the stage writes)
    - ``stop_after`` to pause after a named stage (preview-before-next)
    - ``resume(run_id)`` to pick up after a failed/paused run, skipping completed
      stages (so an interrupted run never re-pays an expensive earlier stage)
    - monotonic progress + a terminal status (``running``/``paused``/``complete``/``error``)

What it does NOT own (by design — these stay the stage's job):
    - *which provider* runs a stage. Provider-swappability is the capability
      chain: a stage calls ``self.speak(..., prefer_provider=[...])`` /
      ``self.think(...)`` / ``self.footage(...)`` and the kernel routes +
      consent-gates. The pipeline never picks a provider.
    - binary artifact storage shape. Stages write bytes/images via
      ``ctx.write_artifact`` or to their app's own dir; the value a stage
      *returns* is persisted in ``run.json`` and MUST be JSON-able (it is
      coerced defensively — Paths → str — so a stray ``Path`` won't crash a run).

See ``.claude/rules/staged-pipeline.md`` for the full contract + when to use it.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from emptyos.sdk.run_budget import BudgetApprovalRequired, RunBudget
from emptyos.sdk.run_registry import RunRegistry


class PipelineError(Exception):
    """Programmer error — unknown run, missing state, duplicate stage names.

    Stage *failures* are NOT raised: they are captured into the run's state
    (``status="error"``) and returned in the summary so the run folder survives
    for ``resume()``. Only structural misuse raises this.
    """


class PipelineReviewRequired(Exception):
    """Pause a stage because evidence needs a human judgement.

    Unlike a stage failure, this is an expected review gate.  The stage keeps
    its durable preview artifacts on disk and resumes from the same stage after
    the consumer records the user's byte-bound decision in ``inputs``.
    """

    def __init__(self, message: str, *, pending_review: dict | None = None):
        super().__init__(message)
        self.pending_review = dict(pending_review or {})


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def describe_exception(e: BaseException, *, limit: int = 500) -> str:
    """Always-non-empty one-line description of an exception.

    ``str(e)`` is empty for a bare ``raise SomeError()`` and for several
    stdlib errors, so a run recorded ``error: ""`` — the only field the run
    list reads — and the failure could not be diagnosed without re-running
    the whole stage. Falling back to the type name (and the cause, when the
    exception itself is mute) keeps a failure self-describing.
    """
    name = type(e).__name__
    text = str(e).strip()
    if not text:
        cause = e.__cause__ or e.__context__
        cause_text = str(cause).strip() if cause is not None else ""
        text = (
            f"{type(cause).__name__}: {cause_text}" if cause_text
            else f"{name} (no message)"
        )
        return text[:limit]
    return f"{name}: {text}"[:limit]


# ── Stopped runs — the "it needs a human" surface ───────────────────────────
# A staged run is expensive and long. When a gate pauses one for approval, or
# a stage fails, the run keeps every completed stage on disk and is resumable —
# but nothing outside the owning app says so, and a *caught* failure quietly
# becomes an *abandoned* one. Any Pipeline consumer can surface its own with
# ``BaseApp.stopped_runs_panel``; these are the pure pieces underneath.

STOPPED_STATUSES = ("paused", "error")


def stopped_run_reason(state: dict) -> str:
    """Why this run is sitting there. Never empty — an unexplained stop is
    itself the thing worth saying, not a blank cell."""
    err = str(state.get("error") or "").strip()
    if err:
        return err
    if state.get("status") == "paused":
        return "waiting for your approval"
    stage = state.get("failed_stage") or state.get("stage") or "an unknown stage"
    return f"stopped at {stage} without recording a reason"


def stopped_run_rows(
    states: Iterable[Any],
    *,
    href: str,
    label_fields: Sequence[str] = ("title",),
    total_stages: int = 0,
    limit: int = 5,
) -> list[dict] | None:
    """Hub ``plain-list`` rows for runs that stopped and need a human.

    ``states`` accepts ``RunRegistry.recent_states()`` output (handle, state)
    or bare state dicts. ``label_fields`` are ``inputs`` keys tried in order
    for the run's human name — apps disagree here (a song, a topic), which is
    the only real per-app difference. Returns ``None`` when nothing is stopped,
    so a healthy day renders no panel at all.

    Rows carry ``icon`` rather than ``tone``: the plain-list renderer ignores
    ``tone``, so state has to be encoded in something it actually draws.
    """
    stopped: list[dict] = []
    for item in states:
        state = item[1] if isinstance(item, tuple) else item
        if isinstance(state, dict) and state.get("status") in STOPPED_STATUSES:
            stopped.append(state)
    if not stopped:
        return None

    rows: list[dict] = []
    for state in stopped[:limit]:
        inputs = state.get("inputs") or {}
        label = ""
        for field in label_fields:
            value = inputs.get(field)
            if isinstance(value, str) and value.strip():
                label = value.strip()
                break
        paused = state.get("status") == "paused"
        stage = state.get("failed_stage") or state.get("stage") or "?"
        subtitle = stopped_run_reason(state)
        if total_stages > 0:
            done = len(state.get("completed") or [])
            subtitle = f"{subtitle} · {done}/{total_stages} stages"
        rows.append({
            "title": f"{label or 'untitled'} · {stage}",
            "subtitle": subtitle,
            "href": href,
            "icon": "⏸" if paused else "⚠",
        })
    if len(stopped) > limit:
        rows.append({
            "title": f"+{len(stopped) - limit} more stopped",
            "subtitle": "open the app to review",
            "href": href,
        })
    return rows


def _jsonable(value: Any) -> Any:
    """Coerce a stage result to something ``json.dumps`` accepts.

    Stage results land in ``run.json``; a returned ``Path`` (common — image /
    audio paths) would otherwise crash ``write_state``. Round-tripping with
    ``default=str`` turns Paths into strings and drops nothing a stage should
    be returning as state (binary belongs in artifacts, not the result)."""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return str(value)


@dataclass
class StageContext:
    """What a stage's ``run``/``preview`` callable receives.

    A stage reads prior results via :meth:`result`, writes intermediate files
    via :meth:`write_artifact`, and reports progress via :meth:`progress`
    (reshaped to the run's overall scale) or by forwarding the raw callback in
    :attr:`raw_progress` (when a stage delegates to a helper that already emits
    absolute percentages — e.g. the podcast audio helper)."""

    app: Any
    handle: Any  # RunHandle
    inputs: dict
    results: dict
    stage: str
    base_pct: int = 0
    span_pct: int = 0
    raw_progress: Callable[..., Awaitable[None]] | None = None
    budget: Any = None  # RunBudget — always present (observe-no-cap default)

    def result(self, stage_name: str, default: Any = None) -> Any:
        """The artifact a prior stage returned (JSON-coerced)."""
        return self.results.get(stage_name, default)

    def artifact(self, name: str):
        return self.handle.artifact(name)

    def write_artifact(self, name: str, content: str | bytes):
        return self.handle.write_artifact(name, content)

    def read_artifact(self, name: str) -> str | None:
        return self.handle.read_artifact(name)

    async def progress(self, frac: float, detail: str = "") -> None:
        """Report progress as a 0..1 fraction *within this stage*; the pipeline
        reshapes it onto the run's overall 0..100 scale and forwards to the
        caller's progress callback (no-op when none was given)."""
        if self.raw_progress is None:
            return
        pct = self.base_pct + int(self.span_pct * _clamp01(frac))
        await self.raw_progress(self.stage, pct, detail)


@dataclass(frozen=True)
class Stage:
    """One step. ``run(ctx)`` returns its artifact (JSON-able state); optional
    ``preview(ctx, artifact)`` renders that artifact for a UI before the next
    stage runs. ``weight`` proportions the progress bar."""

    name: str
    run: Callable[[StageContext], Awaitable[Any]]
    preview: Callable[[StageContext, Any], Awaitable[Any]] | None = None
    weight: int = 1


@dataclass
class Pipeline:
    """An ordered set of stages with a backing :class:`RunRegistry`.

    Construct with either ``app=`` (uses ``app.runs(registry_kind)``) or an
    explicit ``registry=`` (tests, non-app callers).

    Usage::

        pipe = Pipeline(app=self, name="podcast", stages=[...])
        summary = await pipe.start({"topic": "..."}, progress=cb)
        if summary["status"] == "error":
            summary = await pipe.resume(summary["run_id"], progress=cb)
    """

    name: str
    stages: list[Stage]
    app: Any = None
    registry: RunRegistry | None = None
    registry_kind: str = "runs"
    _by_name: dict[str, Stage] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self):
        names = [s.name for s in self.stages]
        if len(names) != len(set(names)):
            raise PipelineError(f"duplicate stage names in pipeline {self.name!r}: {names}")
        self._by_name = {s.name: s for s in self.stages}
        if self.registry is None:
            if self.app is None:
                raise PipelineError("Pipeline needs either app= or registry=")
            self.registry = self.app.runs(self.registry_kind)

    # --- driving ---

    async def start(
        self,
        inputs: dict | None = None,
        *,
        run_id: str | None = None,
        stop_after: str | None = None,
        progress: Callable[..., Awaitable[None]] | None = None,
        budget: RunBudget | None = None,
    ) -> dict:
        """Mint a run and drive it (optionally pausing after ``stop_after``).

        ``budget`` is an optional per-run cost ceiling; when omitted, an
        observe-no-cap budget is used so stages can call ``ctx.budget.spend(...)``
        unconditionally without changing behaviour."""
        if stop_after and stop_after not in self._by_name:
            raise PipelineError(f"unknown stop_after stage {stop_after!r}")
        handle = self.registry.new(run_id)
        state = {
            "pipeline": self.name,
            "status": "running",
            "stage": None,
            "completed": [],
            "results": {},
            "inputs": _jsonable(inputs or {}),
            "progress": 0,
            "error": None,
            "created": _now(),
            "updated": _now(),
        }
        handle.write_state(state)
        return await self._drive(handle, state, stop_after, progress, budget)

    async def resume(
        self,
        run_id: str,
        *,
        stop_after: str | None = None,
        progress: Callable[..., Awaitable[None]] | None = None,
        inputs: dict | None = None,
        budget: RunBudget | None = None,
    ) -> dict:
        """Continue a paused/failed run, skipping already-completed stages.

        ``inputs`` (optional) shallow-merges into the persisted inputs — useful
        when resuming a ``stop_after`` pause with a user-edited artifact (e.g.
        an edited script)."""
        if stop_after and stop_after not in self._by_name:
            raise PipelineError(f"unknown stop_after stage {stop_after!r}")
        handle = self.registry.get(run_id)
        if handle is None:
            raise PipelineError(f"unknown run {run_id!r}")
        state = handle.read_state()
        if state is None:
            raise PipelineError(f"run {run_id!r} has no state")
        if inputs:
            state["inputs"] = _jsonable({**(state.get("inputs") or {}), **inputs})
        return await self._drive(handle, state, stop_after, progress, budget)

    async def _drive(self, handle, state, stop_after, progress, budget=None) -> dict:
        results = dict(state.get("results") or {})
        completed = list(state.get("completed") or [])
        total = sum(max(1, s.weight) for s in self.stages) or 1
        done_weight = sum(max(1, self._by_name[n].weight) for n in completed if n in self._by_name)
        if budget is None:
            budget = RunBudget()  # observe, no cap — harmless accumulator

        try:
            for stage in self.stages:
                if stage.name in completed:
                    continue
                state["stage"] = stage.name
                state["status"] = "running"
                state["error"] = None
                state.pop("pending_review", None)
                state["updated"] = _now()
                handle.write_state(state)

                ctx = StageContext(
                    app=self.app,
                    handle=handle,
                    inputs=state.get("inputs") or {},
                    results=results,
                    stage=stage.name,
                    base_pct=int(100 * done_weight / total),
                    span_pct=int(100 * max(1, stage.weight) / total),
                    raw_progress=progress,
                    budget=budget,
                )
                artifact = _jsonable(await stage.run(ctx))

                results[stage.name] = artifact
                completed.append(stage.name)
                done_weight += max(1, stage.weight)
                state["results"] = results
                state["completed"] = completed
                state["progress"] = int(100 * done_weight / total)
                state["updated"] = _now()
                handle.write_state(state)

                if stop_after and stage.name == stop_after:
                    state["status"] = "paused"
                    state["budget"] = budget.snapshot()
                    handle.write_state(state)
                    return _summary(handle, state, paused_at=stage.name)

            state["status"] = "complete"
            state["stage"] = None
            state["progress"] = 100
            state["budget"] = budget.snapshot()
            state["updated"] = _now()
            handle.write_state(state)
            return _summary(handle, state)

        except PipelineReviewRequired as e:
            state["status"] = "paused"
            state["error"] = None
            state.pop("failed_stage", None)
            state["pending_review"] = _jsonable({
                **e.pending_review,
                "message": str(e),
                "stage": state.get("stage"),
            })
            state["budget"] = budget.snapshot()
            state["updated"] = _now()
            try:
                handle.write_state(state)
            except Exception:
                pass
            return _summary(handle, state, paused_at=state.get("stage"))

        except BudgetApprovalRequired as e:
            # A single call exceeded per_action_usd in cap mode — pause for
            # approval (resumable, distinct from a hard error). The consumer
            # surfaces `pending_approval` as a review-gate "approve $X?" card.
            state["status"] = "paused"
            state["pending_approval"] = {
                "label": e.label, "estimate": e.estimate,
                "message": str(e), "stage": state.get("stage"),
            }
            state["budget"] = budget.snapshot()
            state["updated"] = _now()
            try:
                handle.write_state(state)
            except Exception:
                pass
            return _summary(handle, state, paused_at=state.get("stage"))

        except Exception as e:  # noqa: BLE001 — capture, persist, never lose the run
            state["status"] = "error"
            # A stage must never fail silently: `str(e)` is empty for a bare
            # `raise SomeError()`, which left the one field the run list reads
            # carrying nothing and cost a full re-render to diagnose.
            state["error"] = describe_exception(e)
            state["failed_stage"] = state.get("stage")
            state["budget"] = budget.snapshot()
            state["updated"] = _now()
            # results already holds every completed stage → resume() skips them.
            try:
                handle.write_state(state)
            except Exception:
                pass
            return _summary(handle, state)

    # --- reads ---

    def stage_names(self) -> list[str]:
        return [s.name for s in self.stages]

    def get(self, run_id: str) -> dict | None:
        handle = self.registry.get(run_id)
        if handle is None:
            return None
        state = handle.read_state()
        if state is None:
            return None
        return _summary(handle, state)

    def list(self, n: int = 50, *, status: str | None = None) -> list[dict]:
        out = []
        for handle, state in self.registry.recent_states(n, status=status):
            out.append(_summary(handle, state))
        return out

    async def preview(self, run_id: str, stage: str) -> Any:
        """Render a completed stage's artifact via its ``preview`` callable
        (or return the raw artifact when the stage declared no preview)."""
        handle = self.registry.get(run_id)
        if handle is None:
            raise PipelineError(f"unknown run {run_id!r}")
        state = handle.read_state()
        if state is None:
            raise PipelineError(f"run {run_id!r} has no state")
        s = self._by_name.get(stage)
        if s is None:
            raise PipelineError(f"unknown stage {stage!r}")
        artifact = (state.get("results") or {}).get(stage)
        if s.preview is None:
            return artifact
        ctx = StageContext(
            app=self.app, handle=handle, inputs=state.get("inputs") or {},
            results=dict(state.get("results") or {}), stage=stage,
        )
        return await s.preview(ctx, artifact)


def _summary(handle, state, *, paused_at: str | None = None) -> dict:
    """The caller-facing view of a run — run_id + status + stage results."""
    return {
        "run_id": handle.run_id,
        "pipeline": state.get("pipeline"),
        "status": state.get("status"),
        "stage": state.get("stage"),
        "completed": list(state.get("completed") or []),
        "progress": state.get("progress", 0),
        "results": dict(state.get("results") or {}),
        "error": state.get("error"),
        "failed_stage": state.get("failed_stage"),
        "paused_at": paused_at,
        "pending_approval": state.get("pending_approval"),
        "pending_review": state.get("pending_review"),
        "budget": state.get("budget"),
        "dir": str(handle.dir),
    }
