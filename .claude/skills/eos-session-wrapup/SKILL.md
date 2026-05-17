# EmptyOS Session Wrapup

End-of-session housekeeping: sync docs, check for personal data leaks, then record what happened. Run all steps in sequence — docs-sync first (so CLAUDE.md reflects current state), safety checks second (catch leaks before they persist), then devlog (so the log references accurate numbers).

## When to Use

- End of a meaningful development session
- User says "wrapup", "wrap up", "session done", "devlog", "docs-sync", "sync docs"
- After adding/removing apps or plugins
- After any session that changed code

## Process

### Step 1: Docs Sync — Update CLAUDE.md to match reality

Scan the codebase and patch documentation sections that are derived from code.

#### 1a. Scan Manifests

```bash
# Count all apps (core + personal)
find apps/ apps/personal/ -name "manifest.toml" -not -path "*/_retired/*" 2>/dev/null | wc -l

# Count plugins
find plugins/ -name "manifest.toml" 2>/dev/null | wc -l

# Count endpoints (web_route decorators)
grep -r "@web_route" apps/ apps/personal/ plugins/ --include="*.py" 2>/dev/null | wc -l

# Count apps with custom UI
find apps/ apps/personal/ -name "index.html" -path "*/pages/*" -not -path "*/_retired/*" 2>/dev/null | wc -l

# Count topology edges (app-to-app dependencies)
grep -r "^apps = " apps/ apps/personal/ --include="*.toml" -not -path "*/_retired/*" 2>/dev/null
```

#### 1b. Build App Table

For each manifest.toml, extract:
```python
import tomllib
with open(manifest_path, "rb") as f:
    m = tomllib.load(f)
app_id = m["app"]["id"]
app_name = m["app"]["name"]
has_ui = (manifest_path.parent / "pages" / "index.html").exists()
```

#### 1c. Sync Release Manifest

If apps or plugins changed, update `release.toml` tier lists:

```bash
# Check if new community apps exist that aren't in any tier
for d in apps/*/; do
    id=$(basename "$d")
    grep -q "\"$id\"" release.toml || echo "NOT IN RELEASE: $id"
done
```

New community apps should be added to the appropriate tier in `release.toml`:
- **core**: infrastructure essentials (capture, note, task, search, link, settings, system-log, run)
- **standard**: everything else that's generic and community-ready

Run `python scripts/package-release.py --check` to verify tiers resolve correctly.

#### 1d. Sync Public Docs

If the session changed architecture, capabilities, or app inventory:
- **README.md**: update app counts, tier table, capability list if changed
- **docs/GETTING-STARTED.md**: update if config format or setup flow changed
- **docs/APP-DEVELOPMENT.md**: update if SDK API signatures, decorators, or manifest format changed
- **docs/APPS.md**: update if apps were added, removed, or recategorized

Only update sections with factual changes (counts, lists, API signatures). Don't rewrite prose.

#### 1e. Patch CLAUDE.md

Use exact string replacement on these patterns:

1. **Architecture box**: `Apps (N, ALL first-class` → update N
2. **Section header**: `## N Apps` → update N
3. **Topology**: `Live dependency graph (N nodes, M edges)` → update N, M
4. **What's Done header**: `### N Apps (M with custom UI pages), K endpoints` → update all
5. **Plugin header**: `### N Plugins` → update N
6. **App list** (under "What's Done"): regenerate full comma-separated list

#### 1f. Report Changes

```
Docs Sync:
  Apps: 63 → 65 (+2)
  Endpoints: 687 → 695 (+8)
  Custom UIs: 63 → 65 (+2)
  Updated: CLAUDE.md (4 sections), release.toml (standard tier +2 apps)
  Public docs: README.md (app count), docs/APPS.md (new entries)
```

If counts match current docs → report "no changes needed", skip patching.

#### Safety Rules

- **Never modify manual sections** — only patch lines matching exact count patterns
- **Preserve formatting** — only change numbers, not surrounding text
- Do not commit files that contain secrets

---

### Step 2: Release Safety Check

Run both safety scanners before wrapping up. If violations are found, fix them before proceeding.

```bash
python scripts/check-personal.py
python scripts/check-branding.py
```

**If violations found:**
1. Report each violation (file, line, pattern)
2. Fix them — replace personal data with generic placeholders, replace branding with generic terms
3. Re-run checks until clean

**Report:**
```
Safety Checks:
  Personal data: CLEAN (244 files, 11 patterns)
  Branding: CLEAN (244 files, 8 patterns)
```

