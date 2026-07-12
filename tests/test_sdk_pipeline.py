"""Tests for emptyos.sdk.pipeline — ordered, resumable, previewable stages.

Pure SDK tests against a tmp_path RunRegistry; no daemon, no BaseApp. Covers
the three things this layer adds over RunRegistry: stage ordering + result
persistence, stop_after preview-pause, and resume-from-stage (skip completed,
retry-from-failure). Async work is wrapped in asyncio.run() per the repo's
test convention.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from emptyos.sdk.pipeline import Pipeline, PipelineError, Stage
from emptyos.sdk.run_registry import RunRegistry


def _run(coro):
    return asyncio.run(coro)


def _pipe(tmp_path, stages, name="t"):
    return Pipeline(name=name, stages=stages, registry=RunRegistry(tmp_path / name))


# ─── happy path: ordering + result threading + persistence ─────────


def test_runs_stages_in_order_and_threads_results(tmp_path):
    order = []

    async def s1(ctx):
        order.append("s1")
        return {"n": 1}

    async def s2(ctx):
        order.append("s2")
        prev = ctx.result("one")
        return {"n": prev["n"] + 1}

    pipe = _pipe(tmp_path, [Stage("one", s1), Stage("two", s2)])
    summary = _run(pipe.start({"seed": 1}))

    assert order == ["s1", "s2"]
    assert summary["status"] == "complete"
    assert summary["progress"] == 100
    assert summary["completed"] == ["one", "two"]
    assert summary["results"]["two"] == {"n": 2}


def test_state_persists_to_run_folder(tmp_path):
    async def s1(ctx):
        ctx.write_artifact("note.txt", "hello")
        return {"ok": True}

    reg = RunRegistry(tmp_path / "p")
    pipe = Pipeline(name="p", stages=[Stage("one", s1)], registry=reg)
    summary = _run(pipe.start({}))

    run_dir = Path(summary["dir"])
    assert (run_dir / "run.json").exists()
    assert (run_dir / "note.txt").read_text() == "hello"
    # get() reflects the persisted state
    again = pipe.get(summary["run_id"])
    assert again["status"] == "complete"
    assert again["results"]["one"] == {"ok": True}


def test_inputs_available_to_stages(tmp_path):
    async def s1(ctx):
        return {"topic": ctx.inputs["topic"]}

    pipe = _pipe(tmp_path, [Stage("one", s1)])
    summary = _run(pipe.start({"topic": "AI"}))
    assert summary["results"]["one"] == {"topic": "AI"}


# ─── progress: monotonic, reshaped onto overall scale by weight ────


def test_progress_callback_monotonic_and_weighted(tmp_path):
    seen = []

    async def cb(stage, pct, detail):
        seen.append((stage, pct))

    async def s1(ctx):
        await ctx.progress(0.5, "half")
        return 1

    async def s2(ctx):
        await ctx.progress(1.0, "done")
        return 2

    # weights 1 + 3 → s1 spans 0..25, s2 spans 25..100
    pipe = _pipe(tmp_path, [Stage("one", s1, weight=1), Stage("two", s2, weight=3)])
    _run(pipe.start({}, progress=cb))

    pcts = [p for _, p in seen]
    assert pcts == sorted(pcts)  # monotonic
    assert ("one", 12) in seen   # 0 + int(25 * 0.5)
    assert ("two", 100) in seen  # 25 + int(75 * 1.0)


# ─── stop_after: pause + resume ────────────────────────────────────


def test_stop_after_pauses_then_resume_completes_without_rerun(tmp_path):
    calls = {"one": 0, "two": 0}

    async def s1(ctx):
        calls["one"] += 1
        return {"script": "draft"}

    async def s2(ctx):
        calls["two"] += 1
        return {"audio": "x.mp3"}

    pipe = _pipe(tmp_path, [Stage("one", s1), Stage("two", s2)])
    paused = _run(pipe.start({}, stop_after="one"))

    assert paused["status"] == "paused"
    assert paused["paused_at"] == "one"
    assert paused["completed"] == ["one"]
    assert calls == {"one": 1, "two": 0}

    done = _run(pipe.resume(paused["run_id"]))
    assert done["status"] == "complete"
    assert done["completed"] == ["one", "two"]
    # s1 NOT re-run on resume — the whole point of resume-from-stage.
    assert calls == {"one": 1, "two": 1}


def test_resume_can_override_inputs(tmp_path):
    async def s1(ctx):
        return {"v": ctx.inputs.get("script", "auto")}

    async def s2(ctx):
        return {"used": ctx.result("one")["v"]}

    pipe = _pipe(tmp_path, [Stage("one", s1), Stage("two", s2)])
    paused = _run(pipe.start({}, stop_after="one"))
    assert paused["results"]["one"] == {"v": "auto"}

    # Resuming doesn't re-run "one", so its result stays "auto"; new inputs
    # reach the remaining stages.
    done = _run(pipe.resume(paused["run_id"], inputs={"extra": "edited"}))
    assert done["status"] == "complete"


# ─── error capture + resume-from-failure ───────────────────────────


def test_stage_failure_is_captured_not_raised(tmp_path):
    async def s1(ctx):
        return {"ok": True}

    async def s2(ctx):
        raise RuntimeError("boom")

    pipe = _pipe(tmp_path, [Stage("one", s1), Stage("two", s2)])
    summary = _run(pipe.start({}))

    assert summary["status"] == "error"
    assert summary["failed_stage"] == "two"
    assert "boom" in summary["error"]
    assert summary["completed"] == ["one"]  # s1 persisted


def test_resume_retries_only_the_failed_stage(tmp_path):
    state = {"fail": True}
    calls = {"one": 0, "two": 0}

    async def s1(ctx):
        calls["one"] += 1
        return {"ok": True}

    async def s2(ctx):
        calls["two"] += 1
        if state["fail"]:
            raise RuntimeError("transient")
        return {"done": True}

    pipe = _pipe(tmp_path, [Stage("one", s1), Stage("two", s2)])
    failed = _run(pipe.start({}))
    assert failed["status"] == "error"
    assert calls == {"one": 1, "two": 1}

    state["fail"] = False
    fixed = _run(pipe.resume(failed["run_id"]))
    assert fixed["status"] == "complete"
    # s1 not re-run (already completed); s2 retried once.
    assert calls == {"one": 1, "two": 2}


# ─── JSON coercion of results (Path → str) ─────────────────────────


def test_path_results_are_coerced_for_run_json(tmp_path):
    async def s1(ctx):
        # Returning a Path would crash a naive json.dumps; pipeline coerces it.
        return {"file": Path("D:/x/y.mp3"), "n": 3}

    pipe = _pipe(tmp_path, [Stage("one", s1)])
    summary = _run(pipe.start({}))
    assert summary["status"] == "complete"
    assert isinstance(summary["results"]["one"]["file"], str)
    assert summary["results"]["one"]["n"] == 3


# ─── preview ───────────────────────────────────────────────────────


def test_preview_renders_via_stage_preview_callable(tmp_path):
    async def s1(ctx):
        return {"raw": [1, 2, 3]}

    async def prev(ctx, artifact):
        return {"count": len(artifact["raw"])}

    pipe = _pipe(tmp_path, [Stage("one", s1, preview=prev)])
    summary = _run(pipe.start({}))
    rendered = _run(pipe.preview(summary["run_id"], "one"))
    assert rendered == {"count": 3}


# ─── structural misuse raises ──────────────────────────────────────


def test_duplicate_stage_names_raise(tmp_path):
    async def s(ctx):
        return None

    with pytest.raises(PipelineError):
        Pipeline(name="dup", stages=[Stage("a", s), Stage("a", s)],
                 registry=RunRegistry(tmp_path / "dup"))


def test_unknown_run_resume_raises(tmp_path):
    async def s(ctx):
        return None

    pipe = _pipe(tmp_path, [Stage("a", s)])
    with pytest.raises(PipelineError):
        _run(pipe.resume("nope-does-not-exist"))


def test_unknown_stop_after_raises(tmp_path):
    async def s(ctx):
        return None

    pipe = _pipe(tmp_path, [Stage("a", s)])
    with pytest.raises(PipelineError):
        _run(pipe.start({}, stop_after="ghost"))


def test_list_and_get(tmp_path):
    async def s(ctx):
        return {"ok": True}

    pipe = _pipe(tmp_path, [Stage("a", s)])
    r1 = _run(pipe.start({}))
    r2 = _run(pipe.start({}))
    runs = pipe.list()
    ids = {r["run_id"] for r in runs}
    assert r1["run_id"] in ids and r2["run_id"] in ids
    assert pipe.get(r1["run_id"])["status"] == "complete"
    assert pipe.get("missing") is None


# ─── per-run budget threading (Borrow A) ───────────────────────────


def test_budget_threaded_to_stage_and_snapshot_in_summary(tmp_path):
    from emptyos.sdk.run_budget import RunBudget

    async def s(ctx):
        async with ctx.budget.spend("draw", provider="openai-image", n=2):
            pass
        return {"ok": True}

    budget = RunBudget(total_usd=1.0, mode="cap", cost_fn=lambda *a, **k: 0.16)
    pipe = _pipe(tmp_path, [Stage("gen", s)])
    summary = _run(pipe.start({}, budget=budget))

    assert summary["status"] == "complete"
    assert summary["budget"]["spent_usd"] == pytest.approx(0.16)
    assert summary["budget"]["mode"] == "cap"


def test_default_budget_present_when_none_passed(tmp_path):
    async def s(ctx):
        # ctx.budget must exist even with no budget= arg (observe-no-cap default)
        async with ctx.budget.spend("speak", provider="edge-tts", chars=100):
            pass
        return {"ok": True}

    pipe = _pipe(tmp_path, [Stage("a", s)])
    summary = _run(pipe.start({}))
    assert summary["status"] == "complete"
    assert summary["budget"]["spent_usd"] == 0.0  # local provider = free


def test_cap_over_total_fails_stage_as_error(tmp_path):
    from emptyos.sdk.run_budget import RunBudget

    async def s(ctx):
        async with ctx.budget.spend("draw"):
            pass
        return {"ok": True}

    budget = RunBudget(total_usd=0.10, mode="cap", cost_fn=lambda *a, **k: 0.5)
    pipe = _pipe(tmp_path, [Stage("gen", s)])
    summary = _run(pipe.start({}, budget=budget))
    assert summary["status"] == "error"
    assert "exceed run budget" in (summary["error"] or "")


def test_per_action_cap_pauses_for_approval(tmp_path):
    from emptyos.sdk.run_budget import RunBudget

    async def s(ctx):
        async with ctx.budget.spend("draw", label="big"):
            pass
        return {"ok": True}

    budget = RunBudget(total_usd=100.0, mode="cap", per_action_usd=0.5,
                       cost_fn=lambda *a, **k: 0.9)
    pipe = _pipe(tmp_path, [Stage("gen", s)])
    summary = _run(pipe.start({}, budget=budget))
    assert summary["status"] == "paused"
    assert summary["pending_approval"]["label"] == "big"
    assert summary["pending_approval"]["estimate"] == pytest.approx(0.9)
