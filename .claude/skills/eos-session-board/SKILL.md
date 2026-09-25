---
name: eos-session-board
description: See and sort out every EmptyOS work thread at once — dev tracks, session plans and the Claude/Codex sessions running right now — as one board with lanes (in flight / waiting on Kevin / Claude can start / parked / dormant / untagged), then talk it through, answer the decisions waiting on Kevin, close/merge/park tracks, promote a track into a plan, or pick the next session. Use when the user says "session board", "what sessions are running", "理清 session", "我在做哪些事", "what am I forgetting", "too many tracks", "what's waiting on me", "close / merge / park this track", or wants to think out loud about priorities across sessions. NOT for resuming ONE track (use eos-session-resume) and NOT for closing out the current session (use eos-session-wrapup).
---

# EmptyOS Session Board

`/eos-session-resume` opens one track and `/eos-session-wrapup` closes one
session. This skill works one level up: all ~115 tracks, the `_plans/`, and the
sessions running now, side by side. It exists so Kevin has **one place to think
across sessions** without holding the list in his head.

All data comes from `scripts/session_board.py`, which computes and does not
judge. The judging is yours, done in conversation with Kevin.

## Modes

The argument selects the mode. With no argument, run `board` and then `talk`.

| Mode | What it does | Writes? |
|---|---|---|
| `board` | Build the board, write `_next/_board.md`, brief Kevin in ≤15 lines | only `_board.md` (generated) |
| `talk` | Kevin says what is on his mind. Map each thought onto the board | only after he agrees |
| `decide` | Walk the decision queue (`[decision-Kevin]` / `[blocked-human]` threads) | the briefs he answers |
| `close` / `merge` / `park` | Tidy tracks | yes, dry-run first |
| `plan` | Promote a track with ≥3 ordered threads into a `_plans/` plan | new plan file |
| `pick` | Recommend the next session's ONE task, then hand off | none (resume claims) |

## Step 0 — build the board (every mode starts here)

```bash
python scripts/session_board.py --write --json
```

- `--write` renders `{vault}/10_Projects/emptyos/log/_next/_board.md`. It is a
  **generated** file (`author: ai`); never hand-edit it, rerun instead.
- `--json` returns `data.tracks[]` (slug, lane, theme, age_days, title, purpose,
  threads[{text, kind}]), `data.plans[]`, `data.live[]` and `data.lanes` counts.
- `--live-minutes N` widens the live-session window (default 120).

Lanes (first match wins, so every track is in exactly one lane):

| Lane | Meaning |
|---|---|
| 🔵 live | a session active in the window acted on the track, or its plan has a claimed `active_task` |
| ⏸ parked | parked on purpose, with a reason. Beats the tags below, and its threads leave the decision queue |
| 🟠 kevin | an open thread tagged `[decision-Kevin]` or `[blocked-human]` |
| 🟢 ready | an `[open-code]` thread: Claude can start without Kevin |
| ⚪ dormant | >30 days untouched, nothing tagged |
| ▫ untagged | open threads nobody classified |

**A live session with an empty `tracks` list is normal.** The mapping counts
only what a session *did*: typed `session-resume <slug>`, a resume-skill call,
or an Edit/Write to `_next/<slug>.md`. Reading a brief does not count, because a
survey session (this one included) reads dozens. Codex auto-review sub-threads
are dropped, and a Codex title is only the first 40 characters of its first
prompt. Match the rest yourself from the session title, and say that it is
a guess ("『Cable pulling lubricant refill point』 looks like cable-pulling").

## board — the briefing

Brief in Kevin's language, landmarks first. Keep it to this shape:

```
## 現在在跑的 session（N）
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
track / active_task: ""`, and a `## Tasks` table with
`id | task | depends_on | status | session | disposition`). Leave the track
brief in place (the story) and replace the promoted threads with one line:
`Tracked as plan [[<slug>]] T1–Tn`. Then run
`python scripts/check_plan_staleness.py` and fix anything it flags.

## pick — choose the next session

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
  read from transcript files only.
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
