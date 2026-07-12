---
name: eos-week-review
description: Personal-life weekly review and next-week plan. Reads the past week's daily journal entries, milestones, mood check-ins, the system-generated wheel review, and the previous weekly note. Drafts the blank "完成 / 未完成 / 感想" review section in the current `{vault}/50_Journal/{year}/{year}-W{NN}.md` file, then drafts a top-3-plus-supporting plan into the next week's note's Focus section, and pushes the actionable items into the projects app as real tasks so they surface on /hub/ and /task/. Use when the user says "review this week and plan next week", "weekly review", "/eos-week-review", "what did I do this week", or "plan next week" in a personal-life context. Distinct from /eos-session-wrapup (which logs an EmptyOS dev session, not a life week). Distinct from /eos-session-resume (which picks up a per-track dev brief). Wheel-imbalance signal from the scheduler-written `## Wheel Review` block is the input, not the output — this skill is the bridge from system-detected insight to recorded action.
---

# EmptyOS Week Review

Reflect on the past week (personal life, not EmptyOS dev work) and plan the next, then capture the plan as durable tasks in the projects app so it survives the conversation.

## When to use

- User says "review this week and plan next week", "weekly review", "what did I do this week"
- Sunday evening / Monday morning loop
- The blank `## Review` and Focus sections in the latest `{vault}/50_Journal/{year}/{year}-W{NN}.md` files are the visible symptom

Do **NOT** use this for EmptyOS dev session work — `/eos-session-wrapup` covers that. The line between them: dev-session = git-tracked codebase changes; week-review = the human's life.

## Pre-flight

This skill writes its plan as tasks through the daemon HTTP API, so the daemon must be reachable first:

- **Daemon up** — `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:9000/projects/` should print `200`. If not, ask the user to run `restart.bat` — never start/restart the daemon yourself (`.claude/rules/daemon-handling.md`).
- **Auth (private mode)** — read `auth_token` from `emptyos.toml` `[network]` and send `Authorization: Bearer <token>` on every request (`.claude/rules/environment.md`).
- **Non-ASCII task text** — POST via Python `urllib`, not `curl -d` (cp1252 mangles em-dash / CJK).

## Vocabulary

- **This week** — the week containing today's date, in ISO Mon-start (`datetime.date.today().isocalendar()`)
- **Last week** — `this_week - 1`
- **Weekly note path** — `{vault}/50_Journal/{year}/{year}-W{NN}.md`. Kevin's notes use Sun-start weeks (file says "May 10-16" but ISO W20 is May 11-17). Use the ISO week for the filename; trust the file's date-range header for the actual coverage.

## Process

### Step 1 — Gather signal

Read these, in order, then synthesise. Don't synthesise from one source.

1. **The week's daily journals**: `{vault}/50_Journal/{year}/YYYY-MM-DD.md` for each day Mon→Sun. Filter out `PLAYWRIGHT-TEST-*` and bot breadcrumbs (`Growth Agent`, `🎙️ Generated a podcast`, `🏗️ Built site`, `✓ Applied rooms.write_note`, `🪞 System reflected`). Keep: `🎯` milestone bullets, mood entries with substantive text, anything with a `#tag` that isn't an automated app emit.
2. **The current weekly note's existing content** (`{vault}/50_Journal/{year}/{year}-W{NN}.md`): theme, focus tasks that were set, ## Review section (probably blank), ## Wheel Review (probably populated by the Sunday-9pm scheduler — `apps/journal/app.py::scheduled_weekly_wheel_review`).
3. **Active life-area files**: glob `{vault}/20_Areas/{Career,Health,Finances,...}/*.md` modified in the past 7 days (`stat` mtime). Don't read all of them — just the ones that moved.
4. **Active life-project files**: same shape for `{vault}/10_Projects/{slug}/` directories. Visa, job-search, healing, immigration tend to be the durable ones.
5. **The previous weekly note's plan** (`{year}-W{NN-1}.md`): which items shipped, which slipped. Compare to actual reality, not just to the checkbox state (Kevin rarely ticks).
6. **The wheel-review narrative** if present in the current weekly note: it's the system's read of which dimensions got starved. Use it; don't re-derive.

### Step 2 — Draft the review

Find the `## Review` section in the **current week's** weekly note (`this_week`). It usually has placeholders like:

```
**English Hours**: ___/7h | **Zumba**: ___/3
**完成:**
**未完成:**
**感想**:
```

Fill in. **Voice rule for the whole section:** this is Kevin's own weekly note — draft it as if Kevin wrote it (first person, never address him as "you"; same discipline as braindump's capture summary). Recognition test: each line should make him go "oh right, that."

- **完成** — bullets with a leading emoji + (date), **one bullet per goal/work-stream, not per event**. Default to merging: fold related wins under the stream they served — two bullets about the same stream should almost never exist. Lead with the biggest unblock, not chronologically. Skip dev-work wins (those go to /eos-session-wrapup).
- **未完成** — bullets describing each gap. Be specific: which outreach didn't happen, which area got zero signal, which item from last week's Focus quietly rolled. Name the wheel-imbalance directly using the wheel review's words.
- **感想** — 2–3 short paragraphs in Kevin's first-person voice. Honest, not performative. Don't summarise — interpret. What was the emotional shape of the week? Was the energy in the right place given Kevin's stated priorities (career pivot, visa wait, balanced life)?
- **Personal-life summary** — one short paragraph at the bottom linking forward to next week's note: `[[YYYY-WNN+1]]`. State the top-3 in one sentence.

