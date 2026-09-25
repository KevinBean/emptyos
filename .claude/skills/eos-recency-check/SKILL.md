---
name: eos-recency-check
description: Ground a task in what has actually changed recently — across git commits, vault journal/devlog entries, project trackers, and AI-conversation digests. Use when the user says "check recent notes", "what's changed lately", "catch me up", "check recent information", "before you update X check what's new". Distinct from eos-insights (a full periodic EmptyOS dev-trend synthesis report with a ledger/scorecard) and eos-life-insights.
---

# Recency Check

A "read the past before acting" utility (`.claude/rules/time-dimension.md`,
`.claude/rules/deep-research.md` move 1). Before updating a doc that claims to
describe *current* reality, find out what actually changed since it was last
true, and hand back a short cited digest — not a report, not a synthesis, just
grounding.

## When to use

- Immediately before updating branding/positioning/messaging copy, a strategy
  note, a project status doc, or anything else that asserts "this is where
  things stand."
- User asks "what's changed lately", "catch me up", "check recent notes/
  information" with no specific target doc — use a stated or default window.
- Called out as an explicit prerequisite by another skill or by the user
  ("before that, check...").

Do **NOT** use for:
- A full monthly dev-trend synthesis with a ledger/scorecard → `/eos-insights`.
- Personal wellbeing-wheel life reflection → `/eos-life-insights`.
- A one-line git log lookup with no need for cross-source synthesis — just run
  the command.

## Inputs

- **Target doc(s)** (optional) — the file(s) whose staleness is in question.
  If given, the reference date is that file's last *meaningful* edit (skip
  trivial commits like typo fixes if the log makes that obvious).
- **Window** (optional) — an explicit lookback ("last 14 days") when there's
  no single target doc, or to override the target-doc-derived date.
- If neither is given, default to the last 14 days.

## Process

### Step 1 — Resolve the reference date

- Repo file: `git log -1 --format="%ad %s" --date=short -- <path>` (do this
  per target doc; use the earliest if several).
- Vault note: file mtime, or its `updated:`/frontmatter date if present.
- No target: `today - window`.

### Step 2 — Gather across four lanes, bounded by that date

Read bodies, don't just list filenames — a filename tells you something
touched a topic, not what changed.

1. **Git** — `git log --since="<date>" --pretty=format:"%ad %s" --date=short`
   filtered to `feat:`/`fix:`/`security:` (skip `chore:`/merge noise). Group
   by area (app/plugin/engine touched). This is "what shipped."
2. **Vault journal** (`{vault}/50_Journal/**`) — entries with mtime after the
   reference date. Skim for anything narratively significant: a decision, a
   milestone, a status change, a new fact.
3. **Session devlogs** (`{vault}/10_Projects/emptyos/log/YYYY-MM-DD.md`) —
   files after the reference date; read the "What changed"/"Result" sections
   (same extraction the `promote` app's candidate scanner uses).
4. **Project trackers + memory** — grep `{vault}/10_Projects/**/*.md` tagged
   `type: project` with recent mtime, and cross-check the auto-memory index
   (`MEMORY.md`) for entries whose content is dated after the reference date.
   Memory decays (per CLAUDE.md § auto memory) — verify anything load-bearing
   against the vault/code before trusting it, don't just recite it.
5. **AI-conversation digests** — vault notes produced by
   `eos-ai-conversation-ingest` / `vault-ai-conversation-digest` (reference
   source notes + distilled KB notes) with mtime after the reference date —
   these are where a pasted conversation's decisions/feedback live.

Skip a lane cleanly (say so) if its source doesn't exist for this vault/repo —
never fabricate a finding to fill a lane.

### Step 3 — Synthesize the digest

A short, cited bullet list, grouped by lane, newest first within each group:

```
## What's changed since <date>

**Shipped (git)**
- <one line> — <path/commit>, <date>

**Vault / journal**
- <one line, with the actual fact, not just "touched"> — <note path>, <date>

**Strategy / status**
- ⚠ <material status change — a fact that would make an existing "current
  state" claim wrong> — <source>, <date>
```

Mark a bullet ⚠ only when it's a genuine status change a stale doc would get
*wrong* if left unedited (a completed milestone, a reversed decision, a
number that moved) — not every commit deserves the marker.

### Step 4 — Hand back, don't act

This skill's output is grounding input for whatever asked for it. It never
edits a file itself — the calling task (or the user) decides what to do with
the digest. If invoked standalone with no follow-on task, just print the
digest.

## Anti-patterns

- **Tabulating instead of reading.** A commit-count table with no read of the
  actual bodies is not a grounding digest — it's the count-only failure mode
  `.claude/rules/deep-research.md` names explicitly.
- **Treating memory as ground truth.** A memory file can be stale; the vault
  and git are authoritative. Cross-check before citing.
- **Turning this into a report.** No output file, no ledger, no scorecard —
  that's `eos-insights`'s job. This is a fast prerequisite pass.
