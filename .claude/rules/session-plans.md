---
paths:
  - ".claude/skills/eos-session-resume/**"
  - ".claude/skills/eos-session-wrapup/**"
  - "apps/extension/dev/devboard/**"
  - "scripts/check_plan_staleness.py"
  - "emptyos/plan_table.py"
  - "scripts/session_board.py"
---

# Session Plans — decomposition + closure for multi-session problems

A **session plan** is the missing middle between a *problem* and a *session*: an
ordered list of session-sized tasks with dependencies, living in one file, where
each task is claimed by exactly one session and closed with a disposition. It is
the artifact that lets "問題 → 任務分解 → session 執行 → 追蹤" survive a context
reset.

**Store:** `{vault}/10_Projects/emptyos/log/_plans/` (sibling of `_next/`).
**Producers/consumers:** `/eos-session-resume` (claim) + `/eos-session-wrapup`
(close). **Vocabulary reused from:** `emptyos/sdk/fix_queue.py` (`DISPOSITIONS`).
**Dashboard:** devboard reads `_next/`; a plans lane is a pure-read addition.

## Why this exists — plans vs tracks

`_next/<track>.md` briefs are excellent and stay exactly as they are. But a
**track is a work area, not a task** — `career`, `core-infra`, `model-bench`
never "complete", so nothing about them ever closes. Two measured consequences:

- A 2026-07-03 reconciliation audit found **87 of 121 tracks were already
  resolved** — done work sat in the index until a hand-run three-way audit
  archived it. By 2026-07-25 the index was back to **78**. The pile re-forms
  every few weeks because `classify_track()` computes staleness but nothing
  consumes it to close anything.
- Sessions sprawl. With no claimed deliverable, one session did payslip parsing
  + an expense scheduler + a housing verdict + retirement modelling.

A plan fixes both by being **bounded**: it has a finite task list, so it can
finish, move to `done/`, and leave the index on its own.

| | Track (`_next/`) | Plan (`_plans/`) |
|---|---|---|
| Shape | open-ended work area | bounded problem |
| Content | prose brief, on-deck card | ordered task table + deps |
| Closes when | never (archived by hand) | last task closes → `done/` |
| Session binding | none | each claimed row names its session (`#sid8`) |
| Keep using for | ongoing areas, exploration, anything unbounded | anything you can enumerate as ≥3 ordered tasks |

The two compose: a plan names the `track` its session briefs land in, so
narrative history keeps flowing to `_next/<track>.md` unchanged.

## File format

```markdown
---
type: session-plan
plan: approval-correctness
problem: Three apply_pending TOCTOU races + an unbound ToolConsent session
opened: 2026-07-25
track: core-infra
---

# Approval correctness

Why this is bounded, what was decided, what the review corrected.

## Tasks

| id | task | depends_on | status | session | disposition |
|----|------|-----------|--------|---------|-------------|
| T1 | SDK atomic claim + land in all three apply paths | — | active | 2026-07-25 #efbad179 | |
| T2 | ToolConsent session binding + TTL | — | queued | | |
| T3 | Reuse candidate_key as content id | T1 | blocked | | |

## Notes

Decisions, corrections, links. Free prose.
```

- `status` ∈ `queued` · `active` · `done` · `blocked`
- `disposition` (set only when `status: done`) ∈ `shipped` · `deferred` ·
  `declined` · `dismissed` · `done` — the `fix_queue.DISPOSITIONS` vocabulary
  minus `planned` (meaningless for a task that *is* the plan). One lifecycle
  across findings, gaps, and plan tasks, not a fifth. A note may follow the
  word after an em dash — `shipped — 9670d19c, live on :9000` — and the
  checker reads only the word before ` — `.
- `session` = `YYYY-MM-DD #sid8`. While the row is `active` it is the **claim**:
  the date it was claimed and the first 8 characters of the claiming Claude Code
  session id (the form the status bar shows; the SessionStart hook prints it as
  `session: #<sid8>`). Once `done` it names the session that closed it — the
  date is the dated devlog, the join back to narrative history. A session with
  no id to hand (Codex has no SessionStart hook) writes the date alone.
- `active_task` (frontmatter) is **legacy**. It held one id per plan, so two
  sessions working one plan in parallel could not both be marked — P6 fell back
  to an unmarked row on 2026-09-26. New claims never write it; a plan that still
  carries one is honoured as a claim on the row it names, and cleared at that
  row's close.
- A `blocked` row carries its reason **inline in the task cell** with the tag
  vocabulary the briefs already use and devboard already parses:
  `[blocked-human]` · `[decision-Kevin]` · `[open-code]`. The **task** cell is
  the place: a tag on the *status* cell (`blocked [decision-Kevin]`) parses, but
  is reported as `bad_status` drift — the status column holds one of the four
  words and nothing else.