Or if fixes were needed:
```
Safety Checks:
  Personal data: 2 violations fixed (docs/DESIGN.md, apps/projects/app.py)
  Branding: CLEAN
```

This ensures no personal information or unwanted branding leaks into the codebase between releases.

---

### Step 3: Vault Ripple — Propagate facts that changed in this session

A Claude Code session produces three durable outputs: **code** (git + devlog), **behavioral learnings** (auto-memory), and **vault-tracked facts that changed** (this step). Steps 1+2 handle code-side state. This step handles vault-side facts so notes don't go stale silently.

#### When to run

Scan the conversation for fact-changing signals. Run ripple when **any** of these landed in the session:

- Career / posture changes — "switching to X", "stopped pursuing Y", "we decided to leave Z"
- Status changes — "the offer came in", "the interview happened", "RFI is now active", "deadline moved to…"
- Strategic decisions — "we're picking A over B", "dropping the C plan", "the experiment finished"
- Income / finance shifts — new salary number, expense pattern change, financial decision
- Project pivots — "Foo project is dead", "Bar is now the priority", "rebuilding Baz"
- Relationship / health updates — anything the user stated about people, body, energy

#### Skip when

- Session was pure code (UI fixes, refactors, dependency bumps, scanner cleanups)
- Session was pure exploration without commitments ("let's investigate X" without a decision)
- Session only changed things tracked **in code**, not in vault notes — e.g. adding a new app's manifest is code, not a vault fact

If none of the signals fired, **skip silently** — report "Vault Ripple: no fact changes this session, skipped." and move on. Do NOT invent fact changes to justify running the skill.

#### How to run

Invoke the `vault-info-ripple` skill with a one-line summary of each fact change. The skill scans the vault for stale references to the **old** state, surfaces what's stale, proposes edits, applies approved ones, and logs the decision in today's journal entry.

```
Skill: vault-info-ripple
Args: <one-line-per-fact summary of what changed>
```

Example: if today's session included "stopped internal salary push at current employer, now actively pursuing external roles", the ripple skill might surface every vault note that says "negotiating with current employer" and propose updates.

#### Report

```
Vault Ripple:
  Facts surfaced: 2
    - Internal salary push → halted (2026-05-13)
    - Job focus → external roles in energy
  Vault notes updated: 5 (audited 14 stale references)
  Journal entry: 50_Journal/2026/2026-05-13.md (decision logged)
```

Or if skipped:

```
Vault Ripple: no fact changes this session, skipped
```

#### Safety

- **Always surface before applying** — `vault-info-ripple` proposes edits; user approves each batch. Wrapup should not auto-apply.
- **One-line summaries, not transcripts** — feed the ripple skill distilled facts, not raw conversation. The skill is fact-shaped, not transcript-shaped (that's `vault-ai-conversation-digest`'s job, which is wrong tool here).
- **Don't double-write to the journal** — ripple logs to today's journal entry. Step 4 (devlog) writes to the project log under `10_Projects/emptyos/log/`. Different files, no conflict.

---

### Step 4: Dev Log — Record what happened this session

Write a structured session summary to the project log in the vault.

#### 2a. Gather Session Data

```bash
# Get today's commits
git log --oneline --since="midnight" --no-merges

# Get files changed today
git diff --stat HEAD~$(git log --oneline --since="midnight" --no-merges | wc -l) HEAD 2>/dev/null || git diff --stat HEAD~1 HEAD

# Get lines added/removed
git diff --shortstat HEAD~$(git log --oneline --since="midnight" --no-merges | wc -l) HEAD 2>/dev/null
```

If no commits today, check unstaged changes:
```bash
git diff --stat
git status --short
```

#### 2b. Check Existing Log

Check if today's log already exists:
```
{vault}/10_Projects/emptyos/log/YYYY-MM-DD.md
```

If it exists, **append** a new session section rather than overwriting.

#### 2c. Write Log Entry

Write to `{vault}/10_Projects/emptyos/log/YYYY-MM-DD.md`:

```markdown
---
date: YYYY-MM-DD
type: dev-session
tags: [emptyos, dev-log, <affected-apps>]
skills_used: [eos-session-wrapup, ...]   # every /<skill> invoked this session
---

# YYYY-MM-DD — <Session Title>

## What Changed
- <bullet points summarizing what was built/fixed/improved>

## Files Modified
- <file paths with brief descriptions of changes>

## Result
<1-2 sentences on outcome and verification status>
```

