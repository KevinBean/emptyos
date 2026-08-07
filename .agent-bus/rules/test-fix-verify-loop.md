# Test-Fix-Verify Loop — EmptyOS self-improvement infrastructure

EmptyOS uses its own apps to test, fix, and verify EmptyOS. The loop is not a
single app — it's four roles composed via the event bus + `call_app`. Any new
app that wants to participate (as a friction *source* or as a friction *consumer*)
plugs into the contract documented here.

**Reference implementations today** (four friction sources now write to the shared queue):
- `apps/extension/dev/dogfood-agent/` — friction *source* (persona runs) + drain orchestrator + active scenario-rerun verifier
- `apps/extension/dev/trace-miner/` — friction *source* (syslog mining) + **passive** verifier. The LangSmith-Engine analogue: sweeps `data/syslog.db` on a cron, groups recurring errors into normalized signatures, classifies code-bug vs external-service noise, scores by frequency × recency, and emits fix-prompts into the dogfood queue dir tagged `source: trace-miner`.
- `apps/extension/dev/fix-agent/` — fix-driver (worktree-per-fix, branch-per-issue, merge gate). Its verify handler branches on `source: trace-miner` and delegates to `trace-miner api_verify` instead of a dogfood scenario re-run.
- `apps/extension/dev/kb-butler/` — a fourth trace-miner-shaped source (KB-maintenance findings → fix-prompts) + `scripts/ui_walk_promote.py` (promotes hand-walk friction). Both write the same fix-prompt format via the shared queue.
- `plugins/dogfood-demo/` — sandbox lifecycle (the `:9001` daemon under test)
- **Observation:** `apps/extension/dev/cockpit/` tails fix-agent/dogfood `stream.jsonl` run streams live (behind `feature.autorun-view.enabled`) — the read-only window into a running loop, not a participant.

**Two verify shapes now exist.** Dogfood's is *active* — re-run the scenario; the persona shouldn't re-report the friction. Trace-miner's is *passive* — after merge, watch syslog for recurrence of the error signature: absence over the window = provisionally fixed, recurrence = regressed + reopened on the next sweep. Passive verify exists because syslog errors have no re-runnable scenario (exactly the "When NOT to plug in" caveat below). A friction source with a re-runnable path uses the active shape; one without owns a passive verify like trace-miner.

**SDK extraction is DONE.** `emptyos/sdk/fix_queue.py` (`FixPromptQueue` + `FRICTION_HEADING`) + `BaseApp.surface_friction(...)` shipped once the second consumer landed, and now has four+ consumers (dogfood, trace-miner, fix-agent/verify, kb-butler, `scripts/ui_walk_promote.py`) + `devboard` reading the done-ledger. All friction sources write the one format through it; build fix-prompts via `fix_queue.py` helpers, never by hand. (Historical note: `dogfood-agent/friction.py` predates the module and migrates on next touch.)

## The four roles

| Role | Responsibility | Today |
|---|---|---|
| **Friction source** | Surface a failure as a structured fix-prompt with a stable key. Persist to a queue so the loop can find it later. | `dogfood-agent` persona runs |
| **Fix-driver** | Read the fix-prompt, spawn an LLM with editing tools in an isolated worktree, run the py_compile gate, ff-merge on success. | `apps/extension/dev/fix-agent/` |
| **Sandbox** | Provide an isolated runtime that can be killed + restarted without touching the user-owned main daemon. Owned by a plugin that holds the subprocess handle. | `plugins/dogfood-demo/` |
| **Verifier** | After merge + sandbox restart, re-exercise the originating scenario against the patched sandbox. Report verified / verify-failed. | `dogfood-agent` verify-runs |

The same app can play multiple roles (today's source + verifier are both
`dogfood-agent`), but the *roles* are what compose, not the apps.

## The contract

### Friction source emits a fix-prompt

A friction source persists a markdown file with a deterministic filename to
its app's queue directory. Default convention: `data/apps/<app>/fix-prompts/<key>.md`.

The body should contain enough context that the fix-driver can drive the fix
without re-querying the source:

```markdown
# Friction: <one-line summary>

## What surfaced this
- scenario: <id>             # for the verifier to re-run
- persona: <id>              # for the verifier
- last_run_id: <id>          # verify anchor
- friction_kind: bug | confusing | missing
- friction_text: <verbatim>

## What the persona reported
> <quoted log lines from the source run>

## Suggested investigation
1. ...
2. ...
```

