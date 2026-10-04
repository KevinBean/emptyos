---
name: eos-footprint-worklog
description: Reconstruct how long a piece of work actually took from the Claude Code + Codex session transcripts, and log it to the worklog (60_Worklogs). Use when the user says "log the time I spent on X", "how long did X take", "put X on my worklog / timesheet", or wants documented hours on a project (NIW evidence, client billing, personal review). NOT for planning future time, and NOT for work done outside an AI session — the transcripts are the only footprint this reads.
---

# Footprint → Worklog

The coding agents already keep a timestamped record of every action they took on
your behalf. That record **is** a timesheet — nobody ever reads it as one. This
skill turns it into `60_Worklogs/{year}/{date}.md` entries the `worklog` app
parses (Work items + a `## Timesheet` line).

Split of labour, same shape as `eos-article-diagrams`: **the marker regex is
judgment** (only you know which artifacts belong to the task, including the ones
that got renamed), and it lives here. **The scan, the arithmetic and the merge
are deterministic** and live in `scripts/footprint_worklog.py`.

Read `.claude/rules/proposed-action.md` first — this writes to the vault, so the
preview is not optional.

## Prerequisites

- **Session transcripts** — `~/.claude/projects/<project>/*.jsonl` and/or
  `~/.codex/sessions/**/*.jsonl`. Both are read-only; either may be absent.
- **Vault** — resolved from `emptyos.toml` `[notes] path` unless `--vault` is
  passed. The scan and the write need no daemon.
- **Daemon on :9000** — only for the step-4 verification curl (and only to prove
  the app parses what was written). Skip it if the daemon is down; the day file
  is plain markdown either way.

## Procedure

### 1. Name the artifacts — including the dead names

Build a regex of every string that identifies the task in a transcript: file
paths, slugs, figure names, draft filenames, app ids, branch names.

**The footprint is older than the final name.** An article gets retitled, a
figure gets superseded, a draft gets deleted — the early sessions still refer to
the *old* name, so a regex built only from what exists today silently truncates
the timeline. Recover the dead names from:

```bash
git log --diff-filter=A --format="%ad %s" --date=short -- "<dir>/**/<prefix>*"   # when it first appeared
ls <images-or-media-dir>/<prefix>-*                                              # superseded figures still on disk
git log --diff-filter=D --name-only --since=<date> | grep <prefix>               # deleted drafts
```

### 2. Validate the regex before you trust it (mandatory)

A loose marker invents work that never happened. Take every *early* or *isolated*
hit and check what it actually matched:

```bash
grep -oi "[a-z0-9/_.-]*<marker>[a-z0-9-]*" <session>.jsonl | sort | uniq -c | head
```

Real case: `topology-after` matched `.tmp-topology-after`, an unrelated temp
path, and fabricated **twelve** phantom work-days across two months before it was
caught. This is `.claude/rules/audits.md` false-positive discipline applied to
your own timesheet — and a timesheet is exactly the artifact where invented data
is least acceptable.

Sanity checks: does the first day match when the work plausibly started? Are
there days with a single 3-minute hit and nothing around them? Those are
mentions, not work.

### 3. Preview

```bash
python scripts/footprint_worklog.py \
    --topic "Architecture article" \
    --match "system-evolution-|how-architecture-emerges|topology-(beginning|now|thread)\b" \
    --project "EmptyOS Brand" \
    --employer EmptyOS \
    --item "complete:Published — <title> (~1,800w, 7 figures)" \
    --item "review:LinkedIn companion drafted — schedule Tue 21:30 AEST"
```

Writes nothing. Read the per-day table back to the user before applying — they
are the only one who can say "that Tuesday wasn't this task".

### 4. Apply, then verify through the app

```bash
python scripts/footprint_worklog.py ... --apply
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:9000/worklog/api/day?date=<YYYY-MM-DD>" \
  | python -c "import sys,json;d=json.load(sys.stdin);print(d['timesheet'],[g['project'] for g in d['projects']])"
```

The day file is only real if the app parses it: `timesheet` non-empty and the
project present under `projects` (**not** `work` — that key does not exist).

## How the hours are derived

- An **event** is one timestamped JSONL record (Claude Code or Codex).
- An event is **on-topic** when its raw text matches `--match`.
- Consecutive on-topic events ≤ `--idle` (15 min) apart form one continuous
  **run**, counted at full wall-clock. The work *between* two mentions of a file
  is still work on that file.
- A run's last event gets `--tail` (3 min), so an isolated mention costs minutes,
  not zero.
- Runs from every tool are **unioned** on one timeline.

Two traps this design exists to avoid:

| Trap | What happens | Why |
|---|---|---|
| Attributing a block by the **fraction** of lines naming the file | ~3× undercount | A session that is 100% one task names it in only ~30% of its lines; the rest is thinking, edits, tool chatter |
| **Summing** per-tool totals | Overcount (0.9 h on one article) | Claude and Codex run side by side. There is one human. Union the intervals |

## Employer is a decision, not a default

`worklog` stamps `employer` from the `worklog.default_employer` setting — the day
job. Personal, brand, or venture work must **not** inherit it: the timesheet PDF
(`/worklog/api/timesheet.pdf?employer=`) filters on that field, so a Sunday spent
writing would otherwise land inside an employer's export. Pass `--employer` with
the venture (`EmptyOS`), or omit it for unattributed personal work. This is why
the script writes the file directly instead of calling `POST /worklog/api/log` —
that endpoint cannot express a non-default employer.

## When NOT to use it

- **Work done off-transcript** — whiteboard, reading, meetings, thinking in the
  shower. The transcripts cannot see it, and padding them to compensate turns a
  measurement into a guess.
- **Planning** ("how long *will* this take"). This reads the past only.
- **Anything already logged** by hand. The script merges by project heading, so
  re-running is safe, but two sources of truth for the same hours is not.
- **A task with no distinctive string.** If you cannot write a regex that matches
  the work and nothing else, the footprint cannot see it either.

## Known wrinkle: the logging session logs itself

The session that runs this skill mentions the markers constantly (in the regex,
in the greps), so it registers as on-topic work. Usually minutes. Say so in the
timesheet note rather than letting it silently inflate the total — or pass
`--since` / trim the last run if it matters.

## Cross-references

- `scripts/footprint_worklog.py` — the scan + merge (`--dry-run` by default)
- `apps/public/standard/worklog/parser.py` — the day-file grammar this writes to
- `.claude/rules/proposed-action.md` — preview before apply; the vault is user data
- `.claude/rules/audits.md` — false-positive discipline for the marker regex
- `.claude/rules/time-dimension.md` — read the past before acting on it