**`skills_used` sourcing:** list every `.claude/skills/*` (or `/<skill>`) invocation
from this session, in order, deduplicated. Include `eos-session-wrapup` itself.
Skip CLI command-palette hits that aren't skills. This field exists to
correlate skill use with outcome later — accumulate now, analyze when
there's 4+ weeks of data and a specific bet to test (e.g. "does
`eos-design-review` cut UI consolidation passes in half?"). No analyzer
ships today; the data is just grep-able in vault frontmatter.

#### Format Rules

- **Session title**: 3-5 words, action-oriented ("Cable App Growth", "Search Performance Fix")
- **What Changed**: bullets, each starting with the component name
- **Files Modified**: only list files with meaningful changes, not auto-generated
- **Result**: mention if tested, any console errors, visual verification
- **Tags**: include app IDs that were modified
- **Keep it brief**: 10-25 lines total. A record, not a tutorial.

#### Multi-Session Days

If the log file already exists (second session same day), append with a separator:

```markdown
---

## Session 2: <Title>

### What Changed
...
```

---

### Step 5: Site Sync — Regenerate EmptyOS live site

If the vault is connected and the session changed app/plugin inventory, capabilities, or system architecture:

```bash
# Dry-run first to preview changes
python scripts/generate_emptyos_site.py --dry-run

# Apply if changes detected
python scripts/generate_emptyos_site.py
```

This script:
- Scans `apps/` + `apps/personal/` manifests → regenerates `apps.md` (full app catalog)
- Scans `plugins/` manifests → regenerates `plugins.md`
- Generates `capabilities.md` (7 capabilities + provider chains)
- Injects live counts into `index.md` (between `<!-- stats:start/end -->` markers)
- Updates app count in `architecture.md` ASCII art

**After running**, if the EmptyOS daemon is running, trigger a site rebuild:
```bash
curl -s -X POST http://localhost:9000/publish/api/build
```

If daemon is not running, note in the report: "Site source updated — rebuild when daemon starts."

**Do not auto-push.** The script updates local site source. Publishing to `eos.binbian.net` is a deliberate user action via the Publish app UI or `/publish/api/deploy`.

**Note on session content:** `generate_emptyos_site.py` only regenerates inventory pages (apps/plugins/capabilities/stats). It does **not** turn the session devlog into a public post. If the session is worth surfacing publicly, suggest `/eos-devlog-publish` as a follow-up step — it reads the log written in Step 4, writes a `type: post` note to the EmptyOS site source, checks discrepancies vs already-published sessions, and triggers a rebuild (never auto-deploys).

#### Report
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

### Step 6: Next Session Brief — Write a primer for the next conversation

While the session's context is still fresh, write a concise primer the **next** Claude can read to skip the warmup. Briefs are stored **per work track** so parallel tracks (engines, career, publish, infra) can't clobber each other when their wrapups land back-to-back.

```
{vault}/10_Projects/emptyos/log/_next/
  _index.md             ← list of active tracks + last_touched dates
  <track>.md            ← the on-deck card for that track
  <other-track>.md
  ...
```

The skill writes/updates **one track's brief** per wrapup, plus the index. Other tracks are never touched. Long-form history lives in dated `YYYY-MM-DD.md` files; per-track briefs are just on-deck cards.

#### Pick the track

Identify which track this session advanced — by tags on the dated log, by the apps/files touched, or by asking the user if ambiguous. Common tracks at time of writing:

- `em-engines` — `engines/` work + cable/lightning/interference/earthing apps
- `career` — jobs app, outreach, applications, interview prep
- `publish-site` — `apps/publish/`, `eos.binbian.net` content
- `core-infra` — kernel, SDK, capabilities, runtime, web framework
- `apps-other` — UI work on apps that don't fit the above

If the session straddles two tracks, write to **one** brief (whichever was the primary focus) and mention the secondary in that brief's "Open threads".

If a brand-new track is needed, create the file with `track: <slug>` in frontmatter and add a row to `_index.md`. Slugs are kebab-case.

#### What goes in the brief

Lead with concrete state, end with one specific next move. Pad nothing.

