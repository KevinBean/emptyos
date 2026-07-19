# Codex task — `agent_fleet` Phase 1 backend

Build the backend half of `agent_fleet` per the plan at
`.claude/plans/agent-fleet.md`. **Read that plan first** — it is the spec, and
it was already revised against an external review (the `[R1]`–`[R4]`/`[Rc]`
tags mark corrections that are non-negotiable). This file is the delta: your
scope, the frozen seam, and the traps.

Another agent (Claude, in the main tree) is building the **hook script half in
parallel**. Ownership is file-level and strict — see § Ownership.

---

## Ownership — do not touch anything outside this list

**Yours (create/edit freely):**

```
apps/extension/dev/agent_fleet/**          # the whole new app
tests/test_sys_agent_fleet.py              # system tests
tests/test_unit_agent_fleet_reducer.py     # reducer/TTL/dedup unit tests
tests/fixtures/agent_fleet/*.json          # captured hook payload fixtures
apps/extension/dev/run-center/app.py       # ONE registration edit — see §5
emptyos/sdk/run_status.py                  # ONE vocabulary edit — see §5
```

**NOT yours (the other agent owns these — editing them will collide):**

```
~/.claude/settings.json      .codex/hooks.json      ~/.codex/hooks.json
scripts/agent_fleet_hook.*   ~/.claude/wezterm-status.ps1
```

You never install, enable, restart, or benchmark anything against the live
daemon on `:9000` — it is user-owned (`.claude/rules/daemon-handling.md`).
Verification is tests only; the other agent does live verify on a leased
sandbox.

---

## 1. The frozen seam — the hook envelope

This is the contract between the hook (theirs) and your endpoint (yours).
**It is frozen. Do not extend it, do not accept extra fields, do not "improve"
it.** If you believe it is wrong, say so in your summary and stop — do not
unilaterally change it.

`POST /agent_fleet/api/hook`, `Content-Type: application/json`, bearer auth
(private mode — the daemon middleware handles it; your route just needs to
work under it).

```jsonc
{
  "source":            "claude" | "codex",   // required
  "session_id":        "abc123",             // required, opaque string
  "event":             "SessionStart",       // required, see §2
  "ts":                "2026-07-17T09:41:02Z",// required, ISO-8601 UTC
  "cwd":               "D:/emptyos",         // optional, "" when unknown
  "turn_id":           "t7",                 // optional
  "agent_id":          "claude-sonnet-4",    // optional
  "notification_type": "permission_prompt"   // optional; only on Notification
}
```

**Never accept or store raw prompt text or tool payloads.** Unknown keys are
dropped at the boundary, not persisted. A malformed body returns
`{"ok": false, "error": "<short_code>"}` with a 200 — the hook must never
block a coding session on our validation.

**Ground truth (2026-07-17): the hook half is BUILT and WIRED, and real
payloads are captured.** `tests/fixtures/agent_fleet/claude-events.jsonl` holds
13 **real** Claude events (redacted content, anonymised sessions) — use these,
not synthetic ones; its README lists what they proved. The envelope key names
above are unchanged, so this contract still stands exactly as written. What the
capture corrected, on the hook side only:

- `turn_id` is sourced from Claude's **`prompt_id`** (there is no `turn_id`
  field). The envelope key stays `turn_id` — your side is unaffected.
- `notification_type` is a **native Claude field**, and its real values are
  **`permission_prompt`** and **`idle_prompt`**. Your reducer's rule
  ("`permission_prompt` → blocked, any other type → no state change") is
  confirmed correct against real data: `idle_prompt` arrives right after `Stop`,
  when the session is already `idle`, so "no change" is right. Do **not** treat
  `idle_prompt` as blocked, and never infer the type from message prose (a
  draft that did read "Claude is waiting for your input" as a permission gate).
- **[R3] is now empirically proven, not just documented**: a captured session
  runs `Notification → SubagentStop → UserPromptSubmit → Stop → Notification`,
  i.e. it keeps emitting **after** `Stop`. Your reducer must not end it there.
- **No `SessionEnd` was captured yet** (sessions were still open), so its firing
  is still unverified even for Claude. The TTL sweep is the only guaranteed
  terminator for **both** sources — treat it as load-bearing, not a fallback.
- `SubagentStop` events exist but are **not** wired to the fleet; ignore them.
- Fields available but deliberately **out** of the frozen envelope: `model`
  (e.g. `claude-fable-5`), `permission_mode`, `agent_type`, `effort`. Do not add
  them unilaterally — flag them and they'll be agreed as an extension.

