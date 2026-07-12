---
name: eos-repo-extract
description: Study an external repo and extract its *discipline* into EmptyOS as the smallest honest abstraction over existing primitives (a reusable pattern, not a port), proven by >=2 real consumers plus a first-consumer implementation. Use when the user says "study/mine this repo", "borrow from <project>", "extract the pattern from <external codebase>", or pastes a foreign repo to learn from. Sibling of eos-sdk-extract (which dedupes *within* the codebase); NOT for porting a foreign app wholesale.
---

# EmptyOS Repo Extract

Study an external repo/project, then extract its **discipline** into EmptyOS — a
reusable pattern, not a port. The sibling of `eos-sdk-extract`: that one dedupes
*within* the codebase; this one mines a *foreign* codebase for a workflow shape
worth adopting and lands it as EmptyOS-native infrastructure.

Goal: **turn "this external project does X well" into the smallest honest
EmptyOS abstraction (built on existing primitives), proven by ≥2 real consumers
and a first-consumer implementation — without rebuilding the external app.**

Codifies the move made for MoneyPrinterTurbo → `emptyos/sdk/pipeline.py` +
`footage` capability (2026-06-07). Triggered the second time the repo-mining
shape recurred (cf. `project_repo_mining_2026_06`); this skill is the runner so
the third time isn't hand-walked.

## When to Use

- User points at a GitHub repo / project and says "study X and design an EmptyOS
  version", "extract the pattern from X", "what's worth lifting from X",
  "don't rebuild X, take its discipline".
- `/eos-simplify` §8 flagged a hand-run "study repo → extract pattern" workflow.
- You're about to read a foreign codebase to copy an idea — run this instead of
  free-handing it.

**Not** for: rebuilding the external app feature-for-feature (that's a port, not
an extraction); adopting a single function (just write it); pure research with
no intended EmptyOS artifact (that's `deep-research` or a repo-review note —
`reference_repo_review_log`).

## The Pipeline

### 1. Study the external repo — *digest, not feature list*

Fan out an `Agent` (general-purpose) to fetch the repo's README + source tree +
key files (raw.githubusercontent URLs) and return a **structured architectural
digest**: the layering split, the per-unit state/data model, where intermediate
state is persisted, what's swappable, and — critically — **the minimal
discipline worth copying** + **the one thing NOT to copy verbatim**. Demand file
paths and code-shape quotes, not prose. You're designing an abstraction from
this, so vague is useless.

### 2. Audit EmptyOS for ≥2 real consumers — *in parallel*

Fan out a **second** `Agent` (same message, parallel) to find every EmptyOS
feature that already hand-rolls the same shape. For each: list the concrete
stages/state/persistence with file:line, whether it'd benefit, and **whether the
rule-9 threshold (2+ real consumers) is met**. Count *latent* consumers too, not
just active ones (`feedback_sdk_audit_missing_helper_trap`). One consumer →
**stop**; build it specific in that one app, don't extract (CLAUDE.md rule 9 /
Forge anti-abstraction).

### 3. Grep the SDK before reinventing — *build on, don't beside*

The highest-leverage step and the easiest to skip. Before designing anything,
grep `emptyos/sdk/` + `base_app.py` for an existing primitive that already owns
part of the shape (`feedback_grep_sdk_before_reinventing`). The MPT case found
`RunRegistry` already owned the run-folder/state half and its docstring
*explicitly deferred* the other half until a 4th consumer — so the right design
was a thin layer **on top of** it, not a greenfield module. If an existing
primitive's "Out of scope" note names exactly what you're about to build, that's
your graduation signal — extend it, don't duplicate it.

### 4. Design — layer-on-top, provider-agnostic

Design the smallest abstraction that adds only the missing piece. Keep
provider/model choice in the capability chain, not the new abstraction (that's
how EmptyOS already does swappability + cloud consent). Honour the "don't copy
verbatim" finding from step 1 (e.g. MPT's stop-early-only resumability → we added
true resume-from-partial).

### 5. Land three artifacts

- **`.claude/rules/<pattern>.md`** — the design doc: why-it-sits-on-the-existing-
  primitive, the external lineage, when to use / not, the migration discipline.
- **First-consumer implementation, dark-flagged.** Refactor the cleanest consumer
  onto the new abstraction behind `[apps.<id>] feature.<slug>.enabled` (dark
  default, `feature_pipeline_flag_default_dark`). Use **behavior-preserving
  extraction** so the legacy path and the new path share one set of helpers (no
  drift); keep the legacy path as default until the new one is proven.
- **The one primitive they had that we lacked.** If step 1 surfaced a missing
  capability (MPT's `material.py` → stock footage), add it as a **capability**
  (mirrors draw/animate/browse: provider chain + human fallback + cloud consent
  for free, CLAUDE.md rule 18) when it's a generic verb, or a connector app when
  it's not. Dark until configured.

### 6. Verify

`python -m py_compile` the changed Python; write daemon-free unit tests for the
new SDK (wrap async with `asyncio.run()` per repo convention); then prove the
first consumer **end-to-end on a leased sandbox member**, never `:9000`
(`.claude/rules/sandbox-driven-testing.md`). The error path is part of the proof
— a captured-not-crashed failure with a persisted run is a passing result.

### 7. Record + sync docs

Add a one-line `project_*` memory (the design decision + the don't-re-research
note), update any capability/plugin counts in CLAUDE.md + `.claude/rules/`, and
run `/eos-simplify` before committing.

## Principles (the load-bearing ones)

- **Extract the discipline, not the app.** "Don't rebuild MPT — lift its workflow
  discipline." The deliverable is an EmptyOS-native abstraction, not a clone.
- **2+ consumers is the floor.** No consumers in hand → research note, not code.
- **Build on existing primitives.** Grep the SDK first; a layer on `RunRegistry`
  beats a parallel run-folder system that drifts.
- **Dark-flag the first consumer.** Nothing regresses until the flag flips.
- **The capability chain owns provider-swappability**, not your new abstraction.

## Anti-patterns

- Porting the external app's structure 1:1 (their MVC, their file names) instead
  of mapping it onto EmptyOS conventions.
- Greenfielding a module that duplicates an existing SDK primitive because you
  didn't grep first.
- Extracting on one consumer "because it'll obviously be reused" — premature.
- Flipping the first-consumer flag on by default in the same change.
- Skipping the sandbox E2E because the unit tests pass — unit tests don't catch
  boot-path / provider-availability reality.

## Cross-references

- `.claude/rules/staged-pipeline.md` — the reference output of this skill.
- `eos-sdk-extract` — the within-codebase sibling (dedupe, not foreign-mine).
- `reference_repo_review_log` — where a *review* (no extraction) is filed instead.
- `.claude/rules/sandbox-driven-testing.md` — the step-6 verification loop.
- CLAUDE.md rule 9 (extract on 2nd consumer) + rule 18 (cloud consent for the
  new capability).