```markdown
---
type: next-session-brief
track: <track-slug>
written: <YYYY-MM-DD HH:MM>
last_session: <YYYY-MM-DD>
last_session_title: <Session Title>
threads_cleared: <N>     # open threads from previous brief resolved this session
threads_added: <N>       # new open threads introduced this session
threads_carried: <N>     # open threads still open at end of this session
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

#### Sourcing the working-tree snapshot

Run `git status --short` and `git log --oneline -1` at wrapup time. Paste verbatim (no editing). The next resume's verification pass diffs this snapshot against current state to surface "the working tree changed since this brief was written" — usually because another track ran wrapup between this brief and the next resume.

After writing the track brief, update `_index.md`:

- Refresh that track's row with new `last_touched` date and `last_session` title.
- Add a row if it's a brand-new track.
- Don't touch other tracks' rows.

#### Sourcing rules

- **Where things stand**: distill from the just-written devlog's "Result" section + any `Flagged (needs your call)` items from a prior `/eos-simplify` pass.
- **Open threads**: re-read the session devlog you just wrote — anything phrased as "follow-up", "want me to", "out of scope", or "deferred" is an open thread. Don't invent threads that aren't real.
- **TODO markers**: `git diff HEAD` for `TODO|FIXME|XXX|TODO\(extract\)` lines that **didn't exist** before this session. Use `git diff` not `git log` so unstaged work is included.
- **Recommended starting move**: prefer the most concrete open thread. If none stands out, leave the section as `(no specific recommendation — start by reading {dated log path})`. Don't manufacture a move just to fill the slot.
- **Thread counts** (`threads_cleared` / `threads_added` / `threads_carried`):
  - `threads_cleared`: read the previous brief for this track (`_next/<track>.md` *before* overwriting); count how many of its "Open threads" bullets are resolved by this session's work. If there's no previous brief, this is `0`.
  - `threads_added`: count "Open threads" bullets in *this* brief that weren't in the previous one.
  - `threads_carried`: total bullets in this brief's "Open threads" section (resolved threads should already be omitted from this section per the existing rules).
  - These don't have to be exact — they're trend signals over many sessions, not metrics. Skip-or-zero when ambiguous. Same compounding-data purpose as `skills_used` in the dated log: no analyzer today, but the fields make future analysis a grep instead of a re-read.

#### Skip conditions

If the session was trivial (typo fix, single-line change, doc-only) write only the frontmatter + a one-line "Where things stand". Don't bulk it up.

If a previous brief exists for **the same track** and the session **didn't actually advance** any of its open threads (e.g. user pivoted to unrelated work *within the same track*), preserve the old recommended starting move alongside the new one — mark it `## Carried over from <date>`. Don't silently drop it.

This carry-over rule applies **only within a track**. If the session worked on a different track entirely, that other track's brief stays untouched at its existing path; nothing to carry over because nothing collided.

#### Report

```
Next Session Brief: {vault}/10_Projects/emptyos/log/_next/<track>.md (track: <slug>)
  Open threads: 3 | TODO markers: 1 | Carried over: 0
  Other tracks (untouched): em-engines (2026-05-02), publish-site (2026-04-28)
```

If skipped (trivial session): `Next Session Brief: skipped — trivial session`.

---

### Step 7: Report Summary

After all steps, output a brief summary:

```
Session Wrapup Complete:
  Docs:   CLAUDE.md updated (apps 63→65, endpoints 687→695)
  Safety: CLEAN (personal + branding)
  Ripple: 2 facts surfaced, 5 vault notes updated  (or: skipped — no fact changes)
  Log:    10_Projects/emptyos/log/2026-04-12.md written
  Site:   regenerated (74 apps, 9 plugins) — rebuild triggered
  Next:   10_Projects/emptyos/log/_next/<track>.md written (3 open threads)
          Other tracks untouched: <track1> (last touched <date>), <track2> (last touched <date>)
```

Suggest as a follow-up at next session start: `/eos-session-resume` (or `/eos-session-resume <track>` to jump straight to a specific track).

## Vault Connection

This skill requires vault connection for the devlog step. Check `.claude/vault-connection.json` first.
The project log directory is: `{vault}/10_Projects/emptyos/log/`

## Relationship to Other Systems

- **Reactor journal ripple**: auto-logs individual commits as one-liners to daily journal (breadcrumbs, private)
- **This skill**: structured session summaries to project log + docs sync (end of session, private)
- **`/eos-devlog-publish`**: promotes session log sections into public posts on `eos.binbian.net` (discretionary, opt-in)
- **Git history**: raw commit messages (terse, code-focused)

Four layers: breadcrumbs (reactor) → private summary (this skill) → public post (eos-devlog-publish) → raw history (git). Each is deliberately separate so private dev notes never leak to the public site without an explicit promote step.
