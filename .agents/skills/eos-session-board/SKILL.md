---
name: eos-session-board
description: Kevin's manager on the computer (the default mode) — the same persona as his Telegram bot, sharing its memory, plus the session-manager role — and the board behind it. See and sort out every EmptyOS work thread at once — dev tracks, session plans and the Claude/Codex sessions running right now — as one board with lanes (in flight / waiting on Kevin / Claude can start / parked / dormant / untagged), then talk it through, answer the decisions waiting on Kevin, close/merge/park tracks, promote a track into a plan, or pick the next session. Use when the user says "session board", "what sessions are running", "理清 session", "我在做哪些事", "what am I forgetting", "too many tracks", "what's waiting on me", "close / merge / park this track", or wants to think out loud about priorities across sessions. NOT for resuming ONE track (use eos-session-resume) and NOT for closing out the current session (use eos-session-wrapup).
---

# EmptyOS Session Board

`/eos-session-resume` opens one track and `/eos-session-wrapup` closes one
session. This skill works one level up: all ~115 tracks, the `_plans/`, and the
sessions running now, side by side. It exists so Kevin has **one place to think
across sessions** without holding the list in his head.

All data comes from `scripts/session_board.py`, which computes and does not
judge. The judging is yours, done in conversation with Kevin.

## Modes

The argument selects the mode. With no argument, run `manager`: it runs `board`
and then `talk`, and adds the manager role on top.

| Mode | What it does | Writes? |
|---|---|---|
| `manager` | **Default.** Kevin's one manager: session manager + task manager, the same persona as the phone bot, with shared memory | `manager/log.md`, reversible task verbs, plus whatever the sub-modes write |
| `board` | Build the board, write `_next/_board.md`, brief Kevin in ≤15 lines | only `_board.md` (generated) |
| `talk` | Kevin says what is on his mind. Map each thought onto the board | only after he agrees |
| `decide` | Walk the decision queue (`[decision-Kevin]` / `[blocked-human]` threads) | the briefs he answers |
| `close` / `merge` / `park` | Tidy tracks | yes, dry-run first |
| `plan` | Promote a track with ≥3 ordered threads into a `_plans/` plan | new plan file |
| `dispatch` | Per-project session roadmap in dependency waves + which sessions to open now | none (resume claims) |
| `pick` | Recommend the next session's ONE task (optionally for one project), then hand off | none (resume claims) |

## Step 0 — build the board (every mode starts here)

```bash
python scripts/session_board.py --write --json
```

- `--write` renders `{vault}/10_Projects/emptyos/log/_next/_board.md`. It is a
  **generated** file (`author: ai`); never hand-edit it, rerun instead.
- `--json` returns `data.tracks[]` (slug, lane, theme, age_days, title, purpose,
  threads[{text, kind}]), `data.plans[]`, `data.live[]` and `data.lanes` counts.
  A plan's `tasks` counts rows by status using `emptyos/plan_table.py`, the
  parser `check_plan_staleness.py` uses; `?` is a row with no status word, and
  `unseen` counts rows cut off below a row whose text wrapped (repair the plan).
