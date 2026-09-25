---
paths:
  - "emptyos/sdk/pipeline.py"
  - "emptyos/sdk/run_registry.py"
  - "emptyos/sdk/run_budget.py"
  - "apps/**"
---
# Staged Pipeline Rule — resumable, previewable, provider-swappable generation

Long multi-stage generation (podcast, music video, future video/report
builders) is the same shape every time: an ordered chain of expensive stages,
each producing an artifact, where a failure three stages in shouldn't re-pay
the two stages before it, and the user wants to inspect an early stage before
committing to the rest. `emptyos/sdk/pipeline.py` is that shape, extracted.

**Core:** `emptyos/sdk/pipeline.py` (`Pipeline`, `Stage`, `StageContext`,
`PipelineError`) — a thin phase-orchestration layer over `RunRegistry`.
**First consumer:** `apps/personal/podcast/` (`_run_via_pipeline` +
`_PODCAST_STAGES` in `pipeline.py`), behind `[apps.podcast] feature.pipeline.enabled`
(dark default). **Study that motivated it:** MoneyPrinterTurbo's `app/services/task.py`.

## Why this sits on top of RunRegistry, not beside it

`RunRegistry` (`emptyos/sdk/run_registry.py`) already owns "a run = a stable id
+ a directory of artifacts + a `run.json` state file", with three consumers
(dogfood-agent, staff, model-bench). Its docstring explicitly **deferred**
phase orchestration + resume-from-phase "until a 4th harness app needs them".
A staged generation pipeline *is* that 4th need. So `pipeline.py`:

- **consumes** `RunRegistry` for the run folder + state file (never reinvents
  `data/apps/<app>/runs/<run_id>/`),
- **adds** the ordered-stage loop, `stop_after` preview, and
  `resume(run_id)` skip-completed-stages.

Do not add a parallel run-folder concept. A run *is* a `RunHandle`.

## What the MoneyPrinterTurbo study contributed

MPT's `task.start(task_id, params, stop_at)` is one linear orchestrator with a
`stop_at` checkpoint that marks the run complete + returns the intermediate
artifact after a named stage — stage-preview for almost zero code. We lifted
that, plus its "task = an accumulating dict in a swappable store" and
"deterministic per-run artifact dir + a content-addressed cross-run cache".

The **one thing we did NOT copy**: MPT's resumability is *stop-early only* —
re-invoking always re-runs from stage 1 (it never reads `script.json` back to
skip done work). `pipeline.py` persists each stage's result in `run.json`, so
`resume()` skips completed stages and an interrupted run never re-pays an
earlier expensive stage (the TTS pass, the image generations).

## The shape

```python
from emptyos.sdk.pipeline import Pipeline, Stage

async def _stage_script(ctx):
    script = ctx.inputs.get("script") or await ctx.app._generate_script(ctx.inputs["topic"])
    ctx.write_artifact("script.json", json.dumps(script))   # intermediate file in the run dir
    await ctx.progress(1.0, f"{len(script)} segments")
    return script                                            # JSON-able → persisted to run.json

async def _stage_audio(ctx):
    script = ctx.result("script")                            # prior stage's artifact
    segments, url = await ctx.app._generate_audio(script, ..., progress=ctx.raw_progress)
    return {"segments": segments, "full_audio": url}

STAGES = [Stage("script", _stage_script, weight=2),
          Stage("audio",  _stage_audio,  weight=5)]

pipe = Pipeline(app=self, name="podcast", stages=STAGES)
summary = await pipe.start({"topic": "AI"}, progress=cb)     # run to completion
# summary = {"run_id", "status", "stage", "completed", "progress", "results", "error", "dir"}
```

- **`Stage.run(ctx)` returns the stage's artifact.** The return value is
  JSON-coerced (`_jsonable`: Paths→str) and stored in `run.json` under the
  stage name, so `resume()` can skip it and later stages read it via
  `ctx.result("<stage>")`. **Binary (audio/images) goes to disk** via
  `ctx.write_artifact(name, bytes)` or the app's own dir — return a *path/url*,
  never the bytes.
- **`stop_after="script"`** runs through `script` then pauses (`status="paused"`).
  The UI shows the artifact; `resume(run_id, inputs={"script": edited})` picks up
  at `audio`. This is the script-preview seam every generator already wants.
- **`PipelineReviewRequired`** pauses *inside* the current stage when the
  evidence has a genuine middle state that requires human judgement. The stage
  writes its preview artifact first, then raises with a small
  `pending_review` payload. The run records `status="paused"` (not `"error"`),
  does not mark the stage complete, and resumes that same stage after the
  consumer records a byte-bound decision in inputs. Use it for real tri-state
  gates: hard pass continues, hard fail follows the stage's repair/error path,
  and only the ambiguous middle asks the human.
