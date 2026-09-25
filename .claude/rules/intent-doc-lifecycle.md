---
paths:
  - "apps/**/app-builder/**"
  - "apps/**/INTENT.md"
---
# INTENT.md Lifecycle — three docs, three lifetimes, and the archive step

Every scaffolded app carries three documents that answer the same underlying
question — *what is this app supposed to do* — at three different points in
time, and they must not be conflated:

| Doc | Lifetime | Job |
|---|---|---|
| The grill spec (`.claude/skills/eos-new-app`'s Step 1 output) | **Frozen at scaffold time** | Birth certificate — what was approved to build, verbatim, never edited after |
| `apps/<id>/INTENT.md` | **Living — moves with the code** | Current understanding of why the app exists, what it calls into/emits/listens for, open questions, future ideas |
| `{vault}/10_Projects/emptyos/log/app-development.md` | **Auto-written, append-only** | Changelog — what happened, in order |

`INTENT.md`'s header states this explicitly on every app that has one (19 as
of 2026-08-22): `> Living design doc for apps/<id>/. Edit as the app
evolves. > Birth certificate: <spec path>. Changelog:
10_Projects/emptyos/log/app-development.md.`

## Two colliding meanings — know which one you're touching

**This file governs the informal, app-scaffolding `INTENT.md`** — the
four-section living doc (`## Why` / `## Relationships` / `## Open
questions` / `## Future`) every new app gets via `eos-new-app`.

A **separate, formal** `INTENT.md`/`ALGORITHM.md` pair exists for
engineering-calculator apps under `docs/TRUST-LOOP.md` — a controlled
Application Package with stable requirement ids (`UX-*`) traced to code and
tests via `[assurance]` in the manifest, human-approved as a contract before
implementation. That one is a stronger, opt-in discipline for a narrower app
class; it is not what this file is about, and the two must not be merged.
If an app has `[assurance]` in its manifest, its `INTENT.md` follows
`docs/TRUST-LOOP.md`, not this rule.

## The gap this rule closes — read-before existed, archive-after didn't

`apps/extension/dev/app-builder/dev_change.py::api_dev_change` already reads
a target app's `INTENT.md` into the change-drafting prompt (read-before).
Nothing wrote back to it after a change shipped (archive-after) —
`api_backfill_intent` only *creates* a missing `INTENT.md` from scratch, on
demand, and refuses to touch one that already exists. So a living doc could
drift arbitrarily far from the code it's supposed to describe, with no
mechanism ever flagging or closing the gap.

**Lineage:** this is the one genuine workflow-sequencing gap surfaced by
evaluating [Fission-AI/OpenSpec](https://github.com/Fission-AI/OpenSpec)
(`docs/OPEN-SOURCE-BORROWING-PLAN.md` § Fission-AI/OpenSpec, 2026-08-22).
OpenSpec's `archive` step merges an approved change's spec delta back into
the durable base spec, atomically, after implementation. EmptyOS already had
more propose/spec machinery than OpenSpec on every other axis (`SandboxedWrite`,
`session-plans.md`, `docs/TRUST-LOOP.md`, `apps/public/standard/requirements/`)
— this was the one stage genuinely missing.

## The mechanism — `api_reconcile_intent`

`POST /app-builder/api/reconcile_intent` (`dev_change.py`), same draft/write
two-step shape as `api_backfill_intent`:

1. **Draft** (`{run_id}`) — reads the app's *existing* `INTENT.md`, the
   `dev_change` run's approved instruction, and the actual `git show` diff
   of the merged commit (scoped to the app's own directory). A `think()`
   call reconciles: preserve everything the diff doesn't touch, update only
   what it demonstrably changed. Returns the draft for review — never
   writes.
2. **Write** (`{run_id, intent_md}`) — the human-reviewed/edited text is
   saved verbatim.

Offered from the run card once a `kind: "dev"` run reaches `merged` or
`verified` status (`🔄 Reconcile INTENT.md` button, `apps/extension/dev/app-builder/pages/index.html`).
Same propose/preview/confirm posture as `SandboxedWrite`
(`.claude/rules/proposed-action.md`) — the reconcile step never auto-writes.

**Dark by default** — `[apps.app-builder] feature.intent-reconcile.enabled`
(default off, per `feedback_feature_pipeline_flag_default_dark`). The
endpoint refuses with a clear message when the flag is off; the button
always renders (cheap, no dead-UI cost) and the flag only gates the actual
LLM call + write.

## When to reconcile

- After a `dev_change` run merges and the change plausibly touched anything
  `INTENT.md` claims — a new `call_app` target, a new emit/listener, a
  behavior change the `## Why` paragraph no longer describes, an `## Open
  questions` item the diff answers, a `## Future` idea that just shipped.
- Not for every merge — a pure refactor or bugfix with no behavioral or
  relationship change has nothing to reconcile. The drafter is instructed to
  leave untouched sections verbatim rather than invent edits, so an
  unnecessary reconcile pass is cheap (a no-op draft), but don't make it
  reflexive ceremony.

## When NOT to use this

- **Scaffold runs** (`kind: "scaffold"`) — a brand-new app's `INTENT.md` is
  the birth certificate's direct descendant, not something to reconcile
  against a diff; `api_backfill_intent`'s create path already covers it.
- **An app with no existing `INTENT.md`** — use Backfill, not Reconcile;
  the reconcile prompt is explicitly a diff-against-existing-doc operation
  and refuses when there's nothing to reconcile against.
- **TRUST-LOOP `[assurance]` apps** — their `INTENT.md`/`ALGORITHM.md`
  reconciliation (if ever built) is a separate, formal discipline; don't
  route them through this endpoint.
- **CLAUDE.md rule-authoring itself** — a new `.claude/rules/*.md` file
  still lands ad hoc within the session that needed it, documented
  retrospectively by `/eos-session-wrapup`'s docs-sync step. Extending the
  archive-after discipline to rule-authoring is a distinct, larger change
  and out of scope here.

## Cross-references

- `docs/OPEN-SOURCE-BORROWING-PLAN.md` § Fission-AI/OpenSpec (2026-08-22) —
  full borrow verdict (build-nothing on the code axis, this rule is the one
  Axis-B artifact).
- `docs/DEFERRED-WORK.md` — the row this rule closes.
- `.claude/skills/eos-new-app/SKILL.md` Step 3.5 — the birth-time template
  this doc's living half descends from.
- `docs/TRUST-LOOP.md` — the separate, formal `INTENT.md` discipline for
  `[assurance]` apps.
- `.claude/rules/proposed-action.md` — the propose/preview/confirm posture
  `api_reconcile_intent` follows.
- `apps/public/standard/requirements/` — the general-purpose requirement
  object this rule's `INTENT.md` convention is adjacent to but distinct
  from (per-requirement lifecycle vs. one living free-text doc per app).