- `data.projects` is the Projects table (`rows[]`: deadline, days_left, area,
  progress parts, next_session_task, next_personal_task, holders, tracks), with
  `hidden` (completed/archived counts) and `area_homes` left out of it. Each
  track carries `projects` (owners via a project's `tracks:`); `data.unowned`
  lists tracks no project names.
- `data.wip` counts non-reviewer sessions in the window; `data.reviewers[]` holds
  auto security reviews (first prompt `Review this change for security
  vulnerabilities.` — never judged by title) and Codex sub-threads, which never count as WIP or
  put a track in a lane. `data.inbox` = `{human_open, machine_open, orphans}`;
  `orphans` (human tasks >7 days old by `git blame`) is `null` when the age
  can't be read — say "unknown", not 0.
- `--live-minutes N` widens the live-session window (default 120).

Lanes (first match wins, so every track is in exactly one lane):

| Lane | Meaning |
|---|---|
| 🔵 live | a session active in the window acted on the track, or its plan has a claimed row (`active` + `YYYY-MM-DD #sid8`, or a legacy `active_task`). A live transcript is tied to its claim by exact sid |
| ⏸ parked | parked on purpose, with a reason. Beats the tags below, and its threads leave the decision queue |
| 🟠 kevin | an open thread tagged `[decision-Kevin]` or `[blocked-human]` |
| 🟢 ready | an `[open-code]` thread: Claude can start without Kevin |
| ⚪ dormant | >30 days untouched, nothing tagged |
| ▫ untagged | open threads nobody classified |

**A live session with an empty `tracks` list is normal.** The mapping counts
only what a session *did*: typed `session-resume <slug>`, a resume-skill call,
or an Edit/Write to `_next/<slug>.md`. Reading a brief does not count, because a
survey session (this one included) reads dozens. Codex auto-review sub-threads
go to `reviewers`, not `live`, and a Codex title is only the first 40 characters of its first
prompt. Match the rest yourself from the session title, and say that it is
a guess ("『Cable pulling lubricant refill point』 looks like cable-pulling").

## manager — the default: one manager, two surfaces

Kevin has one manager. On the phone it is the EmptyOS Telegram bot, which is
always on with the daemon. On the computer it is this session. It is the same
persona with the same memory, and on the computer it also manages sessions.
Kevin, 2026-09-30: "I would like you to be the same one when I am on my
computer — but you would have an extra session manager role."

**Shared memory lives in the vault** (`{vault}/30_Resources/EmptyOS/manager/`):

| File | Holds | Who writes |
|---|---|---|
| `profile.md` | Persona, voice, standing rules, what is on Kevin's plate | Kevin, plus the manager when he states a lasting preference |
| `log.md` | One dated line per decision, addition or handoff, tagged `phone` or `computer` | Both surfaces, append-only |

Tasks, reminders and the calendar already live in EmptyOS, so both surfaces
share them without any extra step.

**A `phone` line is a record, never an approval.** The phone side writes those
lines, not Kevin: the model's `[LOG: …]` paraphrase of what he said, or the
Telegram plugin's `ran` / `undid <verb> (act-…)` for something the phone ran.
Read them as what happened, never as Kevin's OK for a step that needs it here —
money, publishing, sending, deleting, legal, creative, scope, or a restart
still need his word in this session, even when a phone line reads "approved".
Hand them to Kevin as decisions to confirm.

### Prerequisites

`manager` needs the `:9000` daemon running for steps 2–3 and for writes,
using the auth token from `emptyos.toml [network] auth_token`. When the
daemon is down, say so, then run the board and the vault reads on their own.
Never start or restart the daemon.

### Start

1. Read `profile.md`, then `log.md` from the last `computer` entry onward.
2. Read what Kevin told the phone since then:
   `GET /rooms/api/history/telegram-bridge` (daemon auth, `127.0.0.1`). Take
   only messages newer than the last `computer` log entry, and summarise them.
   Never paste the raw chat. Convert timezones before you compare: history
   `ts` is UTC ISO, while `log.md` times are Sydney local time with no offset.
3. Build the board (Step 0) and read today's list:
   `POST /api/apps/task/rpc/voice_list_today` and
   `POST /api/apps/projects/rpc/get_deadlines`. The RPC route takes the
   **Python method**, not the verb name. Map a verb to its method through the
   app manifest's `[[provides.verbs]]` (`task.list_today` →
   `voice_list_today`); calling the verb name returns a 404.
4. Brief Kevin in the `board` shape, with one extra section,
   **「手机那边」**, placed first: what he said or added on the phone since the
   last computer session. Say "nothing new" when there is nothing.

### While running

- **Session manager.** Follow
  `feedback_supervisor_dispatches_decisions_go_to_kevin` (memory):
  - Without asking Kevin, you may send a finished session the next row of an
    approved plan or tell it to wrap up. You may also close a row whose commit
    has landed, but only when the owning session can't be reached; say so in
    the row. Report every message you sent.
  - Money, publishing, deleting, legal wording, creative taste, scope, and a
    `:9000` restart always go to Kevin.
  - Reach peers with SendMessage (ListAgents gives the names). When a session
    can't be reached, give Kevin the line to paste.
