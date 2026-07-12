---
name: eos-usecase-audit
description: Walk real use-case scenarios against the live system across three surfaces (web UI, CLI, external bridges), detect BOTH bugs and feature gaps, route every finding to exactly one destination, then fix-and-re-walk in a loop until the pass is clean — queuing oversized fixes to the dogfood fix-prompt queue and logging gaps to the gap registry — before chaining into /eos-simplify and /eos-session-wrapup. Authoring the scenarios is delegated to eos-new-usecase; this skill consumes the catalog, it does not invent use cases. Use when the user says "use-case audit", "usecase audit", "walk and fix", "dogfood and fix everything", "audit the apps with real use cases", "full product audit with fixes", or "/eos-usecase-audit". NOT the scenario-authoring skill (use eos-new-usecase — this one calls it), NOT the report-only hand walk (use eos-ui-walk — this skill wraps its mechanics and adds the fix loop), NOT the backend correctness-scanner sweep (use eos-bug-audit), NOT a market-benchmark feature sprint (use dev-app-optimizer / eos-app-gap-analysis), NOT the queue drain itself (use eos-fix-drain for items this skill queues).
---

# EmptyOS Use-Case Audit — walk, detect, fix, converge

`eos-ui-walk` finds friction and reports. **This skill closes the loop**: it takes
the durable use-case scenarios in the dogfood catalog (authored by
`eos-new-usecase`), walks them across **three surfaces** (web UI, CLI, external
bridges), detects **bugs AND feature gaps**, routes every finding to exactly one
destination, fixes what fits in the session, re-walks until the pass is clean,
then hands off to the standard tail (`/eos-simplify` → `/eos-session-wrapup`,
whose Step 4 writes the devlog).

You are the orchestrator, not a new engine. Scenarios come from `eos-new-usecase`;
walk mechanics come from `eos-ui-walk`; the use-case registry is
`apps/extension/dev/dogfood-agent/scenarios/`; oversized fixes go to the dogfood
fix-prompt queue that `/eos-fix-drain` drains; market-shaped gaps go to the
`eos-app-gap-analysis` registry. Reuse, don't restate.

## When NOT to run this

- Authoring a scenario (not walking one) → `eos-new-usecase`.
- Report-only dogfood pass, no fixing wanted → `eos-ui-walk`.
- Backend correctness sweep (async wedges, vault RMW races, scanners) → `eos-bug-audit`.
- Market benchmarking / feature-sprint scoring → `dev-app-optimizer` / `eos-app-gap-analysis`.
- Draining an already-populated fix queue → `eos-fix-drain`.
- Visual/design polish → `eos-page-design-review` / `eos-design-system-audit`.

## Phase 0 — Preflight + loop memory (every run)

```bash
curl -s -m 5 http://127.0.0.1:9000/api/health     # daemon up?
git status --short                                 # uncommitted files = off-limits for fixes
```

- **Daemon down → surface it and stop.** NEVER restart `:9000`/`:9001`
  (`.claude/rules/daemon-handling.md`).