- **`resume(run_id)`** re-drives, skipping `completed` stages. Use it after a
  crash, a daemon restart, or a `stop_after` pause.
- **Stage failures don't raise** — they're captured into `status="error"` +
  `failed_stage`, the run folder survives, and `resume()` retries from the
  failed stage. Only structural misuse (unknown run/stage, duplicate names)
  raises `PipelineError`.

### Progress: two callbacks, pick one per stage

- `ctx.progress(frac, detail)` — `frac` is 0..1 *within this stage*; the
  pipeline reshapes it onto the overall 0..100 bar using `Stage.weight`. Use
  for new stages.
- `ctx.raw_progress` — the caller's unmodified `(stage, pct, detail)` callback.
  Forward it straight into a helper that *already* emits absolute percentages
  (the podcast audio helper emits `("synthesizing", 30..75, …)`), so existing
  progress UX is preserved byte-for-byte during a migration.

### The UI contract: progress + settings + choices

Every Pipeline consumer must make an active, paused, or failed run
understandable without consulting chat or logs. Its canonical run detail
renders one structured table containing:

| section | source of truth |
|---|---|
| Progress | persisted `status`, `stage`, `progress`, and `completed / stages` |
| Settings | the run's persisted `inputs`, never whatever the form says now |
| Choices | state-legal actions only: approve, retry, resume, cancel, inspect |

If a compact surface uses `EOS_UI.jobProgress`, pass a stable exact-run detail
link. If it uses a list or hub row, that row must open the same detail. A
generic Resume button is invalid at an approval gate where only an explicit
Approve or Regenerate action is legal. Logs remain a secondary diagnostic
choice, not the primary explanation of the run.

### Per-run cost budget (`RunBudget`) — estimate → reserve → reconcile

`Pipeline.start(..., budget=RunBudget(...))` installs a per-run spend ceiling
(`emptyos/sdk/run_budget.py`; borrowed 2026-06-21 from OpenMontage). It is the
**inner** ceiling — "this single run may spend $Y" — orthogonal to the
**outer** per-actor *monthly* cap in `emptyos/sdk/autopilot.py`. They compose:
a finished run forwards `budget.spent_usd` into the monthly ledger via
`autopilot.record_spend` (the *consumer* does this — the pipeline stays pure).

`ctx.budget` is **always present** (an observe-no-cap default when no budget is
passed), so a stage meters an expensive call unconditionally:

```python
async with ctx.budget.spend("draw", provider="openai-image", n=4) as charge:
    img = await ctx.app.draw(...)
    charge(actual_usd)          # optional — defaults to the estimate
```

On enter it estimates via `emptyos/capabilities/cost.py` (`estimate_cost`;
local providers $0, unknown cloud → conservative default, unknown capability $0
— never under-bills a cap), reserves it, and classifies against the caps. Three
modes: `observe` (track only — the default, harmless), `warn` (record in
`warnings`, proceed), `cap` (raise **before** the call). In `cap` mode a
over-total call raises `BudgetExceeded` → captured as a stage error (resumable);
a single call over `per_action_usd` raises `BudgetApprovalRequired` → the run
**pauses** with `summary["pending_approval"]` (resumable after approval, a
review-gate "approve $X?" card, distinct from a hard error). A failed wrapped
call releases its reservation and bills nothing. The run's `budget.snapshot()`
lands in `run.json` + `summary["budget"]` as the audit/refinement trail.

First consumer: podcast (`apps/personal/podcast/pipeline.py` `_make_run_budget`
+ the `_ps_audio` TTS `spend`), behind `[apps.podcast] feature.run-budget.enabled`
(dark; `run_budget.mode`/`.total_usd`/`.per_action_usd` tune it). Off → `None` →
the harmless observe-no-cap default → byte-identical output.

## Provider-swappability is the capability chain, NOT the pipeline

The pipeline never chooses a provider. A stage that needs swappable backends
calls a capability and lets the kernel route + consent-gate:

```python
await ctx.app.speak(text, prefer_provider=["kokoro", "openai-tts"])  # TTS chain
await ctx.app.think(prompt, domain="text")                           # LLM chain
await ctx.app.footage("sunset over ocean")                           # stock-clip chain
```

(`footage` is shown as the shape, not as wiring: the capability and its plugin are
built but **no app consumes it yet** — verified 2026-08-28. The first staged pipeline
that wants stock clips is its intended first consumer.)

This is why "providers swappable per stage" needs no pipeline machinery — it's
already how EmptyOS works (CLAUDE.md Dev Rule 1 + the consent gate, Rule 18).
Putting a provider registry in the pipeline would duplicate the capability
system and bypass cloud consent. Don't.

