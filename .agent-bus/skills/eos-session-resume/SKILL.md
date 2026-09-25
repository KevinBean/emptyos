---
name: eos-session-resume
description: Resume an EmptyOS session from where a previous track left off. Reads the per-track brief index at `{vault}/10_Projects/emptyos/log/_next/_index.md` (written by `/eos-session-wrapup` Step 5) and the chosen track's brief + most recent dated devlog, verifies referenced files still exist, then briefs the user with the recommended starting move. Use at the start of a fresh conversation when the user says "resume", "continue", "where did we leave off", "next session", "what's next", or simply pastes `/eos-session-resume`. Accepts an optional track slug — `/eos-session-resume career` opens the career brief; bare `/eos-session-resume` lists all active tracks and picks the most recently touched (or asks). NOT for closing a session out (use eos-session-wrapup) and NOT for a personal-life week (use eos-week-review).
---

# EmptyOS Session Resume

Cold-start the next session with the previous session's context intact. The companion skill to `/eos-session-wrapup` Step 6 — wrapup writes per-track briefs, this skill reads + acts on them.

## When to Use

- First turn of a fresh conversation in `D:\emptyos`
- User says "resume", "continue", "where did we leave off", "what's next", "next session"
- User invokes `/eos-session-resume` (optionally with a track slug: `/eos-session-resume career`)

## Process

### Step 0: Session-start probe (plans + bus)

Two read-only checks, run first because both feed decisions later in this skill.
One `Bash` call — they are independent:

```bash
python scripts/check_plan_staleness.py --json; python -m emptyos bus ripple --dry-run
```

