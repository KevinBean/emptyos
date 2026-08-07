# Music Studio — Workflow & Feature Audit (2026-07-28)

Read from source at `apps/personal/music-studio/` and from live run state on the
daemon. No code changed in this pass.

**Verdict: B+ (86/120).** A ten-stage music-video pipeline with genuinely
sophisticated quality gates — and almost no way to see what those gates
rejected. Backend, AI depth and data persistence are A-grade. App collaboration
is the single weak dimension at 5/15, and it is what makes the failures below
invisible.

| | |
|---|---|
| HTTP routes | 44 |
| Pipeline stages | 10 |
| LLM call sites | 17 |
| Live runs errored | **4 of 5** |
| Pipeline stages named in the UI | **1 of 10** |

## Scorecard

Scored 0–15 per dimension. Balance matters more than the total: one dimension
below 6 does more damage than three at 11.

| Dimension | Score | Basis |
|---|---:|---|
| Backend | 15 | Full MV lifecycle — plan, render, resume, cancel, preview, drafts — plus compose and release |
| Data | 14 | Content-hash checkpoints, resumable runs, draft store; genuinely sophisticated reuse logic |
| AI utilization | 13 | Multi-stage LLM planning with vision review and self-repair loops; no model pill, so spend is invisible |
| Frontend | 11 | Rich player, queue, album views, approval cards — inside one 2,035-line script |
| User innovation | 11 | The motion-proof gate is a real idea: review movement on 3 clips before paying for 20 |
| UX polish | 9 | Cache-status chips and job progress are good; failures collapse into a one-line toast |
| Vault integration | 8 | Outputs and release packages land in the vault; library reads go through `music-library` |
| **App collaboration** | **5** | One `call_app`, five events, one agent-only verb. No `contributes` block at all |
| **Total** | **86** | |

## The pipeline, and what it actually rejected

Ten ordered stages (`_MV_STAGES`, `visual.py:10962`), each producing an artifact
the next consumes. The gates work — they refuse to ship a shortened or off-model
video. What is missing is the recovery loop after a gate says no.

| # | Stage | Weight | State on the live daemon |
|---:|---|---|---|
| 01 | `creative-basis` | 1 | clean |
| 02 | `prompt-check` | 1 | clean |
| 03 | `stills` | 4 · GPU | **a run died here with an empty error string** |
| 04 | `art-review` | 1 · GPU | **gate passed 1 of 20 scenes** |
| 05 | `still-review` | 1 · GPU | the only stage the UI names |
| 06 | `rough-cut` | 1 | clean |
| 07 | `motion-proof` | 2 · GPU | **0 of 3 proof clips passed** |
| 08 | `motion-audition` | 2 · GPU | clean |
| 09 | `clips` | 5 · GPU | **11 of 20 built; 9 scenes missing** |
| 10 | `assemble` | 1 | never reached |

## Live run state

Four runs are stopped in four different places. The detail panel renders
`paused[0]` and `failed[0]` only — so three of these four have no route back
into the interface.

| Run | Song | Stage | State | Reason recorded |
|---|---|---|---|---|
| `b7a9d39d` | 梦幻泡影 | — | complete | finished end to end |
| `9776d17d` | 梦幻泡影 | `art-review` | error | art-direction gate failed 1/20 after per-scene repair — scene 15 composition + subject-scale mismatch |
| `12f6d3cc` | 那道彩虹 | `motion-proof` | error | 0 of 3 representative clips passed |
| `e213c9b7` | 那道彩虹 | `clips` | error | refused to assemble a shortened MV — 11/20 built, scenes 12–20 missing |
| `60ecb8ab` | `????` | `stills` | error | **nothing recorded — the error field is an empty string** |

## Findings

### 1. Three of four failed runs are unreachable from the UI — severity 1

`msMvLoadRuns()` takes `paused[0]`, and failing that `failed[0]`, and renders one
card. Every other stopped run is invisible — its checkpoints, stills and clips
sit on disk with no button that reaches them. The resume machinery is already
built and correct; only the surface is missing.

```
pages/music-studio.js:1441   var paused = list.filter(…);  if (paused.length) { var p = paused[0]; … }
pages/music-studio.js:1483   var r = failed[0];
```

