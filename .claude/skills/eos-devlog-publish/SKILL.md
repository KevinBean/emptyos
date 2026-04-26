---
name: eos-devlog-publish
description: Turn EmptyOS session devlogs into DRAFT posts on the EmptyOS site (eos.binbian.net). Reads session sections from `{vault}/10_Projects/emptyos/log/YYYY-MM-DD.md`, writes them as `type: post` notes with `publish: false` by default under the EmptyOS site source. Posts show up in the Publish app's Drafts tab for review — user flips `publish: true` when ready. Pass `--publish` to skip the draft step. Checks discrepancies vs what's already published, triggers local rebuild, never auto-deploys. Use when the user says "publish devlog", "draft session", "save session as draft", "blog this session", or wants to surface session work publicly after `/eos-session-wrapup`.
---

# EmptyOS Devlog Publish

Session logs live in the vault at `10_Projects/emptyos/log/YYYY-MM-DD.md`. The live EmptyOS site (`eos.binbian.net`) builds from `30_Resources/EmptyOS-Site/`. This skill bridges those two: session sections → publishable posts → site rebuild.

**Separation of concerns:**
- `/eos-session-wrapup` — records the session privately (mandatory hygiene)
- `/eos-devlog-publish` — promotes it to a public post (discretionary, runs after wrapup or retroactively)

**Never deploys.** Writes post, rebuilds, stops. Deploy is always a separate user action via Publish UI or `POST /publish/api/deploy` (cloud consent principle).

## When to Use

- User says "publish devlog", "publish session", "update the site", "blog this"
- After `/eos-session-wrapup` when the session is worth surfacing publicly
- Retroactively: user wants to publish an old session log

## Prerequisites

- Vault connected (`.claude/vault-connection.json` → connected: true)
- EmptyOS daemon running on `localhost:9000` (for the rebuild trigger)
- Session log exists at `{vault}/10_Projects/emptyos/log/YYYY-MM-DD.md`
- EmptyOS site configured in Publish app (id: `emptyos`, source: `30_Resources/EmptyOS-Site`)

Read vault path from `.claude/vault-connection.json`. Read site source folder from the publish sites.json or via `curl http://localhost:9000/publish/api/sites`.

## Arguments

| Arg | Default | Meaning |
|---|---|---|
| `--date YYYY-MM-DD` | today | Which log file to read |
| `--session N` | all | Publish only the Nth session section (1-indexed). Default: all sessions in the log |
| `--since N` | unset | Publish all unpublished sessions from the last N days |
| `--site ID` | `emptyos` | Target site profile |
| `--publish` | false | Set `publish: true` in frontmatter (makes the post go live on next build). **Default is draft** — posts are written with `publish: false` so they show up in the Publish app's Drafts list for review before going live. |
| `--force` | false | Overwrite existing post files |
| `--dry-run` | false | Show what would be written, don't write |

**Default workflow: drafts.** A session log is the developer's private record — it often needs editing (tone, technical detail, internal file paths) before going public. Writing drafts by default means the skill never silently puts raw content on the live site. The user reviews drafts in the Publish app's Drafts tab, edits in their editor of choice, then toggles `publish: true` when ready.

To publish immediately, pass `--publish` explicitly — skill will warn before writing.

## Process

### Step 1: Discrepancy Check

Before writing anything, report the gap between what's in the log vs what's already published.

```bash
# List log files
ls {vault}/10_Projects/emptyos/log/*.md

# List already-published devlog posts
ls {vault}/{site_source}/devlog/*.md

# Report:
#   Log files: 2026-04-12, 2026-04-15, 2026-04-18
#   Published:  2026-04-12
#   Pending:    2026-04-15, 2026-04-18
```

For each log file, count `## Session:` sections and compare against published posts that cite the same date. Report which are already live, which are drafts (no `publish: true`), which are missing entirely.

### Step 2: Parse Session Sections

Session log format (multi-session per day):

```markdown
---
date: 2026-04-18
type: dev-log
tags: [emptyos, ...]
---

# 2026-04-18 — …

## Session: First Session Title

### What Changed
- ...

### Files Modified
- ...

### Result
...

---

## Session: Second Session Title

...
```

Parser rules:
- Top-level frontmatter — carry `date` and `tags` through to posts
- Each `## Session: <title>` starts a new post unit
- `---` between sessions is the separator (ignore)
- Post body = everything from the first subsection (`###`) through to the next `## Session:` or EOF
- Title = the `## Session:` line minus the prefix

### Step 3: Build Post Frontmatter + Body

**The post is a reflection, not a report.** The session log records what happened (file paths, LOC deltas, bullets). The post tells the reader *why the change mattered*: what problem was showing up, what was built to address it, what it now makes possible, what's still open. Reading the post should leave someone who wasn't in the session with an understanding of the system's direction — not just a list of commits.

