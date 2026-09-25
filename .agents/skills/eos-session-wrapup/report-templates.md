# eos-session-wrapup — report + document templates

Read this file when you reach the step whose template you need. The spine
(`SKILL.md`) owns the sequence, the routing, and the safety rules; this file
owns the exact output shapes.

---

## Step 1 — Docs Sync report

```
Docs Sync:
  Apps: 63 → 65 (+2)
  Endpoints: 687 → 695 (+8)
  Custom UIs: 63 → 65 (+2)
  Updated: CLAUDE.md (4 sections), release.toml (standard tier +2 apps)
  Public docs: README.md (app count), docs/APPS.md (new entries)
```

If counts match current docs → report "no changes needed", skip patching.

---

## Step 2 — Release Safety Check report

```
Safety Checks:
  Personal data: CLEAN (244 files, 11 patterns)
  Branding: CLEAN (244 files, 8 patterns)
```

Or if fixes were needed:

```
Safety Checks:
  Personal data: 2 violations fixed (docs/DESIGN.md, apps/public/standard/projects/app.py)
  Branding: CLEAN
```

---

## Step 3 — Vault Ripple report

```
Vault Ripple:
  Class A (personal): 1 fact — Job focus → external roles (5 notes updated)
  Class B (system):   2 facts —
    - Capability.all_providers() replaces inline chain-walk (kb_claim_audit: 0 broken refs)
    - <old pattern> superseded (1 KB lesson note flagged for review)
  Journal entry: 50_Journal/2026/<today>.md (Class A decisions logged)
```

Or if skipped:

```
Vault Ripple: no fact changes this session, skipped
```

---

## Step 3.5 — Knowledge Capture report

```
Knowledge: 1 candidate
  - A rendered-DOM audit must settle animations before measuring
    -> .claude/rules/audits.md (applied, lands in this session's commit)
```

Or, the common case:

```
Knowledge: none this session
```

Never pad this. One candidate is a good session; zero is the normal one.

---

## Step 4 — Dev Log entry

Write to `{vault}/10_Projects/emptyos/log/YYYY-MM-DD.md`:

```markdown
---
date: YYYY-MM-DD
type: dev-session
tags: [emptyos, dev-log, <affected-apps>]
skills_used: [eos-session-wrapup, ...]   # every /<skill> invoked this session
tracks:
  - <track-slug>   # the track(s) this session advanced — same slug(s) as Step 6
---

# YYYY-MM-DD — <Session Title>

## What Changed
- <bullet points summarizing what was built/fixed/improved>

## Files Modified
- <file paths with brief descriptions of changes>

## Result
<1-2 sentences on outcome and verification status>

## Learned                       # OMIT entirely when Step 3.5 found nothing
- <the one-sentence lesson> -> <where it was written>
```

### `skills_used` sourcing

List every `.claude/skills/*` (or `/<skill>`) invocation from this session, in
order, deduplicated. Include `eos-session-wrapup` itself. Skip CLI
command-palette hits that aren't skills. This field exists to correlate skill
use with outcome later — accumulate now, analyze when there's 4+ weeks of data
and a specific bet to test (e.g. "does `eos-page-design-review` cut UI
consolidation passes in half?"). No analyzer ships today; the data is just
grep-able in vault frontmatter.

### Format rules

- **Session title**: 3-5 words, action-oriented ("Cable App Growth", "Search Performance Fix")
- **What Changed**: bullets, each starting with the component name
- **Files Modified**: only list files with meaningful changes, not auto-generated
- **Result**: mention if tested, any console errors, visual verification
- **Tags**: include app IDs that were modified
- **Keep it brief**: 10-25 lines total. A record, not a tutorial.

### Multi-session days

If the log file already exists (second session same day), append with a separator:

```markdown
---

## Session 2: <Title>

### What Changed
...
```

---

## Step 5 — Site Sync report

```
Site Sync:
  apps.md: regenerated (74 apps)
  plugins.md: regenerated (9 plugins)
  capabilities.md: regenerated (7 capabilities)
  index.md: stats injected (74 apps, 9 plugins, 695 endpoints)
  architecture.md: app count updated (62→74)
  Build: triggered (or: skipped — daemon not running)
```

If no changes detected → report "Site: up to date, no changes."

---

## Step 6 — Next Session Brief

The brief's **frontmatter contract is in the spine** (`/eos-session-resume`
depends on it). This is the body template.