- `depends_on` is a comma-separated list of ids, or `—`. Cycles are an authoring
  error; the staleness scanner reports them.

### No `_plans/_index.md` — glob the directory

Deliberate asymmetry with `_next/`. An index is a **second place holding the same
truth**, and keeping it in sync by hand is exactly what produced the 87-of-121
stale-row pile this rule exists to prevent. Each plan's frontmatter is
authoritative; consumers glob `_plans/*.md` and read `problem` / the claimed rows /
task-table status. One place, no reconciliation step.

**Every consumer MUST skip sync-conflict copies.** The vault syncs, so an edit
during a sync produces a sibling like
`session-coordination.sync-conflict-20260725-104221-6TUUOTZ.md` or
`session-coordination 2.md` — a *stale snapshot carrying stale claims*.
A naive glob reads it as a separate plan holding a claim, which blocks the real
plan and can strand work. This is not hypothetical: one appeared within minutes
of the first plan being written. Filter on the filename:

```python
SKIP = ("sync-conflict", ".conflict")          # plus a trailing " <N>" stem
if any(m in path.stem for m in SKIP) or re.search(r" \d+$", path.stem):
    continue
```

A conflict copy whose content is a strict subset of the live file is safe to
delete (`comm -23` shows no unique lines); one that has diverged needs a human —
same `safe` vs `review` split as `scripts/check_vault_structure.py`.

## The claim protocol (this is the load-bearing part)

**One session = one task** is enforced at the two skill boundaries, because
that's the only place it can be:

1. **`/eos-session-resume`** — after picking a plan, select the first task whose
   `status: queued` **and** every `depends_on` id is `done`. Flip that row to
   `active` and stamp its `session` cell `YYYY-MM-DD #sid8`. State the claimed
   id in the opening brief. Rows already `active` belong to other sessions;
   skip them and claim the next free one.
2. **The session does only that task.** Work that turns out to be a different
   task gets **appended as a new row**, never folded into the claimed one.
3. **`/eos-session-wrapup`** — close **only** the claimed task: set
   `status: done` + a `disposition` + the `session` cell (`YYYY-MM-DD #sid8`),
   and clear a legacy `active_task` if it names that row. Then write the track
   brief as it does today.

### The unit is the claim cycle, not the conversation

A conversation MAY run several claim cycles **serially** — claim → do → close →
claim the next. What is forbidden is holding **two claims at once**. First use of
this rule (2026-07-25) hit the ambiguity immediately: the session that built the
mechanism finished its task and Kevin moved to feature work. Ending the
conversation to satisfy a literal "one session = one task" would buy nothing —
the anti-sprawl property comes from each task **closing discretely with its own
disposition**, and from discoveries being **appended as new rows**, not from
one-task-per-conversation.

So: a session holds at most one claimed row at a time; close it before claiming
the next. Several **sessions** may hold different rows of one plan at once —
that is what per-row claims are for. Wrapup closes every task the conversation
claimed, each with its own disposition and the same `session` stamp.

### A claim guards the TASK, not FILE WRITES

Two different races, and the marker only covers one. Observed live on
2026-07-25: while one session was running wrapup, a second session claimed T8 in
the same plan, closed it, and appended three discovered rows — the protocol
worked, and a mid-claim read even caught `active_task: T8` in flight. Nothing
was lost, but only because their writes happened to interleave cleanly.

The hazard is the **edit pattern**, not the claim: a read-whole-file →
string-replace → write-whole-file pass (the obvious way to script a status flip)
is a read-modify-write on a file another session may be editing, exactly the
vault race in CLAUDE.md § Development Gotchas. So:

- **Never bulk-rewrite a plan file.** With per-row claims, several sessions
  edit the same file, so this race is now the normal case. Before editing,
  re-read it; if the mtime moved since you read it, re-read rather than writing
  your buffer over theirs.
- **Editing another session's rows is never necessary.** Close only your own,
  append your own. A wrapup that finds someone else's task newly closed should
  report it and leave the file alone — which is what the observed session did.
- Prefer a **targeted single-row edit** over rewriting the whole document, so a
  concurrent append to a different part of the file survives.

### Refuse to claim a row that is already `active`

A claimed row is a **mutual-exclusion claim, not a label.** An `active` row is
held by the session that stamped it — running, idle, or dead, and nobody else
can tell which by looking at the row; a second session must pick a different
row (or plan) rather than claim into it. This is the same atomic-claim
discipline as `staff/approvals.py` (`status = "approving"` written under the lock
before the work runs) and the same bug class the `apply_pending` review found —
read-check-then-act on shared state without a claim marker double-executes. A
vault file gives no lock, so the marker is the whole mechanism: write it
**before** starting work, not after.

