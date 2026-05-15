---
name: eos-session-resume
description: Resume an EmptyOS session from where a previous track left off. Reads the per-track brief index at `{vault}/10_Projects/emptyos/log/_next/_index.md` (written by `/eos-session-wrapup` Step 5) and the chosen track's brief + most recent dated devlog, verifies referenced files still exist, then briefs the user with the recommended starting move. Use at the start of a fresh conversation when the user says "resume", "continue", "where did we leave off", "next session", "what's next", or simply pastes `/eos-session-resume`. Accepts an optional track slug — `/eos-session-resume career` opens the career brief; bare `/eos-session-resume` lists all active tracks and picks the most recently touched (or asks).
---

# EmptyOS Session Resume

Cold-start the next session with the previous session's context intact. The companion skill to `/eos-session-wrapup` Step 6 — wrapup writes per-track briefs, this skill reads + acts on them.

## When to Use

- First turn of a fresh conversation in `D:\emptyos`
- User says "resume", "continue", "where did we leave off", "what's next", "next session"
- User invokes `/eos-session-resume` (optionally with a track slug: `/eos-session-resume career`)

## Process

### Step 1: Read the index

```
{vault}/10_Projects/emptyos/log/_next/_index.md
```

The index lists every active track with its `last_touched` date and the file containing its brief. Tracks are parallel work threads — `em-engines`, `career`, `publish-site`, etc. — so multiple briefs can coexist without one wrapup clobbering another.

**If the index doesn't exist** but the legacy single-slot file does (`{vault}/10_Projects/emptyos/log/_next.md`), fall back to reading that and tell the user the per-track structure hasn't been migrated yet.

**If neither exists** → tell the user no brief was written by the last wrapup and ask how they'd like to start. Do NOT invent context.

### Step 2: Pick a track

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

### Step 3: Read the dated log it points to

The brief's `last_session` frontmatter field names a date. Read `{vault}/10_Projects/emptyos/log/<date>.md` for fuller context — open threads in the brief are usually 1-line distillations of richer items in the dated log.

If the dated log is missing → still proceed using the brief alone, but note it.

### Step 4: Verify the brief is still actionable

The brief was written N hours/days ago. State may have moved. Before acting on it:

- For each file path mentioned in the brief (recommended starting move, TODO markers): check the file exists with `Read` or `Glob`. If a referenced file has been moved, deleted, or renamed since the brief was written, flag it.
- Run `git log --oneline --since="<last_session date>" --no-merges` to see if anything has been committed since the brief was written. If yes, those commits may have already advanced one of the open threads — possibly within a *different* track that ran wrapup later.
- Run `git status --short` to see if there are uncommitted changes — they're either work-in-progress on the recommended move or unrelated drift from a parallel track.
- If the brief has a `## Working-tree snapshot` section, diff its contents against current `git status --short`. Files in the snapshot but no longer in status = committed since wrapup (good — fold into the commit summary). Files in status but not the snapshot = drift from another track or post-wrapup work (flag — these aren't covered by the brief's recommendations and may entangle the recommended starting move). A clean snapshot with a dirty current status is the load-bearing signal that the recommended commit shape no longer matches the diff.

This is a **read-only** verification pass. Don't fix discrepancies yet — surface them in Step 5.

### Step 5: Brief the user

Output a tight summary in this shape (keep it under 20 lines unless there's real complexity):

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

### Step 6: Wait for user direction

Don't auto-execute the recommended move, even in auto mode. The brief is from yesterday's Claude — the user gets to confirm or redirect now. Once the user says "go" / "yes" / "start there" / picks a different thread, proceed normally.

**Exception:** if auto mode is active AND the user invoked `/eos-session-resume <track>` with an explicit track slug AND the verification pass found zero blocking discrepancies, you MAY proceed directly to the recommended move after the briefing. Still print the briefing first so the user can interrupt.

## Output Format

Be terse. The user is reading this to remember context, not learn new things. Don't restate what's in the brief — paraphrase and verify. Lead with the most concrete actionable info, end with a single question.

## Edge Cases

- **Empty index**: only `_index.md` with no track files → tell the user the index exists but no track briefs have been written; suggest reading the most recent dated log instead.
- **Index points to missing track file**: e.g. `career.md` listed but the file was deleted. Skip that row, surface it as a warning, fall through to other tracks.
- **Brief points to a deleted file**: flag it prominently, ask if the work was completed and the file removed intentionally, or if something went wrong.
- **Commits in the diff that don't belong to the chosen track**: that's normal — another track ran wrapup on a different day. Don't treat them as discrepancies for *this* track.
- **User pastes the brief content directly into the chat**: skip Steps 1–2 and use the pasted content. Still do Steps 4+.
- **Legacy `_next.md` still present alongside `_next/`**: prefer the new structure; flag the stale single-slot file and offer to remove it.

## Vault Connection

This skill requires vault connection. Check `.claude/vault-connection.json` first.

## Relationship to Other Systems

- **`/eos-session-wrapup` Step 6**: writes the chosen track's `_next/<track>.md` and refreshes its row in `_index.md`. Without that step the brief doesn't exist.
- **Dated session logs** (`YYYY-MM-DD.md`): the source of truth for session detail. The brief is the on-deck card.
- **`git log` / `git status`**: authoritative state. Always trust these over the brief if they conflict.
- **Memory system**: persistent preferences and facts. Briefs are per-track per-session context; memory is across-session context. Both apply at session start.