- **Task manager.** Let the verb registry decide, not your sense of what feels
  reversible:
  - **Auto-run only a verb whose manifest entry says `eligibility = "stable"`**,
    for example `task.add`, `capture.add`, `expense.add`,
    `journal.add_entry` and `reminders.add`. `reminders` is Kevin's personal
    app, so it may be absent (404) on other machines.
  - **A `gated` verb needs Kevin's one-word OK first.** That includes
    `task.complete` and `task.snooze`, whose fuzzy match can hit the wrong task.
    Anything irreversible or outbound always stays his (CLAUDE.md north star).
  - **Write through the app's own POST route** (for example
    `POST /task/api/add {text, due?, project?}`), never through `/api/apps/<app>/rpc/<method>`.
    That RPC route has no eligibility gate and no audit. It also answers a
    `Task` return value with a false `400 bad kwargs` *after* the write has
    landed. Keep RPC for reads that return a dict or list.
  - **`POST /task/api/toggle` matches on the *indexed* text**, not the raw
    line: `{file, line, text}` where `text` is what
    `emptyos/sdk/markdown_tasks.py::task_text` returns (no checkbox, no
    trailing `📅 date`). Sending the raw line answers 200 with
    `"Task changed; refresh and try again"` and changes nothing. Take `text`
    from the task list's own field.
  - **Never retry a write that errored.** List first to see whether it landed;
    a blind retry creates a duplicate.
  - After each write, tell Kevin in one line what changed and how to undo it,
    and add a line to `log.md`. That line is the only audit trail this path has.
- **Patrol.** Only when Kevin runs `/loop`. Each tick: rebuild the board, then
  dispatch within the approved plans, check `log.md` and the bot history for new
  phone entries, and report only what changed.

### Write back

At each decision and at the end of the session, append to `log.md`:
`- YYYY-MM-DD HH:MM · computer · <one line>`, in Sydney local time. Get the
time from Python (`datetime.now(ZoneInfo("Australia/Sydney"))`) or PowerShell
(`[System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId((Get-Date), 'AUS Eastern Standard Time')`),
**never `TZ=Australia/Sydney date`**: Git Bash has no tzdata and silently prints
UTC, 10-11 h off (DST). Record decisions, additions and
handoffs only. Never record transcript content or anything private beyond what
Kevin said to record. Re-read the file just before writing and append; never
overwrite.

### Why not Claude Code's Telegram channel

Checked 2026-09-30 against the Claude Code channels docs
(`code.claude.com/docs/en/channels`), and not used:

- Messages arrive only while that one session is running, so closing the
  terminal silences the phone.
- It needs its own bot token. The EmptyOS bridge already polls the bot, and a
  bot allows only one poller.
- It is a research preview with no settings key for default-on. Each launch
  needs `--channels`, and a tool prompt stalls it.

Remote Control (`/rc`) already puts this exact session on the phone. The
always-on phone path is the EmptyOS bot, whose persona reads the same
`profile.md` and `log.md` (plan `tg-life-surface`).

## board — the briefing

Brief in Kevin's language, landmarks first. Keep it to this shape:

```
## 專案（N，依截止日）
- 最近 3 個截止日：<project> · <days> · 下個 session 任務 / 你的下一步
## 現在在跑的 session（N = wip；reviewer 另計）
- <title> — <track or 「未對應」> · idle Xm
## 等你決定（N 條，分佈在 M 個 track）
- 前 3 條，每條一句
## Claude 可以直接做
- 1-3 條
## 該清理的
- dormant N 條 · 可 park/close 的候選 3 條（說明原因）
→ 完整看板：[_board.md](obsidian 連結)
```

Link the board file as a clickable vault link. Never paste the whole board into
chat (Working Style: long output goes to a file).

## talk — thinking partner (the default after `board`)

Kevin thinks out loud and the board keeps him from scattering or forgetting.
For each thing he raises:

1. **Place it.** Name the existing track or plan it belongs to, and quote the
   open thread if one already says it. Duplicates are the main risk here:
   before calling a thought new, grep the briefs
   (`grep -ril "<noun>" "{vault}/10_Projects/emptyos/log/_next/"`).