The `## What surfaced this` block is the **load-bearing contract** — the fix-agent
parses `persona / scenario / last_run_id / friction_kind / friction_text` via
`_parse_prompt_meta`. Without them, verify can't anchor. New friction sources
must emit this block. (On disk the locked quote heading is `## What the persona
reported` — `emptyos/sdk/fix_queue.py::FRICTION_HEADING`; build prompts via that
module's helpers rather than restating the format.)

**Optional trace-identity frontmatter** (`usecase_id / milestone_id / step_id /
walk_id / evidence`) rides the same contract for manual-UI-walk-promoted
prompts (`source: ui-walk`, written by `scripts/ui_walk_promote.py`).
`_parse_prompt_meta` plucks them into `verify_context`, so runs, verify runs,
and loop receipts inherit the originating use case verbatim. ui-walk prompts
verify by **human re-walk attestation** (`POST /fix-agent/api/runs/{id}/attest`)
— a third verify shape next to active-scenario and passive-syslog — and the
autonomous drain skips them at selection. Every close (any source) appends a
disposition row to `fix-prompts/done/_ledger.jsonl`. Full contract:
`.claude/rules/loop-traceability.md`.

### Fix-driver lifecycle (today: `apps/extension/dev/fix-agent/api_run`)

States transition: `queued → running → ready → merged → verifying → verified | verify-failed | verify-timeout`.

| Endpoint | Effect |
|---|---|
| `POST /fix-agent/api/run` body `{filename}` | Spawn claude-cli in a fresh worktree on a new branch. Status moves `queued → running → ready` (or `no-changes` / `error`). |
| `POST /fix-agent/api/runs/{id}/merge` | py_compile every changed `*.py`; ff-merge to main. Status → `merged`. |
| `POST /fix-agent/api/runs/{id}/verify` | **MUST** restart the sandbox before kicking off the verify scenario (so the sandbox loads patched code). Status → `verifying`. |
| `POST /fix-agent/api/runs/{id}/revert` | Revert the merge commit. Only valid on `verify-failed`. |
| `POST /fix-agent/api/runs/{id}/discard` | Discard the worktree on `ready` without merging. |

### Sandbox plugin must expose `stop()` + `restart()`

Any plugin acting as a sandbox in this loop must expose two async methods:

```python
async def stop(self) -> dict: ...      # returns {ok, reason?, already_stopped?}
async def restart(self) -> dict: ...   # returns {ok, stage, host} or {ok: False, reason}
```

**Critical:** these methods may only kill the subprocess the plugin spawned
(its own `self._proc` handle). Killing an arbitrary python.exe violates
`.claude/rules/daemon-handling.md` and corrupts SQLite WAL. The plugin
must refuse with `{ok: False, reason: "running_but_unowned"}` if the
sandbox was started by something other than itself.

### Verifier contract

Whatever produced the original friction must accept a verify request that
re-runs the same scenario with three pieces of context: `verify_of` (anchor
run id), `verify_friction` (the friction record), and the persona/scenario
to re-run. On completion, the verifier sets `target_fixed = true|false` on
its run record. The fix-driver polls until that flag appears.

`apps/extension/dev/dogfood-agent/api_run` is the reference verifier surface today.

## The drain orchestrator

Optional — drives the four roles in a loop without per-step human approval.
Today: `apps/extension/dev/dogfood-agent/_drain_queue`. The orchestrator does NOT spawn
claude-cli; it composes the four roles' existing endpoints:

```
loop while pending in queue and not stop-signaled:
    queue_res = call_app("fix-agent", "api_run", filename)
    poll until status in {ready, no-changes, error, timeout}
    if ready:
        call_app("fix-agent", "api_run_merge", run_id)   # py_compile gate inside
        call_app("fix-agent", "api_run_verify", run_id)  # sandbox restart inside
        poll until status in {verified, verify-failed, verify-timeout}
        if verify-failed: call_app("fix-agent", "api_run_revert", run_id)
emit "<app>:queue_drained" with applied / stuck counts
```

Drain is gated behind opt-in config (today: `[apps.dogfood-agent] fix_agent_enabled = false` default)
and a per-loop budget (today: `fix_drain_max`). Without the gate, the
mechanism stays per-issue manual.

## When to plug in as a friction source

A new friction source earns its place if all of:

1. **It surfaces failures the user couldn't catch by reading the diff** — i.e.
   runtime / interaction failures, not static lint.
2. **Each failure has a stable identity** so the same friction across runs
   can be deduplicated and verified.
3. **There's a scenario shape that re-exercises the path** — verify needs
   *something* to re-run, otherwise "is it fixed?" can't be answered.

If those are true, you mostly need to: emit a fix-prompt with the required
contract block, write to a queue dir, expose a verify endpoint that accepts
`verify_of` + `verify_friction`. The fix-driver + sandbox + drain orchestrator
work unchanged.

If they aren't true, you probably don't want a fix loop — you want a regular
test or a regular alert. Don't force the shape.

## The SDK extraction (shipped)

The queue-writing contract is now `emptyos/sdk/fix_queue.py` — extracted once
the second friction source landed (CLAUDE.md rule 9), and today carrying
four+ consumers:

- `FixPromptQueue` — `enqueue()`, `pending()`, `move_to_done()`, `move_to_stuck()`,
  `read_ledger()`, plus `FRICTION_HEADING` (the locked `## What the persona
  reported` quote heading) and the fix-prompt format helpers.
- `BaseApp.surface_friction(kind, text, scenario, persona, last_run_id)` — the
  convenience writer every source uses.

Consumers: `dogfood-agent` (persona runs), `trace-miner` (syslog mining),
`fix-agent/verify.py` (done-ledger), `kb-butler` (KB-maintenance findings),
`scripts/ui_walk_promote.py` (hand-walk promotion), `devboard` (reads the
done-ledger for the fix-loop lane). **Never restate the fix-prompt format —
build prompts via `fix_queue.py` helpers.** The one un-migrated site is
`dogfood-agent/friction.py`, which predates the module and migrates on next
touch.

New friction source? Emit through `surface_friction`/`FixPromptQueue`, include
the `## What surfaced this` block (§ The contract), and own a verify shape
(active re-run, passive-recurrence, or human attestation).

Until then, keep the contract documented here and let it sit in `dogfood-agent`.

## Safety invariants

1. **Main daemon is never restarted by the loop.** Only the sandbox is.
   `.claude/rules/daemon-handling.md` applies — Claude / the loop never run
   `taskkill`, `restart.bat`, `python -m emptyos start`, or touch `data/*.db`.
2. **Fix-driver runs in a worktree, never the working tree.** Per-fix branch
   isolation means a verify-failed fix doesn't pollute the next iteration's
   diff. Auto-revert keeps `main` clean even if the user looks away.
3. **py_compile gate before merge.** A SyntaxError in the diff would make the
   sandbox fail to restart — the worst possible failure mode of "blind merge."

   **Regression gate (dark: `[apps.fix-agent] feature.regression-gate.enabled`).**
   py_compile proves the diff *parses*; it is not evidence the bug is fixed, and
   neither is the verifier — dogfood persona self-recurrence on an *unchanged*
   system measured **2%** (2026-08-06), so 90% of unfixed friction read as
   "verified cleared" before `_diff_friction`'s fail-open was closed. With the
   flag on, a fix must carry a **daemon-free** test (`tests/test_unit_*` /
   `tests/test_sdk_*`) that **passes on the branch and fails at the merge base**
   — checked in a throwaway worktree at base with only the test file copied in.
   Both sides are required: a test that passes at base pins nothing. Daemon-backed
   suites (`test_sys_*`, `test_dogfood*`, `test_journeys`, `test_ui*`) cannot gate,
   because they hit `:9000` and would test main-branch source rather than the
   worktree — which is why the fix prompt bans pytest wholesale when the flag is
   off. `apps/extension/dev/fix-agent/regression.py`. This is what promotes
   `"regression-test"` in `fix_queue.LEARNING_OUTCOMES` from an optional
   close-time label to a gate.
4. **Drain is opt-in, capped, and stoppable.** A runaway loop can't churn the
   repo overnight; the operator can clear the active flag to halt cleanly.
5. **WebFetch is excluded from fix-driver tools.** External calls would land
   cache files in the working tree and pollute the diff review.

## Anti-patterns

- **Don't add a friction source that emits unstructured prose.** Without the
  `## What surfaced this` block, the fix-driver can't anchor a verify run.
- **Don't spawn claude-cli from a friction source.** The fix-driver is the
  one role allowed to do that, and it does so in a worktree. A friction
  source that bypasses the fix-driver loses worktree isolation, branch
  history, and the py_compile gate.
- **Don't have the friction source restart the sandbox.** That's the sandbox
  plugin's job, triggered by the fix-driver's verify handler.
- **Don't let the drain orchestrator skip the merge gate.** "Just write the
  files and verify" sounds simpler but loses the py_compile pin — and a
  SyntaxError merged means the sandbox can't restart, which means the next
  iteration's verify can't run, which means the loop locks up.