For each session to publish, write to `{vault}/{site_source}/devlog/YYYY-MM-DD-<slug>.md`:

```markdown
---
title: "Short evocative title — not the raw session heading"
date: 2026-04-18
type: post
publish: false  # default — flip to true when ready to surface publicly
tags: [devlog, emptyos]
summary: "One sentence naming the insight or problem the session addressed"
---

<!-- generated by eos-devlog-publish from 10_Projects/emptyos/log/2026-04-18.md -->

<opening paragraph — the situation that called for the change. What was showing up? What felt wrong or missing? Lead with the pressure, not the solution.>

<middle paragraphs — what we built / changed, framed as *responses* to that pressure. Connect each move to the problem it addressed. Don't enumerate file paths unless a specific one carries meaning.>

<closing paragraph — what this unlocks, or what's still open. "Now we can X" or "This sets up Y but Z is still rough." Short.>
```

### Voice guidelines

- **First-person plural** ("we noticed", "we pulled", "we left X for later") — this is a shared build log, not a press release.
- **Concrete but not exhaustive.** Name the specific thing that forced the change (e.g. "the integrity audit kept scoring publish at 3/10 for atomicity") but don't list every edited file. A post is 4–8 paragraphs, not 40 bullets.
- **Connect the dots.** Each session sits in a longer thread — mention the prior decision or the downstream implication when natural. Posts that stand in isolation read like changelogs.
- **Plain English.** Avoid "leverage", "unlock", "journey", "transform" (corporate blog tells). Avoid "just" as a filler word. Prefer concrete verbs: extracted, split, routed, guarded.
- **Show the thinking, not the keystrokes.** A reader should come away with "oh, so that's why they did X" — not "they changed 745 lines."
- **Name the tradeoff or open thread.** Every real session has friction — a decision you're not sure about, a deferred cleanup, a principle you softened. Mention it briefly; a post without any doubt reads like marketing copy.

### Rules

- Slug = `slugify(title)` — lowercase, ascii, hyphens, max 60 chars, strip trailing hyphens after truncation
- Filename prefix = date so posts sort chronologically
- Title: do **not** reuse the raw `## Session:` heading (those are dev shorthand). Write a short title that names the *meaning* of the session — what shifted, what's new, what got solved.
- Summary: one sentence naming the problem or the insight. This is the Open Graph / preview line; spend a moment to get it right.
- Tags: always include `devlog` and `emptyos`. Optionally include 1–2 topic tags (e.g. `architecture`, `publishing`, `testing`) when the session is clearly about one area.
- Strip `<system-reminder>` tags defensively.
- **Do not copy the raw session body verbatim.** If the session log's `### What Changed` is a tidy 6-bullet list, the post still has to rewrite that as prose — the two formats serve different readers.

### Step 4: Write Posts

- If the target file exists and `--force` is not set → skip with a warning
- Otherwise write the file using `await self.write()` via the Publish app's `POST /publish/api/save-draft`, OR direct file write if skill runs outside the daemon context

```bash
# Simplest: direct write (skill has vault filesystem access)
mkdir -p "{vault}/{site_source}/devlog"
# Write each post
```

### Step 5: Trigger Site Rebuild

```bash
curl -s -X POST "http://localhost:9000/publish/api/build" \
  -H "Content-Type: application/json" \
  -d '{"site": "emptyos"}'
```

Report the build stats (pages/posts/tags counts).

If the daemon isn't running, note in the output: "Posts written; run `curl -X POST http://localhost:9000/publish/api/build` after `eos start` to rebuild."

### Step 6: Report

```
Devlog Publish:
  Source log:  10_Projects/emptyos/log/2026-04-18.md
  Sessions:    3 found, 2 new, 1 already published
  Posts written:
    30_Resources/EmptyOS-Site/devlog/2026-04-18-system-check-and-fix.md
    30_Resources/EmptyOS-Site/devlog/2026-04-18-surface-self-capabilities.md
  Skipped (exists, no --force):
    30_Resources/EmptyOS-Site/devlog/2026-04-18-session-first.md
  Build:       124 pages, 15 posts, 3 tags   ← from /api/build response
  Preview:     http://localhost:9000/publish/ (site: emptyos)
  Deploy:      ready — run `curl -X POST /publish/api/deploy/firebase` to push
```

## Safety

### Public-safety filters (mandatory before writing any post)

The source log is a developer's private record. Most of what it contains — specific dollar amounts, account names, personal domains, employer references, contract details, body-metrics, dated life events, retirement targets — must never appear on the public site. Before writing any post:

1. **Only core/community work is eligible.** Filter sessions by what they touched:
   - **Eligible (publish):** changes under `emptyos/` (kernel, SDK, web, runtime, cli, capabilities), `apps/<non-personal>/` (capture, note, task, search, link, settings, system-log, run, git, reactor, app-gen, plugin-gen, release, tests, assistant, publish, music-studio, web-analytics, app-analytics, model-bench, billing, ai-queue, tmpl), `plugins/`, `scripts/`, `docs/`, `.claude/skills/`, `restart.*`, `release.toml`, `CLAUDE.md`, architecture concepts.
   - **Not eligible (skip or strip):** anything under `apps/personal/` — finance, net-worth, retirement, cable, healing, jobs, job-scout, reader, media, contacts, places, items, habits, workout, sleep, reminders, bookmarks, weather, recipes, nutrition, english, speaking, voice-review, shadowing, hub, staff, briefing, digest, reflect, integrity, sheath-voltage, phone_agent, hdd-estimator, music-studio-before-it-moved.
   - Even when a personal app is mentioned as *context* for a core pattern (e.g. "VaultLibrary was extracted after five apps hand-rolled the same pattern"), name the pattern, don't enumerate the personal apps.

2. **Banned content — never appears in posts, ever.** If a session log contains any of these, the public version must strip them:
   - Dollar amounts, percentages of personal wealth, net-worth figures, account balances
   - Financial platform names used personally (e.g. broker names, bank names tied to personal accounts)
   - Personal domains, emails, GitHub handles, social handles
   - Personal addresses, coordinates, cities named in a personal context (e.g. "move to X", "target retirement in Y")
   - Employer names, ex-employer names, contract references, non-compete clauses, employment dates
   - Personal health/body metrics (weight, sleep hours, workout details, specific medications, specific therapies)
   - Family member names, relationship specifics
   - Any date tied to a personal life event (visa dates, marriage, move, etc.)

3. **Scan every draft against `.eos-personal` patterns** before writing. Additionally apply these skill-local banned patterns (regex, case-insensitive):
   ```
   \$\s?\d{2,}                    # dollar amounts
   \b(?:IBKR|Commbank|[A-Z]\w+\s+Bank)\b  # broker/bank names
   binbian\.net|KevinBean         # personal domain & github handle
   Endeavour\s+Energy             # former employer
   non.?compete                    # contract clauses
   net.?worth|\bNW\b              # wealth figures
   retirement\s+(target|plan)     # personal retirement
   Sydney|Perth|Adelaide          # specific AU cities (if personal context)
   gpt-image-1|dall-e             # proprietary model names tied to cost/licensing
   Obsidian(?!-CLI)|Suno|Kindle|Spotify   # third-party branding (plugins exempt)
   ```
   If a draft hits any of these, rewrite the passage; don't just redact it. If the theme can't be written without hitting them, skip the session entirely.

4. **Theme-based, not day-based.** Don't mechanically write one post per log file. Group by theme — sometimes a day has no public content (skip it), sometimes a theme spans three days (one post, cross-referencing). The goal is the public arc of the system, not a dev diary.

### Operational safety

5. **Never auto-deploy.** The skill stops after a local build. Deploy is an explicit second step the user runs.
6. **Don't overwrite** existing posts without `--force`. A post already promoted should be edited directly; the log may have been since edited.
7. **Respect the site target.** Default `--site emptyos`. Never write to other profiles unless the user explicitly passes `--site`.
8. **Dry-run first** when publishing more than one session or using `--since` — show the plan and the filtered session list (core vs personal), let the user approve before writing.
9. `scripts/check-personal.py` and `check-branding.py` scan tracked files only — they won't catch leaks in vault drafts. The pattern scan in §3 above is the authoritative check for draft content.

## Relationship to Other Skills

- `/eos-session-wrapup` writes the devlog; this skill **reads** it. Keep them separate — wrapup is always-on hygiene, publish is optional promotion.
- `scripts/generate_emptyos_site.py` regenerates the inventory pages (`apps.md`, `plugins.md`, `capabilities.md`). This skill writes posts only; it doesn't touch inventory pages.
- Reactor's journal ripple adds breadcrumbs to `50_Journal/`. That's private journal flavour, not public.

Three layers:
- Reactor → breadcrumbs (private journal)
- Wrapup → session summary (private project log)
- **This skill → curated post (public site)**

## Known Gaps

- Reflective rewriting is Claude's job inside the skill call — there is no deterministic parser that can do this. That means quality varies with how well the session log captured motivation, not just actions. Logs that only list files changed produce thinner posts.
- No automatic linking between consecutive sessions. Each post stands alone, though the voice guidelines encourage mentioning the prior decision when natural.
- Build endpoint currently only targets the active site, so the skill flips active→build→flip-back. Fix when `/publish/api/build` learns an optional `site_id` body param.
- Per-session tag inference: posts currently inherit the log file's frontmatter tags. A smarter version would infer 1–2 topic tags from the session content.