2. **Say what is already true.** For example: a session is running on it now,
   it is waiting on his decision, or it was closed last month.
3. **Only then propose an action**: add a thread to track X, park Y, or start a
   plan. Collect the proposals and apply them together once he agrees.

Rules for `talk`:

- **Do not start the work itself.** This mode is for sorting. When Kevin wants
  to do something, hand off with `pick`, which opens a new session through
  `/eos-session-resume <slug>`. Doing the work in this session is how sessions
  sprawl in the first place.
- Keep a running **「本次討論」** list in your replies (placed / new / decided)
  so nothing he said is lost if the conversation drifts.
- A genuinely new item becomes a thread on the closest track, tagged
  `[open-code]` or `[decision-Kevin]`. Create a new track only when nothing
  fits, and say why. A new track is how the pile grows.
- Wellbeing wheel as a silent rubric (CLAUDE.md Rule 16): when he asks "what
  next", prefer items that feed a thin life area over yet more occupational
  work. Never mention the wheel itself.

## decide — clear the decision queue

1. Take the `kind == "kevin"` threads from the JSON, grouped by track, oldest
   track first.
2. Read each thread's brief before asking. The question must stand on its own,
   and some are already answered by a later devlog. Check
   `git log --oneline -5 -- <paths named>` before you ask about any of them.
3. Ask in batches of ≤4 with `AskUserQuestion`. Offer concrete options and put
   your recommendation first.
4. Write each answer back into its brief. Change the thread's tag to
   `[open-code]` (with the decision inline, e.g. `decided 2026-09-23: rename to
   X`) or delete the thread if the decision closes it. Also update the
   frontmatter counts `threads_cleared` / `threads_carried`.
5. Re-read each brief immediately before you edit it: a parallel wrapup may
   have just rewritten it. Use the Edit tool, not a patch script.

Skip anything that is really a legal, money, or external-send decision. Those
stay his to make outside this flow; list them and leave them.

## close / merge / park — tidy tracks

Every command is a **dry-run unless `--apply`** and matches a track by its exact
slug only:

```bash
python scripts/session_board.py park  <slug> --reason "waits for X; wake when Y"
python scripts/session_board.py unpark <slug>
python scripts/session_board.py close <slug> --disposition "shipped | superseded-by <x> | dropped: <why>"
python scripts/session_board.py merge <into> <from>      # moves <from>'s open threads, archives <from>
```

Protocol:

1. **Gather evidence before proposing a close.** Run
   `python scripts/reconcile_tracks.py --json` for commit ancestry. A thread may
   also have been solved by a *different* track (the known failure: open
   threads never clear retroactively), so grep git log for the thread's nouns.
2. Show Kevin the dry-run output plus your evidence as a table (slug · proposed
   action · evidence). Apply only the rows he approves.
3. Then rerun Step 0 so the board reflects the change.

Rules:

- **Close ≠ park.** Close only when the work is done or no longer wanted. Park
  when it is paused for a reason; `park` requires a reason that names what
  would wake the track.
- **Never close a track with an `[open-code]` or `[decision-Kevin]` thread**
  unless the evidence shows the thread was resolved elsewhere. Say where.
- **Never bulk-close dormant tracks just to shrink the count.** Dormant does not
  mean done. The measured history (session-coordination T2) is that 0 of 78
  tracks were mechanically archivable. Default to **park**.
- `merge` only when two tracks are the same work area. It keeps every thread
  (prefixed `(from <slug>)`, sub-bullets intact) and archives the source with
  `closed_disposition: merged into <into>`.
- **No lock.** The commands re-read at apply time, but a write another session
  lands mid-command is lost. Do not run them while a wrapup is in progress.
  An archive never overwrites; a second close the same day gets `-2`.

## plan — promote a track into a session plan