- **Read `data/usecase-audit/backlog.md` FIRST.** It is this skill's loop
  memory (same discipline as eos-bug-audit's `data/audit-loop/backlog.md`):
  don't re-litigate rejected false positives; **prior-run DEFER items are the
  highest-yield place to start**. Missing file = first run; create it in Phase 6.
- Read `data/apps/dogfood-agent/fix-prompts/_queue.md` — don't re-queue a
  finding that's already pending.
- Set up the run folder (same convention as eos-ui-walk):
  `data/ui-walk/usecases/<YYYY-MM-DD-HHMM>/` + `steplog.jsonl` inside it.
  Run id for this session: `usecase-audit-<YYYY-MM-DD-HHMM>`.

## Phase 1 — Get the use-case set (3–6, surface-mixed)

**Authoring is delegated to `eos-new-usecase`. Do not invent use cases here.**
This skill is the *auditor*; it consumes scenarios. A walker that authors its own
use cases writes flows it already knows the system passes — shallow, repetitive,
and blind to gaps by construction (that failure is exactly what the 2026-07-12
dry run exhibited: three hand-written scenarios, all of which the system mostly
passed, and zero web-surface gaps found).

So: when the run needs a use case that the catalog doesn't already cover,
**invoke `eos-new-usecase`** to author it, then walk what it produces. Pick the
run's set from the existing catalog first (inputs below); only reach for the
authoring skill to fill a real coverage hole.

Inputs (read all, cheap):

| Input | Where | Tells you |
|---|---|---|
| App inventory | `GET :9000/api/apps` | what exists |
| Walk coverage | `data/apps/dogfood-agent/ui-walks/coverage.json` | which apps have zero/stale walk credit |
| Friction heatmap | `data/apps/dogfood-agent/behavior-rollup.json` | where personas historically hit friction |
| Scenario catalog | `apps/extension/dev/dogfood-agent/scenarios/*.md` (+ their `expected_apps`) | which flows are already authored |
| Recent churn | `git log --oneline --since="14 days ago"` | recently-touched surfaces = highest regression yield |
| Loop memory | `data/usecase-audit/backlog.md` | prior deferrals + settled FPs |

**Selection rule:** pick **3–6 use cases** per run — at least one **cross-app
web** flow, at least one **CLI** flow, at most one **bridge** lane. Prefer, in
order: (a) prior-run DEFER items, (b) apps/surfaces touched in the last 14 days,
(c) apps with zero or stale coverage credit. Prefer flows that **cross apps**
and flows that **write then read back** — that's where real friction and real
gaps hide.

The house contract a scenario file must follow (frontmatter schema,
`{{DAEMON_URL}}` + guardrails, turn/fumble caps, tag vocabulary, `## Wrap`,
the `runtime: manual` rotation guard) lives in **`eos-new-usecase`'s
`scenario-shape.md`** — the single source of truth; don't restate it here.
Scenario files ARE committed (they're product test assets); everything under
`data/` is not.

## Phase 2 — Walk each use case (per surface)

One steplog for the whole run; every row carries the extra `"surface"` field.

### The walk writes REAL data into the user's REAL vault — mark it (load-bearing)

This walk drives `:9000`, which is mounted on the user's actual vault. Every
capture, task, journal entry, and record it creates is **indistinguishable from
something the user wrote** unless you mark it. The 2026-07-12 dry run left six
unmarked artifacts in the live vault (a capture, a journal entry, tasks, and the
reactor breadcrumbs they rippled into) and they had to be removed by hand.

**Every value this walk writes into the vault MUST carry the `PLAYWRIGHT-TEST-`
prefix in its primary text field** — the same marker the pytest suite uses, so the
existing guard can sweep it:

```
capture:  "PLAYWRIGHT-TEST-cable derating below the water table #dev"
task:     "PLAYWRIGHT-TEST-call the depot about clearances"
journal:  "PLAYWRIGHT-TEST-solid focus morning"
```

The prefix does not weaken the walk — tag routing, project routing, ripples, and
read-back all behave identically. It only makes the artifact sweepable.

**Never mark by hand-editing the vault.** Write through the app (the UI / the API),
exactly as a user would; the marker rides in the content.

**Do not walk destructive flows on the live vault at all** (bulk delete, archive
sweeps, `publish.deploy`). If a use case needs one, walk it on a leased sandbox
member with its own throwaway vault instead.

- **Web** — follow **eos-ui-walk Steps 0.5–3** exactly (browser backend pick,
  one-time `/?token=<TOKEN>` auth deep-link, act → settle → screenshot →
  judge + log). Do not restate them here; that skill is the mechanics manual.
- **CLI** — Bash-drive real `eos` commands (`eos capture "..."`, `eos task list`,
  `eos search "..."`). Prefer `--json` where the agent-cli envelope exists
  (`.claude/rules/agent-cli.md`): exit code is the primary signal, `{ok, code,
  message, data}` the payload. Log the full command line in the steplog `url`
  field; no `shot`. Judge with the same vocabulary — `fail` = bad exit /
  traceback; `confusing` = output a human can't act on; `missing` = the flow
  needs a command/flag that doesn't exist.
- **Bridge** (Telegram, Chrome extension, phone PWA, MCP foundry) — probe what
  is **HTTP-checkable from local** (the `telegram-inbound` scenario is the
  precedent: plugin health, flag state, parse endpoints). Steps that need human
  hands (phone touch, extension popup) are logged `"status": "skipped"` with
  `note` prefixed `MANUAL: ` — they become the manual checklist in the final
  report, not silent omissions.

Steplog row (eos-ui-walk schema + `surface`, and the status vocabulary gains
**`missing`**):

```json
{"usecase":"CLI daily driver","surface":"cli","step":2,"action":"eos task list --json","status":"missing","note":"no --json on task list; human table only — an agent can't parse this","url":"eos task list --json"}
```

`status` ∈ `pass | slow | confusing | fail | missing | skipped | info`.
**`missing` = this use case needs a capability that doesn't exist.** It renders
as a purple GAP badge in the report, ranked just below BROKEN.

## Phase 3 — Triage (false-positive discipline — `.claude/rules/audits.md`)

- **Data-volume slowness on the real vault is NOT a code bug** — log `slow`,
  flag to the user, never blind-fix against real data.
- **Transient one-off** that doesn't reproduce on a second try → `info` or drop.
- **Design friction** (flat panel, weak affordance) → `info`, route to
  `eos-page-design-review` / `eos-design-system-audit`.
- Verify a suspected bug against committed state (`git show HEAD:<path>`); a
  file with uncommitted parallel-session edits is **not yours to judge or fix**.
- **A gap is only `missing` if the use case genuinely needs it.** You were
  trying to complete a real flow and hit a wall — that's a gap. "Wouldn't it be
  nice if…" while cruising past is wishlist inflation; drop it.

## Phase 4 — Route every finding (one destination each)

| Finding | Test | Destination |
|---|---|---|
| Bug — root-caused, small | ≤2 files, ~≤50 lines, no cross-app contract change, you can name the broken line | **Fix in-session** (Phase 5), re-walk to verify |
| Bug — large / root cause unclear / touches parallel-session files | anything else | **Queue fix-prompt** `kind: bug` (shape below) → later `/eos-fix-drain` |
| Gap — small missing capability in an existing app | one app, obvious owner (a button, a filter, an endpoint, a `--json` flag) | **Queue fix-prompt** `kind: missing` — logged, **never built in-session** |
| Gap — new capability class / market-shaped | competitors have it, or it's a new subsystem | **Append a row to `{vault}/30_Resources/EmptyOS/gap-analysis/<app-id>.md`** (status `open`); if no note exists for that app, record in backlog + recommend `/eos-app-gap-analysis <app>` |
| Gap — deliberately-not-now with a trigger | "when X happens, build Y" | **Propose a `docs/DEFERRED-WORK.md` row in the final message — never auto-edit that file** |
| Design friction | works but feels wrong | `info` → design skills |
| Vault-scale slowness | reproduces only at real-vault size | `slow` + flag to user; not a code bug |

Queue via `POST :9000/dogfood-agent/api/queue/add` with body
`{"filename": "<slug>.md", "content": "<prompt>"}` (Bearer token from
`emptyos.toml [network] auth_token`; POST non-ASCII bodies via Python `urllib`,
not `curl -d`). The prompt copies the `emptyos/sdk/fix_queue.py` contract
**exactly** — the two headings are load-bearing (fix-agent parses them):

```markdown
---
kind: bug
app: task
key: usecase-audit::task::reschedule-drops-note
count: 1
first_seen: 2026-07-12T21:04:00
last_seen: 2026-07-12T21:04:00
last_run_id: usecase-audit-2026-07-12-2104
recent_runs: ['usecase-audit-2026-07-12-2104']
source_hint: apps/public/core/task/pages/index.html:214
---
# Fix this EmptyOS friction item

**Kind**: `bug` · seen 1× · **Persona**: Kevin · **Scenario**: capture-to-project-ripple

## What the persona reported

> Rescheduling a task from the board view silently drops the task's note field.

## Where to look

`apps/public/core/task/pages/index.html:214` (best-effort — verify before editing)
```

Key format `usecase-audit::<app>::<short-slug>`; filename = slugified key.
Omit `## Where to look` when you have no hint. Check `_queue.md` first — bump
nothing, dupes are the queue's own job.

## Phase 5 — Fix → re-walk inner loop

- **Root cause before fix** (`.claude/rules/debugging.md`); prefer the platform
  fix over N per-app fixes (`feedback_platform_fix_for_n_app_bugs`); reuse
  `EOS_UI` / `EOS.*` / SDK helpers — grep `base_app.py` before writing plumbing.
- **Static files** (`pages/*.html`, `*.js`, CSS) hot-reload — re-walk the
  affected steps immediately.
- **Python files** — `py_compile` + the relevant unit slice, then verify on a
  **leased sandbox** (`POST /sandbox/api/lease`, restart the member, probe —
  `.claude/rules/sandbox-driven-testing.md`). Never restart `:9000`.
  **Batch ALL Python changes into ONE restart request at session end**
  (`feedback_batch_python_changes`) — don't ping the user per file.
- **Convergence rules (hard):**
  - Re-walk **only the use cases whose steps failed** — not the whole set.
  - **Stop when a full pass over this run's set has zero `fail` and zero new
    bug findings.**
  - **Hard cap: 3 inner iterations.** Anything still failing at the cap gets
    queued as a fix-prompt and recorded `[DEFER]` in the backlog. Do not grind.
  - `slow` / `confusing` / `missing` residue **routes out (Phase 4), never
    loops** — the loop converges on breakage only.

## Phase 6 — Record (append-only loop memory)

Append a dated section to `data/usecase-audit/backlog.md` (create with a
header comment on first run):

```markdown
## Run usecase-audit-2026-07-12-2104

### Use cases walked (surface · scenario · result)
- web · capture-to-project-ripple (NEW scenario) · converged pass 2
- cli · cli-daily-driver · clean
- bridge · bridge-phone-pwa · 2 MANUAL items outstanding

### Findings
- [FIXED] task board reschedule drops note — pages/index.html:214, commit abc1234, re-walked clean
- [QUEUED bug] search filter 500s on empty query → fix-prompts/usecase-audit-search-….md
- [QUEUED missing] task list has no --json envelope → fix-prompts/usecase-audit-task-….md
- [GAP→registry] expense has no recurring-rule — gap-analysis/expense.md row `expense-recurring`, open
- [GAP→proposed] DEFERRED-WORK row proposed (user decides): …
- [FP rejected] /task/ 1.8s load = vault scale, not code — do not re-report
- [DEFER] viz export flake — didn't reproduce twice; recheck next run

### Coverage delta
- coverage.json: task 0→2, expense 1→2

### Scenarios authored
- scenarios/capture-to-project-ripple.md (surface: web)
```

## Phase 7 — Sweep the vault, then render + report

**Sweep FIRST — a run does not end with its test data still in the user's vault:**

```bash
python scripts/check_vault_test_leak.py            # what did this walk leave?
python scripts/check_vault_test_leak.py --purge    # remove owned files + strip marked lines
```

`--purge` removes only `PLAYWRIGHT-TEST-`-marked artifacts (that's why Phase 2's
marking rule is load-bearing) and never touches real notes. Anything it reports as
**review** is a marked value on a non-list line — strip that by hand, don't leave it.
If the walk wrote something the guard can't see, you forgot to mark it: remove that
line by exact match yourself and fix the marking next run.

```bash
python scripts/ui_walk_report.py \
  --steplog data/ui-walk/usecases/<run>/steplog.jsonl \
  --out data/ui-walk/usecases/<run>/report.html \
  --title "Use-case audit — <date>" --persona Kevin
```

Final message to the user: report path · headline findings in plain language ·
fixed (commit hashes) · queued (filenames + a pointer to `/eos-fix-drain`) ·
gap rows written · DEFERRED-WORK proposals (for the user to accept) · the
**MANUAL bridge checklist** (every `MANUAL:` step, so the user can run them by
hand).

## Phase 8 — Tail

1. **If any `.py` changed:** state the batched-restart reminder ONCE — "N
   Python fixes are sandbox-verified but need one `restart.bat` to go live on
   `:9000`."
2. Invoke `/eos-simplify` — quality pass over this session's changed code.
3. Invoke `/eos-session-wrapup` — docs-sync, leak checks, **devlog (wrapup
   Step 4 — never write your own)**, scoped commit + push, live-verify.

## Hard rules

- NEVER restart `:9000`/`:9001`; sandbox members only, via the lease API.
- Commit only your own files by explicit pathspec — never `git add -A`
  (`.claude/rules/environment.md` § parallel-session staging).
- Report HTML, steplog, and backlog live under `data/` (gitignored, never
  committed). **Scenario files ARE committed.**
- Feature gaps are **logged and proposed, never auto-built** in this session.
- `docs/DEFERRED-WORK.md` is propose-only from this skill.

## Cross-references

- `eos-new-usecase` — **authors** the scenarios this skill walks. Invoke it to
  fill a coverage hole; never hand-write a scenario here.
- `eos-ui-walk` — the walk mechanics this skill drives (Steps 0.5–3) and the
  report-only sibling.
- `scripts/check_vault_test_leak.py` — the Phase 7 sweep; only works because
  Phase 2 marks every vault write with `PLAYWRIGHT-TEST-`.
- `eos-bug-audit` — backend correctness sweep + the backlog loop-memory
  pattern this skill mirrors.
- `eos-fix-drain` — drains the fix-prompts this skill queues.
- `eos-app-gap-analysis` — owns the market-gap registry this skill appends to.
- `emptyos/sdk/fix_queue.py` — the fix-prompt contract (headings are load-bearing).
- `scripts/ui_walk_report.py` — steplog → HTML renderer (`missing` → GAP badge).
- `.claude/rules/agent-cli.md` — the CLI `--json` envelope the CLI lane tests.
- `.claude/rules/audits.md` — false-positive discipline.
- `.claude/rules/sandbox-driven-testing.md` / `.claude/rules/daemon-handling.md`
  — verify off `:9000`, never restart it.