### Step 3 — Draft the plan

Find the `## Focus` section in **next week's** weekly note (`this_week + 1`). Often pre-populated with a template. Add three blocks above the existing `本周目标` list:

```markdown
**Top 3 (do these even if everything else slips):**
- [ ] 🎯 ... 📅 YYYY-MM-DD
- [ ] 🎯 ... 📅 YYYY-MM-DD
- [ ] 🎯 ... 📅 YYYY-MM-DD

**<category> — <one-line frame>:**
- [ ] ... 📅 YYYY-MM-DD
- [ ] ... 📅 YYYY-MM-DD

**Life-balance restock (counter the W{NN} wheel-imbalance):**
- [ ] Fill one Three-things per day 📅 YYYY-MM-DD
- [ ] One <thin-dimension> action 📅 YYYY-MM-DD
```

Rules:
- Top-3 must be **named verbs with specific objects**. Not "do career work". "Open Application-Tracker, log Dan/Shanika status, decide on nudge" is correct shape.
- Every supporting bullet must have a 📅 date.
- The "Life-balance restock" block must respond to the actual wheel reading — if Physical was Empty, the action is a physical one. Don't paste a generic template.
- Don't touch the existing template lines (`本周目标`, `重点任务`, `💼 Job Search`, `## Tasks`, etc.) — augment, don't replace. Kevin's weekly template is load-bearing across years.

### Step 4 — Push tasks into the projects app

The weekly note is markdown — easy to skip, easy to forget. Real tasks live in the `projects` app. Pick the right project per bullet:

| Bullet category | Project id |
|---|---|
| Career / outreach / job search | `job-search` |
| Visa / immigration | `visa-189` (or `visa-eb2` / `visa-canada` depending on theme) |
| Health / Zumba / diet / inner work | `54-day-safe-projects` if active, else `health` if it exists, else create a personal project for the quarter |
| Apartment / environment | `apartment-decoration` |
| Sydney networking | `job-search` (it's a career signal, not its own track yet) |

POST shape (Windows notes: cp1252 mangles em-dash via curl `-d`, use Python urllib for any
non-ASCII body — `feedback_curl_windows_utf8_bodies.md`; and use `127.0.0.1`, NOT `localhost`
— Python urllib resolves `localhost`→IPv6 `::1` while the daemon binds IPv4, so `localhost`
gives `WinError 10061 connection refused`):

```python
import urllib.request, json, tomllib
with open('emptyos.toml', 'rb') as f:
    TOKEN = tomllib.load(f)['network']['auth_token']
URL = 'http://127.0.0.1:9000/projects/api/projects/{project_id}/tasks/add'
body = json.dumps({'text': '...', 'due': 'YYYY-MM-DD'}).encode('utf-8')
req = urllib.request.Request(URL, data=body, method='POST',
    headers={'Authorization': f'Bearer {TOKEN}', 'Content-Type': 'application/json'})
urllib.request.urlopen(req, timeout=15)
```

Tag every task `#w{NN}` plus a category tag (`#top3`, `#career`, `#wellbeing`). The hub task panel filters by tag; the weekly note's `## Tasks` block uses an Obsidian Tasks query that pulls by due-date window.

If the daemon hiccups mid-POST (connection refused), retry with `time.sleep(1.5)` between calls — 8-task burst sometimes overruns whatever rate limit is in front of the projects app.

### Step 5 — Report

Tell the user (in chat):
1. Wins + gaps in 2–4 bullets each
2. Top-3 for next week, each as one sentence
3. How many tasks landed in which project
4. **The one EmptyOS gap you noticed** that this skill bumped against — feed it back so the skill (or its dependencies) keep improving. The skill exists because the gap was: "system has the signal, doesn't bridge to action." Notice the next narrowing of that gap.

Do NOT generate a `/eos-session-wrapup`-style devlog from this skill. The weekly note IS the log.

## Cross-references

- `apps/journal/app.py::scheduled_weekly_wheel_review` — Sunday 9pm cron that writes the wheel narrative this skill reads
- `apps/projects/app.py::add_task_to_project` / `POST /projects/api/projects/{id}/tasks/add` — task-write endpoint
- `apps/task/app.py` — read-side aggregator that surfaces tasks tagged `#w{NN}` on the hub
- `.claude/rules/time-dimension.md` — past → future → now is exactly what a weekly review does; this skill is its first concrete consumer
- `.claude/skills/eos-session-wrapup/SKILL.md` — sibling skill for dev-side reviews; do not conflate
- `.claude/skills/eos-session-resume/SKILL.md` — sibling skill for dev-side resume; do not conflate
- `vault-journal-planner` skill (user-level) — generic periodic-notes management; overlaps in surface (weekly notes) but not in goal. `vault-journal-planner` arranges notes; this skill *interprets* a week's signal and *records actions*. Prefer this skill for Sunday-evening / Monday-morning review-and-plan loops; prefer `vault-journal-planner` for "create a new monthly note from template" or "where's my Q3 review folder" shape requests.

## When NOT to use

- The user is mid-conversation about EmptyOS code → use `/eos-session-wrapup` instead
- The user wants a one-off mood reflection → write it directly into the daily journal, no full weekly process
- It's Wednesday → fine to run, but the wheel review the scheduler writes Sunday 9pm won't reflect the current week yet, so the signal will be partial; mention this in step 5