```markdown
---
type: next-session-brief
track: <track-slug>
purpose: <one line — what this track is FOR, stable across sessions>
written: <YYYY-MM-DD HH:MM>
last_session: <YYYY-MM-DD>
last_session_title: <Session Title>
threads_cleared: <N>
threads_added: <N>
threads_carried: <N>
---

# Next session — <on-deck title>

## Where things stand
<2-3 sentences. What was just shipped, what's confirmed working, what's still half-built.>

## Open threads
- <thread 1 — the thing you'd start with if you opened a fresh window>
- <thread 2 — secondary thread, may or may not get touched>
- <thread N — only if it actually matters>

## TODO markers touched this session
- `path/to/file.py:42` — <one-line on what the TODO asks>
<only include TODOs that were *added or touched* in this session's diff. Skip pre-existing TODOs you didn't interact with.>

## Recommended starting move
<ONE specific action — file path + what to do in it. Not "consider X" — "open X, do Y." If the user disagrees they redirect; if they don't, it's a clean cold-start.>

## Verification reminders
<Anything that needs a daemon restart, a test rerun, or a manual check before the next session declares something done. Skip if none.>

## Working-tree snapshot
<Paste the literal `git status --short` output at wrapup time, fenced. If empty, write `(clean)`. This lets `/eos-session-resume` detect drift between brief-time state and resume-time state — without it, a brief written after one track's session can be silently wrong if another track left uncommitted work in the same tree.>
```

### Sourcing rules

- **`purpose`**: what the track is FOR — the goal-level "why does this thread
  exist", NOT what the last session did. **Carry it forward verbatim** from the
  previous brief; author it once when creating a new track. Only rewrite it if
  the track's mission genuinely pivoted. Devboard's track detail renders it as
  the answer to "what is this task for".
- **Working-tree snapshot**: run `git status --short` and `git log --oneline -1`
  at wrapup time. Paste verbatim (no editing). The next resume's verification
  pass diffs this snapshot against current state to surface "the working tree
  changed since this brief was written" — usually because another track ran
  wrapup between this brief and the next resume.
- **Where things stand**: distill from the just-written devlog's "Result"
  section + any `Flagged (needs your call)` items from a prior `/eos-simplify` pass.
- **Open threads**: re-read the session devlog you just wrote — anything phrased
  as "follow-up", "want me to", "out of scope", or "deferred" is an open thread.
  Don't invent threads that aren't real.
- **TODO markers**: `git diff HEAD` for `TODO|FIXME|XXX|TODO\(extract\)` lines
  that **didn't exist** before this session. Use `git diff` not `git log` so
  unstaged work is included.
- **Recommended starting move**: prefer the most concrete open thread. If none
  stands out, leave the section as `(no specific recommendation — start by
  reading {dated log path})`. Don't manufacture a move just to fill the slot.
- **Thread counts** (`threads_cleared` / `threads_added` / `threads_carried`):
  - `threads_cleared`: read the previous brief for this track (`_next/<track>.md`
    *before* overwriting); count how many of its "Open threads" bullets are
    resolved by this session's work. If there's no previous brief, this is `0`.
  - `threads_added`: count "Open threads" bullets in *this* brief that weren't
    in the previous one.
  - `threads_carried`: total bullets in this brief's "Open threads" section
    (resolved threads should already be omitted per the skip rules).
  - These don't have to be exact — they're trend signals over many sessions, not
    metrics. Skip-or-zero when ambiguous. Same compounding-data purpose as
    `skills_used`: no analyzer today, but the fields make future analysis a grep.

### Index update

After writing the track brief, update `_index.md`:

- Refresh that track's row with new `last_touched` date and `last_session` title.
- Add a row if it's a brand-new track.
- Don't touch other tracks' rows.

### Report

```
Next Session Brief: {vault}/10_Projects/emptyos/log/_next/<track>.md (track: <slug>)
  Open threads: 3 | TODO markers: 1 | Carried over: 0
  Other tracks (untouched): em-engines (2026-05-02), publish-site (2026-04-28)
```

If skipped (trivial session): `Next Session Brief: skipped — trivial session`.

---

## Step 7 — Commit & Push report

```
Commit & Push:
  Staged: 4 files (scoped to this session)
  Skipped: 1 file (belongs to a parallel session — left for owner)
  Commit: a1b2c3d "fix(memory): trim index + repair moved anchors"
  Push: origin/main up to date
```

If nothing scoped to this session → "Commit: nothing to commit, skipped."

---

## Step 8 — Live-Verify report

```
Live-Verify:
  Daemon: :9000 up
  Checked: GET /<app>/api/... → 200, expected shape
  (or) Daemon offline — asked Kevin to restart / verified on sandbox :9002
  (or) Static-only change (pages/) — no restart needed
```

---

## Step 9 — Final summary

```
Session Wrapup Complete:
  Docs:   CLAUDE.md updated (apps 63→65, endpoints 687→695)
  Safety: CLEAN (personal + branding)
  Ripple: 2 facts surfaced, 5 vault notes updated  (or: skipped — no fact changes)
  Know:   1 lesson -> .claude/rules/audits.md  (or: none this session)
  Log:    10_Projects/emptyos/log/2026-04-12.md written
  Site:   regenerated (74 apps, 9 plugins) — rebuild triggered
  Next:   10_Projects/emptyos/log/_next/<track>.md written (3 open threads)
          Other tracks untouched: <track1> (last touched <date>), <track2> (last touched <date>)
  Commit: a1b2c3d "fix(memory): trim index + repair anchors" — pushed origin/main
  Verify: :9000 up — GET /... 200  (or: daemon offline, asked Kevin to restart; or: static-only)
```

Suggest as a follow-up at next session start: `/eos-session-resume` (or
`/eos-session-resume <track>` to jump straight to a specific track).
