---
name: eos-life-insights
description: Long-horizon personal-life reflection over the vault. Reads the last 30 days (or 90d / 365d on request) of daily journals, milestones, mood check-ins, active and stalled projects, people notes, and untriaged inbox captures, then writes a standalone AI-authored reflection report. Surfaces recurring themes, a wellbeing-wheel balance read (names the THIN dimensions, never fattens the dominant ones), mood and energy trend, movement against stated life goals, and concrete stalled or open-loop items — ending with 2-3 optional suggested actions that are NEVER auto-pushed. Use when the user says "life insights", "reflect on my life", "what have I been up to", "how am I doing lately", "patterns in my journal", or "monthly reflection". NOT the weekly-note ritual that pushes next-week tasks (use eos-week-review — this edits no note and pushes nothing), and NOT development reflection (use eos-insights). Mirrors what the vault shows; does not preach.
---

# EmptyOS Life Insights

A "knowledgeable friend reading your journal" report. Reflect on a window of the user's life (default 30 days), surface the patterns the user can't see day-to-day, and write a durable report — *without* prescribing. This is the personal-vault analogue of Claude Code's `/insights`: self-analysis over an activity corpus → reflective report + optional next steps.

## When to use

- User says "life insights", "reflect on my life", "how am I doing lately", "monthly reflection", "patterns in my journal", "what have I been up to"
- Start/end of a month, or any time the user wants a step-back view that's wider than one week

Do **NOT** use this for:
- A single week's review-and-plan loop → that's `/eos-week-review` (fills the weekly note + pushes tasks).
- EmptyOS dev work → that's `/eos-insights`.
- A one-off mood note → write it straight into today's daily journal.

The line vs `eos-week-review`: week-review *acts on one week* (template + tasks). life-insights *interprets a trend across weeks* (standalone report, no task push unless the user asks).

## Pre-flight

The pure file-read path needs no daemon. A few **optional** enrichments hit the daemon — only then:

- **Daemon up** (optional) — `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:9000/` → `200`. If down, skip the API enrichments and read files directly; never restart the daemon yourself (`.claude/rules/daemon-handling.md`).
- **Auth (private mode)** — read `auth_token` from `emptyos.toml` `[network]`, send `Authorization: Bearer <token>` (`.claude/rules/environment.md`).
- **`127.0.0.1`, not `localhost`** for any Python/urllib probe (IPv6 `::1` refusal).
- **Non-ASCII** task text (if the user later opts to push actions) → Python `urllib`, never `curl -d` (cp1252 mangling).

## Vocabulary