The route must be **fast and side-effect-light**: validate, update the run
record, return. Anything slow (emits, notifications) goes through
`asyncio.create_task` — see §6.

---

## 2. The reducer — `Stop` is a TURN end, not a session end

This is the finding that killed the first draft of the plan. Claude's `Stop`
fires **once per turn**; only `SessionEnd` terminates. Implement exactly:

| `event` | new state |
|---|---|
| `SessionStart` | `idle` |
| `UserPromptSubmit` | `working` |
| `PermissionRequest` | `blocked` |
| `Notification` **with** `notification_type == "permission_prompt"` | `blocked` |
| `Notification` (any other type) | *no state change* |
| `Stop` | `idle` |
| `SessionEnd` | `ended` |
| — TTL exceeded (no event for N min, default 30) | `unknown`, then `ended` |

Codex emits `SessionStart`, `UserPromptSubmit`, `PermissionRequest`, `Stop`
and subagent hooks, but has **no documented `SessionEnd`** — so the TTL sweep
is the only thing that ends a Codex session. It is mandatory, not a nicety,
and it is also the safety net for a Claude session killed before its hook
fires.

Emit `agent_fleet:blocked` on **entry to** `blocked`, and `agent_fleet:ended`
on **entry to** `ended`. Edge-triggered, not level — a repeat event in the
same state emits nothing. **Never emit `agent_fleet:ended` on `Stop`.**

Out-of-order and duplicate events are expected (two hooks, no global clock):
drop an event whose `ts` is older than the record's last-seen `ts` for that
session, and make identical `(session_id, event, ts)` idempotent.

Reducer must be a **pure function** in its own module — `(state, event) ->
state` — so the unit tests hit it without a daemon.

---

## 3. Persistence — RunRegistry, decided, not up for redesign

Use `emptyos/sdk/run_registry.py` (read it — it is the existing run-folder +
`run.json` primitive, already used by dogfood-agent / staff / model-bench).
One run record per session, keyed `f"{source}:{session_id}"`. Per-session
write lock (`self.write_lock(key)`) around read-modify-write. TTL sweep on a
schedule.

Do **not** use syslog as live state (it is an event trail), and do **not** use
in-memory-only (the fleet must survive a daemon restart).

---

## 4. App shape

`apps/extension/dev/agent_fleet/` — id, directory name, and
`[provides.web] prefix` must **all** be the literal `agent_fleet`
(underscore, matching `cable_network` precedent).

Routes:
- `POST /api/hook` — the ingest above.
- `GET  /api/sessions` — the detail feed for the page.
- `GET  /api/runs/<id>` — detail (run-center `caps.detail = true` needs it).

Page `/agent_fleet/` — a session table (state chip, source, cwd, branch,
last-event age). Keep it minimal; Phase 2 adds the worktree manager. The hub
surface is **not** yours (see §5).

`list_all()` returns the normalized row shape from `sdk/run_status.py`'s
docstring: `id, harness, kind, title, target, status, phase, branch,
diff_stat, started, finished, scope`. For a fleet session: `harness =
"agent_fleet"`, `kind = "session"`, `target` = the session's cwd basename,
`scope` = the worktree path, `branch` from `git -C <cwd> rev-parse
--abbrev-ref HEAD` (cached, fail-soft to `""` — never let a git call block the
event loop; `asyncio.to_thread` it).

---

## 5. Two one-line integrations — and the trap in them

**Run Center registration.** Harness discovery is a **hardcoded tuple**, not a
manifest contribution. In `apps/extension/dev/run-center/app.py`:

```python
HARNESSES: tuple[str, ...] = ("fix-agent", "app-builder", "dogfood-agent", "agent", "agent_fleet")
HARNESS_CAPS["agent_fleet"] = {"detail": True, "stream": False, "doctor": False, "actions": []}
```

`actions: []` is deliberate and permanent — **agent_fleet observes; it never
acts.** Do not add gate actions.

**Do NOT add a hub panel.** Run Center already owns the priority-70 panel and
renders `needs-review` + `running` rows from every harness, so fleet sessions
surface there for free. A second fleet panel is an explicit review rejection
(`[R4]`).

**The trap:** `_PHASE_BY_STATUS` in `sdk/run_status.py` has no entry for
`blocked`, and run-center's defensive recompute maps an unknown status with no
`finished` timestamp to **`running`** — silently losing the one signal this
app exists to surface. Fix it at the source, following the file's own
established pattern (it already enumerates per-app native vocabularies):

