---
paths:
  - "apps/**"
  - "emptyos/web/static/**"
---
# List/Card Density Rule — enough to decide before you click

A summary row (kanban card, list item, dashboard tile) exists to answer one
question: does the user need to open this? If the answer usually requires
opening it, the row has failed at its one job. This rule names the minimum
content contract every summary card/list-item across EmptyOS should satisfy,
and gives a litmus test for judging when a card under-informs.

**Reference implementation:** `apps/public/standard/projects/` list + kanban
cards (`EOS_UI.entityCard` with status badge + progress + deadline +
staleness all visible pre-click).

## The contract

Every summary row for an entity with a lifecycle SHOULD carry, without a click:

| Slot | Answers | Example |
|---|---|---|
| **Identity** | What is this? | title, or date+weekday for a day-shaped entity |
| **Status** | What state is it in? | a labelled badge — text, never color alone |
| **The one number/date that matters** | Does this need me, how urgently? | due date, progress %, count, hours |
| **A one-line content snippet** *(only if the entity carries free-text body content)* | What's actually going on? | first line of notes/plan/update, truncated ~100-120 chars |

Not every entity has all four — a homogeneous checkbox list doesn't need a
status badge if the checkbox already communicates it. But when a field IS
available server-side and the card omits it, that's the bug this rule names.

## The litmus test

> If clicking into the item almost always reveals a fact the user needed in
> order to decide whether to click, the card under-informs.

Ask this before shipping a new list/kanban/gallery card, and when reviewing
an existing one during a design audit pass.

## Status must never be color-only

A colored border, dot, or background communicates severity to someone who has
already learned the color key — and communicates nothing to a first glance, a
screenshot, or a colorblind user. Every status/urgency signal needs a
co-located word: a labelled badge (`EOS_UI.statusBadge`/`.eos-badge-status-*`,
or an app's own text-bearing badge family like jobs' `.sb-<status>`), a
section header that already names the state, or a short text token like
"3d overdue"/"stale". A tier communicated ONLY by CSS class (no text anywhere
on the row) fails this rule.

This sharpens (does not replace) `docs/FRONTEND-DESIGN-LANGUAGE.md` §10's "no
decorative status dots" — that rule bans a dot duplicating an already-visible
label; this rule bans a color that is the ONLY signal.

## How to compose it

- **New cards default to `EOS_UI.entityCard`** — `meta` for the one
  number/date, `body` for the snippet.
- **Existing well-tuned hand-rolled cards** (journal's `.recent-day`,
  worklog's `.day-card`) don't need to migrate to `entityCard` to comply —
  the contract is about content, not markup. Add the missing field(s).
- **Don't cram every field that exists.** Pick the one number/date that
  actually drives the "should I click" decision — see
  `docs/FRONTEND-DESIGN-LANGUAGE.md` §1/§2 (whitespace, density) and §4
  (tint, don't flood).
- **Boards/kanban/gallery presets are config-driven**
  (`.claude/rules/boards-as-view-layer.md`) — a preset author must
  explicitly wire the field knobs; the generic view layer can't know which
  fields matter for a given app's data.

## Enforcement

Judgment-heavy ("is the density right for this entity") — not scanner-safe
per `.claude/rules/audits.md` (false-positive risk on par with the
error-state check, which stays advisory). Registered as a manual checklist
item in the `eos-design-system-audit` skill rather than a new automated
gate. Known gaps found 2026-08-21, fixed this pass: worklog (no content
snippet), task (undated stale/zombie tasks — color-only tier), jobs (no
browsable application list), boards' worklog preset (kanban meta didn't
surface employer).

**A shared renderer's default can be the violation** (worklog Calendar,
2026-08-31). `EOS_UI.monthGrid`'s built-in `renderCell` draws only
`.eos-mg-dot.tone-*` — it ignores each item's `label` and sets no `title` — so
a caller that passes no `renderCell` communicates status by colour alone no
matter how good its own payload is. worklog's was worse than unlabelled: `tone`
collapses seven statuses to three colours and reads backwards to an untrained
eye (a *complete* day painted muted grey, a *blocked* day red), with no word
anywhere on the page and no legend. The fix was a `renderCell` emitting the
status word, plus carrying that word in the payload — `tone` alone is lossy, so
a cell rendered from it cannot say what it means. **When auditing this rule,
check what a shared component renders by default, not only what the app passes
it.**

## Cross-references
- `.claude/rules/app-ui-patterns.md`, `.claude/rules/shared-frontend.md` —
  `EOS_UI.entityCard`/`statusBadge` component contracts this builds on.
- `.claude/rules/boards-as-view-layer.md` — preset authors own field wiring.
- `docs/FRONTEND-DESIGN-LANGUAGE.md` §9 — the philosophy; this is the
  enforceable content contract underneath it.
- `.claude/rules/audits.md` — why this ships as a skill checklist item.