A stale claim (its date days old, no live transcript with its `#sid8`) means a
session died mid-task. Resume surfaces it and asks — never silently steals the
claim. `scripts/session_board.py` matches a live transcript to its claimed row
by exact sid and marks a claim `(no live session)` when no transcript with its
sid was touched in the `--live-minutes` window (default 120) — idle and dead
look the same there, so widen the window before calling a holder dead. `/clear` starts a new session id and drops the context that
knew about the claim, so **close or release a claim before `/clear`**; one left
behind reads as a dead session.

The marker also has a blind side: **a `queued` row is not proof nobody is
working.** A session started before the claim discipline reaches it carries
no marker at all, and on 2026-08-07 exactly this collided — a second session
verified the frontmatter empty, claimed T2, and an unclaimed session already
mid-flight wrote the same records one minute ahead of it. (A row still marked
`in-progress` parses as `active`, so it is held, not claimable.) Before claiming
a `queued` task whose track was touched today, check the
**write frontier** — mtimes on the artifacts the task produces. Fresh files =
a live session; surface, don't claim. (`feedback_claim_check_write_frontier`;
the check is in `/eos-session-resume` Step 1.)

## Closure

- Last task closed → move the whole file to `_plans/done/<slug>.md`. The closed
  file **is** the record: every task carries its disposition and session inline.
- **No separate ledger in v1.** `fix_queue` has one because fix-agent needs
  cross-run queries; nothing needs cross-plan queries yet. Add
  `done/_ledger.jsonl` when a real consumer asks (rule 9).
- `scripts/check_plan_staleness.py` (built 2026-08-07; preflight scopes `always`
  + `vault`, and Step 0 of `/eos-session-resume`) reports: plans with no
  `queued` task and no `done/` move, stale row claims, an `active` row with no
  claim stamp (or a legacy `active_task` that disagrees with its row), unresolvable `depends_on` ids, and dependency cycles —
  plus three vocabulary checks that are free once the table is parsed
  (`done` with no disposition, an off-vocabulary status, an off-vocabulary
  disposition). Advisory — a plan is a human artifact, so the scanner surfaces
  and never rewrites. Its table parser lives in `emptyos/plan_table.py`, shared
  with `scripts/session_board.py` and the devboard plans lane (extracted to
  `scripts/` 2026-09-26, moved top-level 2026-09-28).

  **Closure advice is withheld whenever the table did not fully parse** (added
  2026-09-12). `unclosed_plan` is the only finding here whose advice is
  destructive — it says "archive this" — so it must never rest on a row nobody
  read. Two shapes suppress it, and both were live in this directory:
  an **unreadable row** (no recognisable status cell) and `table_truncated`,
  where a row's own content wraps onto a line with no leading `|` and the walk
  stops there. The second is the one to watch when authoring: a task cell is
  prose and grows, and `conversation-ingest-backlog` reached a 19,483-character
  cell spilling onto a continuation line — so a 13-row table reported **2 rows,
  `open: 0`**, i.e. "finished". Keep each row on one line.

  It runs *before* the claim in Step 0, not after: `stale_claim` is precisely
  the judgment the claim protocol above otherwise asks a human to make.
  A status outside the four words still parses (aliases like `in-progress` are
  read as `active` and reported), because dropping the row would cascade a
  phantom `unresolved_dep` onto whatever depends on it.

## When NOT to write a plan

- **Fewer than ~3 tasks.** A one- or two-session job is what `TaskCreate` + a
  track brief already handle well (`feedback_agent_todos_three_tier`). A plan
  file for two tasks is ceremony.
- **Unbounded / exploratory work.** "Improve the KB", "keep dogfooding" — those
  are tracks. A plan you can't finish can't close, which reintroduces the exact
  drift this exists to fix.
- **Human task management.** `apps/public/core/task/` + `apps/public/standard/projects/` own the human side;
  plans are agent-facing session coordination. Don't merge them.
- **A queue that already exists.** Fix-prompts, deferred rows, and gap-registry
  items have their own lifecycles. A plan may *reference* them; it must not
  re-file them.

## Cross-references

- `.claude/rules/loop-traceability.md` + `emptyos/sdk/fix_queue.py` — the
  disposition vocabulary and the queue→done→record shape this copies.
- `.claude/rules/time-dimension.md` — a plan is the *future* half of a track's
  past/future/now; read both before acting.
- `feedback_agent_todos_three_tier` (memory) — the three tiers, and the
  5+-session revisit trigger this rule answers vault-natively rather than by
  installing a task-tree MCP.
- `apps/extension/dev/devboard/` — `_collect_tracks_sync` is the model for a
  plans lane: parse the table, fail soft per row, never let a malformed row hide
  the others.