```python
    "idle": "running",
    "working": "running",
    "blocked": "needs-review",   # a permission prompt IS a human gate
    "ended": "done",
    "unknown": "done",
```

…and update that file's "Native vocabularies covered" docstring block. Also
set `phase` explicitly in your rows — belt and braces.

`blocked → needs-review` is the whole point: it puts a stalled agent in the
same column as a fix-agent run awaiting merge, which is exactly right.

---

## 6. EmptyOS conventions that will bite you

These are the failure modes this codebase actually sees. Each has burned a
session before.

1. **A decorated route is not a bound route.** If you split into helper
   modules (`.claude/rules/multi-module-apps.md`), every `@web_route` function
   MUST get an explicit binding line in the class body
   (`api_hook = _hook.api_hook`). An unbound helper route is a **silent 404**,
   and unit tests calling the function directly pass anyway. If you stay in one
   `app.py` (fine at this size — the 1200L threshold is far off), this doesn't
   apply.
2. **App imports are relative.** `from . import hook as _hook` — never
   `from apps.extension.dev.agent_fleet import ...`. The loader registers apps
   under `eos_apps.<id>`; an absolute import kills app load.
3. **`call_app` is kwargs-only.** Never positional.
4. **New apps 404 until installed.** Add `store_state.mark_installed()` for
   `agent_fleet` — but do NOT restart the daemon; note it in your summary.
5. **No sync I/O on the event loop.** `git`, file reads in a handler → wrap in
   `asyncio.to_thread`. A blocking call in a route wedges the whole daemon
   (`.claude/rules/debugging.md` — this is the #1 recurring bug class here).
6. **Emits go through `asyncio.create_task`** when fired from inside a lock or
   an HTTP handler — `EventBus.emit()` awaits handlers serially in the calling
   task, so awaiting it in a route can 500 at ~30s and deadlock under a lock.
7. **`proactive_notify` is dark by default.** Wire the blocked>N-min alert
   through it, but the success criterion is "delivers **when the proactive gate
   is enabled**; otherwise suppressed and logged" — not "always fires".
8. Feature flag: ship behind `[apps.agent_fleet] feature.fleet.enabled`,
   default **false** (`project_feature_pipeline_flag_default_dark`). Flag off →
   the hook endpoint accepts and no-ops, `list_all()` returns `[]`, nothing
   renders. That is the regression contract.

---

## 7. Acceptance tests — write these, they are the deliverable

- **Reducer unit tests over multi-turn sequences.** The load-bearing one:
  `SessionStart → UserPromptSubmit → Stop → UserPromptSubmit → Stop` ends in
  `idle` with **zero** `agent_fleet:ended` emissions. If your reducer marks
  that session `ended`, it is wrong.
- `PermissionRequest` → `blocked` → `agent_fleet:blocked` emitted **once**
  (edge, not level).
- TTL: no events for > TTL → `unknown` → `ended`, exactly one
  `agent_fleet:ended`.
- Duplicate `(session_id, event, ts)` is idempotent; out-of-order (older `ts`)
  is dropped.
- Envelope validation: unknown keys dropped; missing required field →
  `{ok: false}`, never a 500, never a raise.
- `list_all()` rows validate against the run-status row shape, and a `blocked`
  session lands in `needs-review` **through run-center's `_gather()`** (test
  the integration, not just your own dict).
- Flag off → `list_all() == []` and the endpoint no-ops.

Fixtures: put **real captured payloads** in `tests/fixtures/agent_fleet/`.
The other agent will capture live Claude + Codex payloads and commit them
there — if they are not present when you start, write the fixtures from the
envelope spec in §1 and mark them `synthetic` in a header comment so they get
replaced.

---

## 8. Explicit non-goals — do not build these

- Any terminal hosting / PTY / ConPTY / xterm.js. Hard non-goal, forever.
- Any orchestration or "smart" behaviour. The fleet **observes**. Rooms /
  agent / staff act.
- Worktree management (that is Phase 2).
- A hub panel (§5).
- Gate actions on runs (§5).

---

## 9. Deliverable

A branch with the app + tests, and a summary naming: which conventions in §6
you touched, anything in §1 you think is wrong, and any test you could not
make pass. **Do not merge.** The other agent reviews by executing the
endpoint — not by reading the diff — because the §6 failure modes are all
invisible in a diff and ship green.
