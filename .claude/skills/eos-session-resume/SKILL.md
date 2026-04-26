---
name: eos-session-resume
description: Resume an EmptyOS session from where the last one ended. Reads `{vault}/10_Projects/emptyos/log/_next.md` (written by `/eos-session-wrapup` Step 5) and the most recent dated devlog, verifies referenced files still exist, then briefs the user with the recommended starting move. Use at the start of a fresh conversation when the user says "resume", "continue", "where did we leave off", "next session", "what's next", or simply pastes `/eos-session-resume`.
---

# EmptyOS Session Resume

Cold-start the next session with the previous session's context intact. The companion skill to `/eos-session-wrapup` Step 5 — wrapup writes the brief, this skill reads + acts on it.

## When to Use

- First turn of a fresh conversation in `D:\emptyos`
- User says "resume", "continue", "where did we leave off", "what's next", "next session"
- User invokes `/eos-session-resume`

## Process

### Step 1: Read the brief

```
{vault}/10_Projects/emptyos/log/_next.md
```

If the file doesn't exist → tell the user no brief was written by the last wrapup and ask how they'd like to start. Do NOT invent context.

If the file exists but is older than 14 days → flag it ("brief is stale — N days old") and confirm with the user before acting on its recommended move.

### Step 2: Read the dated log it points to

The brief's `last_session` frontmatter field names a date. Read `{vault}/10_Projects/emptyos/log/<date>.md` for fuller context — open threads in the brief are usually 1-line distillations of richer items in the dated log.

If the dated log is missing → still proceed using the brief alone, but note it.

### Step 3: Verify the brief is still actionable

The brief was written N hours/days ago. State may have moved. Before acting on it:

- For each file path mentioned in the brief (recommended starting move, TODO markers): check the file exists with `Read` or `Glob`. If a referenced file has been moved, deleted, or renamed since the brief was written, flag it.
- Run `git log --oneline --since="<last_session date>" --no-merges` to see if anything has been committed since the brief was written. If yes, those commits may have already advanced one of the open threads.
- Run `git status --short` to see if there are uncommitted changes — they're either work-in-progress on the recommended move or unrelated drift.

This is a **read-only** verification pass. Don't fix discrepancies yet — surface them in Step 4.

### Step 4: Brief the user

Output a tight summary in this shape (keep it under 20 lines unless there's real complexity):

```
Resuming from <last_session date> — <session title>.

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

Want me to start with the recommended move, or pick a different thread?
```

If the recommended move references files that no longer exist, lead with that — don't bury it. Demote the recommendation and ask the user to redirect.

### Step 5: Wait for user direction

Don't auto-execute the recommended move, even in auto mode. The brief is from yesterday's Claude — the user gets to confirm or redirect now. Once the user says "go" / "yes" / "start there" / picks a different thread, proceed normally.

**Exception:** if auto mode is active AND the user invoked `/eos-session-resume` AND the verification pass found zero blocking discrepancies, you MAY proceed directly to the recommended move after the briefing. Still print the briefing first so the user can interrupt.

## Output Format

Be terse. The user is reading this to remember context, not learn new things. Don't restate what's in the brief — paraphrase and verify. Lead with the most concrete actionable info, end with a single question.

## Edge Cases

- **No `_next.md` exists**: tell the user, suggest reading the most recent dated log instead, ask what they want to work on.
- **Brief points to a deleted file**: flag it prominently, ask if the work was completed and the file removed intentionally, or if something went wrong.
- **Multiple sessions ran since the brief was written** (commits since `last_session` date): the brief may be obsolete. Read the most recent dated log instead and re-derive open threads from it.
- **User pastes the brief content directly into the chat**: skip Step 1 and use the pasted content. Still do Steps 3+.

## Vault Connection

This skill requires vault connection. Check `.claude/vault-connection.json` first.

## Relationship to Other Systems

- **`/eos-session-wrapup` Step 5**: writes `_next.md`. Without that step the brief doesn't exist.
- **Dated session logs** (`YYYY-MM-DD.md`): the source of truth for session detail. The brief is the on-deck card.
- **`git log` / `git status`**: authoritative state. Always trust these over the brief if they conflict.
- **Memory system**: persistent preferences and facts. The brief is per-session context; memory is across-session context. Both apply at session start.
