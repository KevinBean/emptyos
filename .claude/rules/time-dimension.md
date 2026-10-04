# Time Dimension Rule — past → future → now

A thing in EmptyOS is never just its current shape. It has a **past** (how
it got here), a **future** (what's already committed about it), and a
**now** (what's true this instant). The vault, the daemon, and any agent
acting on either must treat time as a first-class dimension alongside
folder / tag / link.

This rule has two halves:

1. **Vault convention** — a note carries enough time-depth that anyone
   reading it can answer "where did this come from? where is it going?"
   without external lookup.
2. **Action discipline** — before any non-trivial mutation (mine, an
   agent's, an app's), read the past, read the future, *then* do the
   thing now. Most "did the system regress?" failures are skipping
   step 1; most "did the agent collide with something already
   scheduled?" failures are skipping step 2.

## Where time data already lives — don't duplicate

Before designing a new time field, check whether one of these already
answers your question. The 4D view is **aggregation, not a new store**.

| Past (what already happened)        | Future (what's already committed)    | Now (what's true)        |
|-------------------------------------|--------------------------------------|--------------------------|
| `created:` frontmatter              | `due:` / `due_ts:` frontmatter       | `status:` frontmatter    |
| `updated:` frontmatter              | `expires_at:` (autopilot grants)     | `lifecycle:` (living/snapshot/archived) |
| `git log` (vault history)           | Scheduler jobs (`/api/scheduler/jobs`) | VaultIndex (current snapshot) |
| `data/syslog.db` (event history)    | Reminders app (`reminders:upcoming`) | The note body            |
| Reactor breadcrumbs in daily journal| Pending actions (`data/apps/rooms/pending/`) | Live capability state    |
| `## Timeline` section in the note   | Countdown panels (`countdowns:upcoming`) |                          |
| `## Log` directory under projects   | Cron entries (CronList)              |                          |

If the answer isn't in one of these, *that's* where new time data goes.
Don't invent a parallel field.

## Vault convention

### Single-event notes

Every note SHOULD carry `created:` (immutable) and `updated:` (mutates
on each write). `BaseApp.vault_update()` and `vault_create_note()` set
these automatically — don't override.

### Notes with a story

Notes that accumulate events over time (job applications, projects,
people, jobs-to-be-done, ongoing decisions) SHOULD use the `## Timeline`
section pattern already in CLAUDE.md § Vault Data Layer:

```markdown
## Timeline
- 2026-04-02 — applied
- 2026-04-08 — recruiter screen scheduled (see [[journal/2026-04-08]])
- 2026-04-15 — first interview
```

One date-prefixed bullet per event, newest at the bottom. Cross-link to
the daily journal entry where the breadcrumb landed; the journal is the
machine-written log, the timeline is the human-curated summary.

### Notes with a future

Notes that have committed-but-unrealized future state SHOULD declare it
in frontmatter, not prose:

```yaml
---
due: 2026-05-20
expires_at: 2026-06-01T00:00:00
next_review: 2026-05-23
---
```

The scheduler / reminders / SRS apps read these directly. A future date
hidden inside `## Notes` prose is invisible to the daemon.

### Notes that are snapshots

Snapshot notes (income at a point in time, vault audit at a date) MUST
carry `lifecycle: snapshot` + `as_of: <date>` in frontmatter. The vault
lifecycle classifier already handles the gate (frozen on write); the
`as_of` date is what makes the snapshot answerable as a past-state.

## Action discipline — past → future → now

Before any non-trivial action on a thing, fetch all three. "Non-trivial"
means anything that mutates state the user cares about, anything an
agent emits via `[DO:]`, or anything I do that isn't a pure read.

### 1. Past — how did we get here?

- `vault_get_properties(path)` for `created`/`updated`
- `## Timeline` section if present
- `git log -- <path>` for files under the vault or repo
- `syslog` filter for events touching this entity
- Memory: grep `MEMORY.md` for prior decisions about this thing

Skip-cost: writing over yesterday's fix because I never read the
breadcrumb. Most "the system regressed" incidents come from this.

### 2. Future — what's already committed?

- `due_ts` / `expires_at` / `next_review` on the note
- Scheduler: any job referencing this entity (`/api/scheduler/jobs`)
- Reminders app for upcoming pings
- Pending actions in `data/apps/rooms/pending/` (an action already
  proposed but not yet applied)
- Autopilot grants (`data/autopilot/grants.json`) whose `verb_pattern`
  covers what I'm about to do

Skip-cost: an agent fires the same `[DO:]` that's already pending
review; the user sees two cards; trust in the gate erodes. Or: I propose
a fix to a file the user has already staged a different fix for.

### 3. Now — only then act

After reading past + future, the action shape is usually clearer:
sometimes it's *don't act* (already scheduled), sometimes it's *narrow
the scope* (history shows a piece is already done), sometimes it's
*ask the user* (past + future are in conflict).

Acting first and reconciling after is the autopilot anti-pattern this
rule rejects.

## What this means for me (Claude in conversation mode)

- **Before editing a file**: `git log -n 5 -- <path>` + grep MEMORY for
  the file's slug. Two lines of bash, prevents half the "didn't you
  remove this last week?" surprises.
- **Before proposing a `[DO:]` or running an app verb**: check if a
  pending action / scheduled job / reminder already covers it.
- **Before suggesting a feature**: read git log for the area + grep
  MEMORY for related project/feedback memories. Future-check: is this
  already on a roadmap entry or a `_next.md` from a prior session?
- **Before committing**: re-read `git status --short` to see if the
  user (or another session) has staged future-shape work I didn't
  expect. Per `.claude/rules/environment.md`.
- **Before answering "what's the state of X"**: don't recite memory
  alone — memory is past-shape and decays. Triangulate against current
  vault/code/git, and update or remove stale memory entries the same
  turn (per CLAUDE.md § auto memory § "Before recommending from memory").

## What this means for apps

- An app that owns a thing with a story SHOULD render past + future
  alongside now. The job-application detail view, the project detail
  view, the person detail view — each one is a 4D view of one entity.
  If your app's detail page only shows the current snapshot, you're
  missing two thirds of what the user came for.
- An app that proposes a future action SHOULD write it where future-
  reads will find it (scheduler / reminders / `expires_at` / pending
  queue), not buried in note prose.
- An app that mutates a thing SHOULD append a breadcrumb to the
  thing's `## Timeline` (or emit an event the reactor turns into a
  journal breadcrumb).

## SDK extraction trigger

The aggregator `BaseApp.timeline(entity_id_or_path) -> {past, future, now}`
is the natural extraction. **Don't build it yet** — CLAUDE.md rule 9
applies. Build the specific past/future read for the first consumer
that needs all three at once (likely a "4D detail view" panel on the
projects or jobs detail page), then extract once a second app wants
the same shape.

Existing time-shaped surface to unify with when extracting:
`apps/public/standard/projects/extended.py::api_timeline` — Gantt view across all
projects (a "many entities at once" cut). The per-entity `timeline()`
aggregator is the orthogonal cut; both should pull from the same source
list in this file when the SDK helper lands.

Until then: each app reads from the existing sources directly. The
aggregation is human-shaped today (this rule); it becomes code-shaped
when an app proves the need.

## Anti-patterns

- **Adding a `time:` block to frontmatter that duplicates `created`/
  `updated`/`due`.** The existing fields are already indexed; a parallel
  block just splits read paths.
- **Writing a future-date string into `## Notes` prose.** Invisible to
  the daemon. Future dates belong in frontmatter or scheduler.
- **Treating MEMORY.md as the past.** It's *a* past view, biased toward
  what surprised me. The vault + git are authoritative.
- **Doing the action and writing the breadcrumb together in one shot,
  before checking the existing past.** That's how regressions land.
- **Reading past + future and then doing the obvious thing without
  reconciling conflicts.** If the past says "we tried this and it
  failed" and the future says "scheduled to try again next week,"
  surface that to the user; don't pick one silently.

## Cross-references

- CLAUDE.md § Vault Data Layer — three-layer structure where `## Timeline`
  lives
- CLAUDE.md § auto memory — memory is past-shape with decay; verify
  before recommending
- `.claude/rules/proposed-action.md` — the propose / preview / confirm
  paradigm; staleness check is exactly the "did the past move?" question
- `.claude/rules/autopilot-grants.md` — grants have `expires_at`; reaping
  expired grants is future-side hygiene