## When to use it

Reach for `Pipeline` when **all** hold:

1. **3+ ordered stages**, each producing an artifact the next consumes.
2. **At least one expensive stage** (TTS, image-gen, video compose, long LLM)
   where re-paying it on a retry is real cost — that's what resume buys you.
3. **A preview seam** — the user benefits from inspecting an early artifact
   (a script, a scene plan) before the rest runs.

Both confirmed consumers fit: podcast (script→audio→visuals→finalize) and
music-studio MV (transcribe→analyze→character→plan, then render). The audit
that justified extraction (CLAUDE.md rule 9) is in
`project_repo_mining`-style notes; the trigger was *two* apps hand-rolling
stage sequencing + final-only history + mid-run data loss.

## When NOT to use it

- **One- or two-step generation.** `self.think()` then `self.write()` is not a
  pipeline; the ceremony costs more than it saves.
- **No expensive stage to protect.** If a full re-run is cheap, resume earns
  nothing — just run it.
- **Fan-out / branching DAGs.** `Pipeline` is *linear* (with `stop_after`). A
  stage may fan out *internally* (e.g. music-studio renders N scene images
  concurrently inside one `render` stage), but the top-level chain is ordered.
  A genuine branching DAG is out of scope — don't bend the linear model into one.
- **High-frequency / per-item loops.** Reactor breadcrumbs, telemetry. The run
  folder per invocation would be litter.

## A stopped run needs a way back (2026-07-28)

A gate that pauses an expensive run, or a stage that fails, leaves every
completed stage on disk and the run resumable — but if nothing outside the
owning app says so, a *caught* failure quietly becomes an *abandoned* one. Two
pieces close that, both in `emptyos/sdk/pipeline.py`:

- **`describe_exception(e)`** — `state["error"]` can no longer be empty. `str(e)`
  is blank for a bare `raise SomeError()`, so a music-studio MV run recorded
  `error: ""` and diagnosing it meant re-running a GPU stage to reproduce a
  failure that had already happened. Falls back to the type name, or the cause
  when the exception itself is mute.
- **`stopped_run_rows(...)` + `BaseApp.stopped_runs_panel(...)`** — hub
  `plain-list` rows for this app's paused/error runs, or `None` on a healthy
  day. A panel method is one call; the only per-app parameter that matters is
  which `inputs` key names the run (a song vs a topic). Extracted at the second
  consumer (music-studio MV renders, podcast episodes); podcast surfaced two
  genuinely stranded runs the first time it ran.

The hub row is a doorway, not the complete explanation. The owning app's
run-detail surface still carries the progress/settings/choices table above.

Any Pipeline consumer should contribute one — `[[contributes.hub.panel]]` at
priority <150 (≥150 is dropped from the core hub, see
`.claude/rules/hub-panels.md`). Rows carry `icon`, not `tone`: the plain-list
renderer draws `icon` and silently ignores `tone`.

## Migration discipline (first consumer pattern)

Podcast is the reference migration and shows the safe shape:

1. **Extract behavior-preserving helpers** from the monolith generator
   (`_build_visuals`, `_finalize_entry`) that *both* the legacy path and the
   pipeline stages call — so there's one implementation, no drift.
2. **Keep the legacy path** as the default. Route through the pipeline only
   when `[apps.<id>] feature.pipeline.enabled` is set (dark default — per the
   `feature_pipeline_flag_default_dark` memory). Nothing regresses until the
   flag is flipped and the pipeline path is proven.
3. **Stages are module-level fns** taking `ctx` (not bound methods); they reach
   the app via `ctx.app`. Reuse the app's existing helpers; don't reimplement.
4. Once the pipeline path is proven in real use, collapse the legacy path and
   make the pipeline unconditional (a later session — not at introduction).

## Cross-references

- `emptyos/sdk/run_registry.py` — the run-folder/state primitive this builds on.
  Read its "Out of scope" note; this rule is its documented graduation.
- `.claude/rules/proposed-action.md` — `stop_after` preview is the generative
  sibling of propose→preview→confirm; a paused run *is* a preview the user
  approves (resume) or discards.
- `.claude/rules/test-fix-verify-loop.md` + `apps/extension/dev/fix-agent/runs.py`
  — the other multi-phase-over-run-folders state machine; same `status`-driven
  shape, different domain (it's not generation, so it didn't graduate to this).
- CLAUDE.md rule 9 — extract on the 2nd consumer; this earned it at podcast + MV.
- CLAUDE.md Dev Rule 18 + the `footage` capability — provider-swappability lives
  in the capability chain, which is why the pipeline stays provider-agnostic.