- **Vault root** — `[notes] path` in `emptyos.toml` (forward slashes).
- **Window** — default last 30 days from today; `90d` / `365d` if the user asks. Resolve dates with `datetime.date`.
- **Report path** — `{vault}/30_Resources/EmptyOS/insights/outputs/YYYY-MM-DD-life.md` (today's date). The `outputs/` folder means the note is implicitly AI-authored (`.claude/rules/authorship-boundary.md`); still set `author: ai` explicitly.
- **Noise** — bot breadcrumbs + test fixtures that must be filtered from journal reads: `PLAYWRIGHT-TEST-*`, `Growth Agent`, `🎙️` (podcast), `🏗️` (site build), `✓ Applied`, `🪞 System reflected`.

## Process

### Step 0 — Read the prior scorecard (close the loop)

```bash
python scripts/insights_ledger.py scorecard life
```

What did last run suggest? A theme/suggestion **recurring** without movement is a stronger signal than a fresh one. Carry forward, note what's resolved, drop the stale. `(no prior ledger …)` on the first run is fine.

### Step 1 — Gather signal (read-only)

Apply the **deep-research method** (`.claude/rules/deep-research.md`) to the vault corpus: read primary sources, **triangulate** (no headline rests on one source), refute, grade. Direct file access is strongest in conversation mode.

1. **Daily journals** — Glob `{vault}/50_Journal/{year}/YYYY-MM-DD.md` for each day in the window (windows may span two year folders). Read each; filter noise. Keep: `🎯` milestones, substantive mood entries, real `#tags` (not automated app emits), prose that carries a theme.
2. **Mood / streak numbers (optional)** — `GET http://127.0.0.1:9000/journal/...` (`journal.get_summary`) for hard counts instead of re-deriving by hand.
3. **Projects** — list `{vault}/10_Projects/*/` directories; use mtime to split **active** (touched in window) vs **stalled** (silent through the whole window). Name the stalled ones.
4. **People** — read `person`-tagged notes (people app: `last_contact` frontmatter, `_days_since`, `_health_score`). Optional: `GET /people/api/...`. Flag **stale relationships** (large days-since on a person the user cares about).
5. **Open loops** — untriaged `{vault}/00_Inbox/` captures still sitting; long-`waiting` tasks (`/task` or vault scan).
6. **Wellbeing-wheel signal** — run the pure (kernel-free, safe to shell) helper over the journal prose:
   ```bash
   python -c "from emptyos.sdk.dimensions import scan_text, balance_score, empty_counts, DIMENSIONS; print(DIMENSIONS)"
   ```
   Accumulate `scan_text(day_prose)` counts across the window into one `{dimension: count}` dict, then `balance_score(counts)` → `{thin, dominant, grade}`. This is *passive inference* (alias vocabulary) — treat it as a reading, not a verdict.

**Adversarial cross-check (before writing).** The wheel helper and mtime heuristics produce false positives. Refute each top read against the actual journals: is a "thin" dimension genuinely neglected, or just untagged (e.g. lots of walking written without the word "walk")? Is a "stalled" project actually paused-on-purpose? Drop or soften anything that doesn't survive a re-read. A read you can't verify is graded `inferred`, not stated as fact.

### Step 2 — Synthesize the report

Interpret, don't chronicle — but back it with a **quantitative spine**, not prose alone (the Claude `/insights` standard):

- A `## Stats` section right after Headline — a 2-col `Metric | Value` table (journal days with content, milestones, applications, interviews, social moments, mood check-ins). Becomes the stat-tile row.
- **≥3 chartable distributions** — each a 2-col `Label | Value` table (the renderer auto-draws bars): **real activity by wheel dimension** (recompute with the `playwright-test-*`/app-emit noise filtered out — the honest read), mood/check-ins per week, and a job-application funnel (applied → interview → declined) or entries-per-week. Slice the month several ways.
- Optional copyable artifact: if you suggest a concrete task/note, put its exact text in a fenced ``` block.

Sections (markdown `##` — keep these names so the renderer themes them right):

1. **Headline** — one short paragraph, the honest shape of the window (becomes the hero box).
2. **Trend (vs prior window)** — this window vs the prior one, and a read of the *previous* life report's deltas if one exists (find the prior `…-life.md` in the outputs dir). Mood arc, attention shifts — trend, not a still photo.
3. **Recurring themes** — what the user kept returning to (preoccupations / open questions), 3-6 bullets.
4. **Wellbeing balance** — the wheel read. **Surface the THIN dimensions; never push to fatten the dominant ones** (CLAUDE.md Rule 16 — this report is the one sanctioned place the wheel shapes output). State which dimensions got little signal and what (in the journal's own words) the quiet looked like. No score widget, no picker — a reflective sentence per thin dimension.
5. **Movement vs stated goals** — gently cross-check against the user's durable direction (career = energy×software; the "stay in power until a US green card" gate; possible US relocation). **Mirror, don't preach** (`user_buddhist_disposition`): "the vault shows X; the stated aim was Y" — describe the gap, don't prescribe the fix. The user owns judgment.
6. **Open items** — named stalled projects, stale relationships, untriaged loops. Concrete, not generic.
7. **Suggested next steps** — 2-3 specific, optional actions. **Do NOT auto-push.** End the section with: "Say the word and I'll add any of these to the projects app."

**Evidence grading** — tag findings by how they were derived: `[read-verified]` (confirmed against the journals), `[inferred]` (a judgment that survived the adversarial cross-check but isn't directly evidenced). Keep the honesty signal visible.

### Step 3 — Write the report

Create the note (block-style YAML tags per `.claude/rules/dev-gotchas.md`):

```markdown
---
author: ai
tags:
  - insights
lens: life
period: 30d
as_of: YYYY-MM-DD
lifecycle: snapshot
---
```

Write to `{vault}/30_Resources/EmptyOS/insights/outputs/YYYY-MM-DD-life.md`. Optionally add a one-line `[[YYYY-MM-DD-life]]` pointer to the current weekly note (`{vault}/50_Journal/{year}/{year}-W{NN}.md`) for discoverability — append only, never edit the user's prose (append discipline, `.claude/rules/authorship-boundary.md`).

Section-heading discipline (the renderer themes cards by keyword in the `##` title): a leading `## Headline` section becomes the amber hero box; *thin/stalled/stale/open loop* lean amber, *suggested/next step* → blue, *theme/balance/mood* → neutral/green. Keep the section names from Step 2 so the themes land right.

### Step 3b — Render the HTML view

```bash
python scripts/render_insights_html.py "{vault}/30_Resources/EmptyOS/insights/outputs/YYYY-MM-DD-life.md"
```

Writes a styled `…-life.md`→`…-life.html` sibling (hero + TOC + themed cards). The markdown stays the source of truth; the HTML is the readable view. Pure stdlib, safe to run anytime; `--open` pops it in the browser. Give the user the `file:///…/YYYY-MM-DD-life.html` URL.

### Step 4 — Optional action push (only on explicit yes)

If — and only if — the user confirms, push the chosen suggestions to the `projects` app, reusing the `eos-week-review` POST shape (`POST /projects/api/projects/{id}/tasks/add`, Python urllib, `127.0.0.1`, Bearer token, `due` date). Tag each `#life-insights`. Otherwise leave the report as the artifact.

**Record the suggestions** (regardless of push) so next run's Step-0 scorecard can grade them:

```bash
python scripts/insights_ledger.py record life --from "{vault}/30_Resources/EmptyOS/insights/outputs/YYYY-MM-DD-life.md"
```

### Step 5 — Report to chat

1. 3-4 highlight bullets (themes + the standout thin dimension + one stalled/stale item).
2. The report paths — both the `.md` and the `file:///…/YYYY-MM-DD-life.html` view.
3. The one EmptyOS gap this run bumped into (e.g. people-note `last_contact` rarely filled, no place to mark a project "intentionally paused") — feed it forward so the surfaces keep improving (the self-improvement habit from `eos-week-review` step 5).

## Cross-references

- `.claude/skills/eos-week-review/SKILL.md` — the weekly sibling; do not conflate (one week + tasks vs cross-week trend report).
- `.claude/skills/eos-insights/SKILL.md` — the system-side sibling (EmptyOS dev, not life).
- `scripts/insights_ledger.py` — the prediction ledger (Step 0 scorecard + Step 4 record).
- `emptyos/sdk/dimensions.py` — `scan_text` + `balance_score` (pure; safe to shell, no kernel boot).
- **Not the `explore` app** — it is web-only (`ask_web`), the wrong corpus for vault reflection; analysis here is internal-only.
- `apps/public/standard/journal/app.py::get_summary` — optional streak/mood counts.
- `apps/public/standard/people/app.py` — `person` notes, `last_contact`, `_health_score`, `_days_since`.
- CLAUDE.md Rule 16 — wellbeing wheel as a reasoning lens (surface thin, never display a widget).
- `.claude/rules/authorship-boundary.md` — `author: ai` + `outputs/` + append discipline.
- `.claude/rules/time-dimension.md` — past → present read; this report is a past-window synthesis.
- `.claude/rules/proposed-action.md` — propose, don't auto-apply (the no-auto-push rule above).
- `user_buddhist_disposition` (memory) — mirror, don't preach.

## When NOT to use

- The user wants this week's plan → `/eos-week-review`.
- The user is mid-conversation about EmptyOS code → `/eos-insights` or `/eos-session-wrapup`.
- The window has almost no journal signal (e.g. a fresh vault) → say so and skip rather than inventing patterns from three entries (the `/insights` "variable between runs" failure mode is worst on thin data).