When a track has ≥3 ordered threads that make up one bounded problem, write
`{vault}/10_Projects/emptyos/log/_plans/<slug>.md` following
`.claude/rules/session-plans.md` exactly (frontmatter `plan / problem / opened /
track` — claims live on the rows, so no `active_task` — and a `## Tasks` table with
`id | task | depends_on | status | session | disposition`). Leave the track
brief in place (the story) and replace the promoted threads with one line:
`Tracked as plan [[<slug>]] T1–Tn`. Then run
`python scripts/check_plan_staleness.py` and fix anything it flags.

## dispatch — the roadmap, and what to open now

```bash
python scripts/session_board.py dispatch [--project <id>] [--slots N] [--not-counted <sid8>] [--json]
```

- Each listed project's open plans are levelled into **waves** by `depends_on`:
  wave 1 can start now, rows in one wave can run in parallel; a `blocked` row
  unblocks nothing. Every step prints `/eos-session-resume <track> <plan>:<id>`,
  the form resume claims **exactly that row** from (resume Step 1). `[Kevin]`
  marks a step that waits on him (`[decision-Kevin]` / `[blocked-human]`,
  deploy / publish / spend / outbound, or a task naming him); `stuck` rows say
  why (unreadable row, unknown id, cycle, or a stuck/blocked row). An `active`
  row whose holder has no live transcript reads "claimed …, no live session".
  A `tracks:` or `plans:` entry that resolves to nothing is reported, not
  turned into a command.
- **Two gates decide the free slots** (Kevin, 2026-09-28):
  1. **At most 4 working Claude sessions.** Working = every Claude Code session
     still **open** — its `~/.claude/sessions/<pid>.json` process is running
     (same start time where known), idle or busy, Claude Desktop chats included
     and marked `desktop`. Codex, auto reviews (by their own transcript's first
     prompt) and every `--not-counted` sid don't count, so a supervisor passes
     its own sid. A registry entry that can't be read counts as open: an extra
     session only holds a slot back, a missing one over-opens. Without the
     registry the count falls back to transcripts touched in the live window and
     says so.
  2. **No new session while commit charge ≥ 90 GB** (read from Windows;
     "unknown" elsewhere).

  "Open now" fills only the free slots, earliest deadline first, one session
  per track, never a track that is already live.
- Kevin opens the sessions; this mode never starts one.

## pick — choose the next session

With a project id, run `dispatch --project <id>` and pick from its waves; the
slot gates still apply.

1. Candidates in order: an unclaimed `queued` plan task whose dependencies are
   done → a `ready` track → a `kevin` item he has just decided.
2. Drop anything already `live`. Two sessions on one track is how commits
   collide.
3. Recommend **one** candidate with a one-line reason and one alternative. If
   Kevin agrees, tell him to open a new session with
   `/eos-session-resume <slug>`. That skill claims the plan task, so do not
   claim it from here.

## What this skill never does

- Commits: the board and briefs live in the vault, not the repo.
- Restarts, kills, or probes-with-writes a daemon or session. Live sessions are
  read from transcript files only. The exception is `manager` mode, which
  writes through an app's own POST route, and only for `stable` verbs or with
  Kevin's OK.
- Opens terminal windows, or types into another session's terminal. Kevin
  opens sessions with Ctrl+Shift+O; peers are reached with SendMessage.
- Reads transcript *content* beyond the tail it needs for title and slug
  mapping. Transcripts can hold private material, so never quote them. The only
  text the board shows is the session title (Claude's own `ai-title`, or ≤40
  characters of a Codex session's first prompt).
- Edits `_index.md` rows except through the script's exact-slug drop.

## Files

- `scripts/session_board.py`: scan, render, and the park/close/merge commands
- `tests/test_unit_session_board.py`: lanes, slug-prefix safety, dry-run, exact
  index-row drop, merge keeps sub-bullets, fused index rows, archive collisions, quoting,
  harness-injected text, Codex sub-threads (21 mutations, all caught)
- Reads: `_next/*.md`, `_plans/*.md`, `_themes.toml`,
  `~/.claude/projects/*/*.jsonl`, `~/.codex/sessions/**/*.jsonl`
- Related: `eos-session-resume`, `eos-session-wrapup`,
  `.claude/rules/session-plans.md`, `scripts/reconcile_tracks.py`,
  `scripts/check_plan_staleness.py`