### 2. A CJK song title was written as `????`, and that run died at stills — severity 1

Run `60ecb8ab` stores `song: "????"` — four replacement characters, matching the
four-character title `那道彩虹`, which the *same* API response renders correctly
for two other runs. So the loss happened on write, not on read: this run's title
crossed a cp1252 boundary. It then failed at `stills` with an empty error, which
is what a song lookup returning nothing looks like.

The encoding bug is the plausible root cause of the failure, not a cosmetic
side-effect of it.

```json
{ "run_id": "60ecb8ab7f12", "stage": "stills", "song": "????",
  "completed": ["prompt-check"], "progress": 5, "error": "" }
```

Both other runs were web-initiated; the app also exposes `@cli_command("mv")`
(`app.py:74`), which is the first place to look for the boundary.

### 3. A stage can fail without recording why — severity 2

The stills stage logs per-scene failures to syslog (`visual.py:8898`), but the
run-level `error` stayed empty — so the one field the run list actually reads
carries nothing. Same shape as the documented ComfyUI trap where `animate()`
returns `""` and clips vanish with a silent log
(`.claude/rules/dev-gotchas.md` § Media). Any stage that can fail should be
unable to fail quietly.

### 4. Nothing outside the app knows a render is stuck — severity 2

`manifest.toml` declares no `contributes` block at all — no hub panel, no voice
intent, no timeline. For an app whose jobs run for hours and whose gates stop
four runs in five, the home screen showing nothing is what turns a *caught*
failure into an *abandoned* one. This is the whole of the 5/15 collaboration
score.

### 5. Seventeen LLM call sites, no model pill — severity 3

Multi-stage planning, vision-based art review and motion validation all spend
the think budget from the primary surface, which shows no indication of which
provider is being billed. `EOS_UI.modelPill` appears zero times in the page.
Standing convention: `.claude/rules/model-pill.md`.

### 6. `visual.py` is 10,990 lines — severity 3

Nine times the decomposition threshold, with a single `VisualMixin` spanning
4,600 of them (L3918–8541) and the staged pipeline in the last 2,400. Real debt
per `.claude/rules/multi-module-apps.md`, but not urgent — and not something to
touch in the same pass as the fixes above.

## Proposed order of work

The first three share one surface: building the run dashboard is what makes the
encoding fix and the error-reporting fix observable.

| # | Change | Dimension | Effort | Why now |
|---:|---|---|---|---|
| 1 | Run dashboard — every run, its stage, its reason, its resume button | Frontend · UX | med | Recovers three stranded runs immediately; makes every later fix visible |
| 2 | Fix title encoding on the run-record write path | Data | low | Likely root cause of a whole failed run; silently breaks any CJK-titled song |
| 3 | Make an empty stage error impossible — always record a reason | Data · UX | low | A gate that rejects without saying why costs a full re-render to diagnose |
| 4 | Hub panel — active / paused / failed render counts | Collaboration | low | Lifts the weakest dimension; puts multi-hour jobs on the home screen |
| 5 | Per-scene retry from a failure card, instead of re-running the stage | UX · Innovation | med | Nine missing clips, or one bad scene of twenty, should not cost a full stage |
| 6 | Stage rail in the detail panel — all ten, with state | Frontend | low | Ten stages exist; one is named |
| 7 | Model pill on the MV surface | AI | low | Standing convention for any surface spending the think budget |
| 8 | Voice intent — "what's rendering?" | Collaboration | low | Natural for a job you start and walk away from |

## What is genuinely good here

Worth stating plainly, because the finding list is failure-shaped:

- **The gates are correct.** Refusing to assemble an 11/20 MV is the right call,
  and most pipelines would have shipped it.
- **Resumability is real.** Content-hash checkpoint keys, per-stage skip, and
  reuse validation against the source still's hash — this is the sophisticated
  end of `emptyos/sdk/pipeline.py`, not a token use of it.
- **The motion-proof gate is a genuine product idea** — spend three clips to
  decide whether twenty are worth rendering.
- **Unit coverage is serious**: `tests/test_unit_music_studio_frames.py` is
  4,228 lines against the pure functions.

The engine is A-grade. The cockpit is not.