**`check_plan_staleness.py`** — reports, per plan: a stale `active_task`, a claim
that disagrees with its row, unresolvable `depends_on`, dependency cycles, and a
plan whose tasks are all closed but which never moved to `_plans/done/`.
Exit 0 = clean. This runs *before* Step 1 on purpose: `stale_claim` is exactly
the judgment Step 1 case 1 asks you to make by hand ("is this plan held by a live
session, or did one die mid-task?"). Let the scanner answer it — a claim dated
today is live, one dated last week is not.

**`eos bus ripple --dry-run`** — exit 0 = `.agent-bus/` and the native
`.claude/` files agree; exit 1 = drift, meaning a spawned CLI would read a
different set of rules than this session does.

Both are **read-only and advisory**. Do not act on either without the user:

- **Never run `eos bus ripple` for real from this skill.** Ripple writes the
  *native* files FROM the store, so against a stale store its "changes" are
  deletions of rules that are fine. This has already been caught once — a
  dry-run proposed removing seven skill directories that had simply never been
  imported. Report the drift; `eos bus import` (the safe direction, native →
  store) is the user's call.
- **Never clear a stale claim silently.** Surface it and ask, per Step 1.

Skip silently when a check cannot run (no vault configured, `_plans/` absent,
command missing) — a fresh clone is healthy, not broken. Carry both results into
the Step 6 briefing; if both are clean, one line each is enough.

### Step 1: Check for an open session plan (bounded work claims a task)

Before touching the track index, glob:

```
{vault}/10_Projects/emptyos/log/_plans/*.md
```

A **session plan** is a bounded problem decomposed into ordered session-sized
tasks. If any plan is open, it takes precedence over free-form track selection —
its whole point is that the next task is already decided. Full contract:
`.claude/rules/session-plans.md`.

For each plan read the frontmatter (`plan`, `problem`, `active_task`, `track`)
and the `## Tasks` table, then:

1. **`active_task` non-empty** → that plan is **held by a live session**. Do NOT
   claim into it. Either the session is still running (pick a different plan or
   fall through to tracks) or it died mid-task. Judge by whether the plan's
   `session` column / the dated devlog shows matching work; surface it and ask.
   **Never silently steal a claim** — `active_task` is a mutual-exclusion marker,
   not a label.
2. **Otherwise** → select the first task with `status: queued` whose every
   `depends_on` id is `done`. That is this session's task.
3. **Empty `active_task` is not proof nobody is working.** A session started
   before the claim discipline reaches it (or one that skipped this skill)
   carries no marker, and `check_plan_staleness.py` can only see claims that
   exist. Before claiming a task whose row says `in-progress`/`active`-adjacent
   — or whose track was touched **today** in the index — check the **write
   frontier**: mtimes on the artifacts that task produces (for ingest, digest
   files under `30_Resources/conversations/`; generally, the task's output
   dir). Files minutes old = a live unclaimed session; do not claim, surface
   it and ask. Caught live 2026-08-07: two sessions independently picked the
   same five records and the unclaimed one's writes landed one minute ahead
   (`feedback_claim_check_write_frontier`).
4. **Claim it before doing any work**: write `active_task: <id>` into the plan's
   frontmatter and flip that row's `status` to `active`. The marker must land
   *before* the work starts — a vault file has no lock, so writing it after is
   the same read-check-then-act bug the plan tables themselves often describe.
5. If a plan has **no claimable task** (all `done`, or every remaining one is
   `blocked` / dependency-gated) → say so and recommend the close-out move: move
   a fully-done plan to `_plans/done/`, or surface what the `blocked` rows need
   from Kevin.

Then continue to Step 2 for the track brief that gives the *narrative* context
(the plan names its `track`). Plans carry the task; briefs carry the story — read
both.

**If no plan is open** → skip to Step 2 unchanged. Plans are for bounded
multi-task problems; unbounded work is track-driven, which is the existing and
still-correct default.

### Step 2: Read the index

```
{vault}/10_Projects/emptyos/log/_next/_index.md
```

The index lists every active track with its `last_touched` date and the file containing its brief. Tracks are parallel work threads — `em-engines`, `career`, `publish-site`, etc. — so multiple briefs can coexist without one wrapup clobbering another.

**If the index doesn't exist** but the legacy single-slot file does (`{vault}/10_Projects/emptyos/log/_next.md`), fall back to reading that and tell the user the per-track structure hasn't been migrated yet.

**If neither exists** → tell the user no brief was written by the last wrapup and ask how they'd like to start. Do NOT invent context.

### Step 3: Pick a track

Three cases:

1. **User passed a track slug** (`/eos-session-resume career`) → open `_next/<slug>.md` directly.
2. **Bare `/eos-session-resume`** with one track in the index → open it.
3. **Bare `/eos-session-resume`** with multiple tracks → list them sorted by `last_touched` desc, with each track's `last_session_title` as a one-liner. Default to the most recently touched, but offer the alternatives so the user can redirect:

   ```
   Active tracks:
     1. em-engines (touched 2026-05-02) — EM Roadmap Phase A + GitHub Reuse Sweep
     2. career (touched 2026-05-01) — Outreach log + career strategy capture

   Defaulting to em-engines (most recent). Reply with a number or track name to switch.
   ```

   Wait for confirmation before reading a brief in the multi-track case unless auto mode is on AND the most recent track is unambiguous.

If the chosen brief is older than 14 days → flag it ("brief is stale — N days old") and confirm before acting on its recommended move.

### Step 4: Read the dated log it points to

The brief's `last_session` frontmatter field names a date. Read `{vault}/10_Projects/emptyos/log/<date>.md` for fuller context — open threads in the brief are usually 1-line distillations of richer items in the dated log.

If the dated log is missing → still proceed using the brief alone, but note it.

### Step 5: Verify the brief is still actionable

The brief was written N hours/days ago. State may have moved. Before acting on it:

- For each file path mentioned in the brief (recommended starting move, TODO markers): check the file exists with `Read` or `Glob`. If a referenced file has been moved, deleted, or renamed since the brief was written, flag it.
- Run `git log --oneline --since="<last_session date>" --no-merges` to see if anything has been committed since the brief was written. If yes, those commits may have already advanced one of the open threads — possibly within a *different* track that ran wrapup later.
- Run `git status --short` to see if there are uncommitted changes — they're either work-in-progress on the recommended move or unrelated drift from a parallel track.
- If the brief has a `## Working-tree snapshot` section, diff its contents against current `git status --short`. Files in the snapshot but no longer in status = committed since wrapup (good — fold into the commit summary). Files in status but not the snapshot = drift from another track or post-wrapup work (flag — these aren't covered by the brief's recommendations and may entangle the recommended starting move). A clean snapshot with a dirty current status is the load-bearing signal that the recommended commit shape no longer matches the diff.

This is a **read-only** verification pass. Don't fix discrepancies yet — surface them in Step 6.

### Step 6: Brief the user

Output a tight summary in this shape (keep it under 20 lines unless there's real complexity):

When a plan task was claimed in Step 1, **lead with it** — it is the session's
single deliverable and everything else is context:

```
Claimed <plan-slug> · <task-id>: <task text>
  (task N of M; deps satisfied: <ids or none>)

This session does only that. Anything else found gets appended as a new task row.
```

Then the track context:

```
Resuming track <slug> from <last_session date> — <session title>.

Where things stood: <one paraphrased sentence from "Where things stand">

Open threads:
  1. <thread 1>
  2. <thread 2>
  ...

Recommended starting move: <verbatim from brief>

Verification:
  - <files exist / files moved / commits since>
  - <any uncommitted changes>
  - <stale-brief warning if applicable>
  - <other tracks active, in case the user wants to switch>

Want me to start with the recommended move, or pick a different thread / switch tracks?
```

If the recommended move references files that no longer exist, lead with that — don't bury it. Demote the recommendation and ask the user to redirect.

### Step 7: Wait for user direction

Don't auto-execute the recommended move, even in auto mode. The brief is from yesterday's Claude — the user gets to confirm or redirect now. Once the user says "go" / "yes" / "start there" / picks a different thread, proceed normally.

**Exception:** if auto mode is active AND the user invoked `/eos-session-resume <track>` with an explicit track slug AND the verification pass found zero blocking discrepancies, you MAY proceed directly to the recommended move after the briefing. Still print the briefing first so the user can interrupt.

## Output Format

Be terse. The user is reading this to remember context, not learn new things. Don't restate what's in the brief — paraphrase and verify. Lead with the most concrete actionable info, end with a single question.

## Edge Cases

- **Empty index**: only `_index.md` with no track files → tell the user the index exists but no track briefs have been written; suggest reading the most recent dated log instead.
- **Index points to missing track file**: e.g. `career.md` listed but the file was deleted. Skip that row, surface it as a warning, fall through to other tracks.
- **Brief points to a deleted file**: flag it prominently, ask if the work was completed and the file removed intentionally, or if something went wrong.
- **Commits in the diff that don't belong to the chosen track**: that's normal — another track ran wrapup on a different day. Don't treat them as discrepancies for *this* track.
- **User pastes the brief content directly into the chat**: skip Steps 2–3 and use the pasted content. Still do Steps 5+.
- **Legacy `_next.md` still present alongside `_next/`**: prefer the new structure; flag the stale single-slot file and offer to remove it.

## Vault Connection

This skill requires vault connection. Check `.claude/vault-connection.json` first.

## Relationship to Other Systems

- **Session plans** (`_plans/*.md`, `.claude/rules/session-plans.md`): carry the *task* (bounded, ordered, one per session). Track briefs carry the *story* (unbounded, prose). This skill claims a plan task in Step 1 and `/eos-session-wrapup` closes it. Plans take precedence when open — the next task is already decided, so there's nothing to choose.
- **`/eos-session-wrapup` Step 6**: writes the chosen track's `_next/<track>.md` and refreshes its row in `_index.md`. Without that step the brief doesn't exist.
- **Dated session logs** (`YYYY-MM-DD.md`): the source of truth for session detail. The brief is the on-deck card.
- **`git log` / `git status`**: authoritative state. Always trust these over the brief if they conflict.
- **Memory system**: persistent preferences and facts. Briefs are per-track per-session context; memory is across-session context. Both apply at session start.
