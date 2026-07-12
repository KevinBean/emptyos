# fix-agent — operational contract

The architectural shape of the test-fix-verify loop lives in `.claude/rules/test-fix-verify-loop.md`. This doc is the **operational contract**: the guarantees `fix-agent` makes (and doesn't make) about the working tree, the runtime invariants the drain orchestrator depends on, and how each failure mode resolves.

Read this before driving a multi-fix drain, before touching `apps/fix-agent/`, or before debugging "why does main have a commit I don't recognise."

## Agent runner

`fix-agent` now dispatches through the shared `AgentRunner` contract. The
default runner remains `claude-cli`, so the drain path and existing operational
contract are unchanged.

For dark testing, `POST /fix-agent/api/run` accepts optional `runner` and
`runner_provider` fields. Example: `{"filename": "...md", "runner":
"eos-agent:openai"}`. Non-Claude runners must still produce a commit on the
checked-out fix branch; the existing merge, verify, and revert gates remain
authoritative.

## What worktree-per-fix actually guarantees

`fix-agent` keeps **one** worktree on disk at `.claude/worktrees/fix-agent/` (`apps/fix-agent/app.py:75-76`). The "per-fix" part is the **branch**, not the directory.

For each fix, `_run_one` resets the worktree (`app.py:758-768`):

```
git reset --hard HEAD
git clean -fd
git checkout -B <fix-branch> main
```

This guarantees:

- **Isolation across fixes** — every fix starts from `main`'s current tip on its own branch. A leftover edit from a crashed earlier run is wiped before claude-cli touches anything.
- **No cross-fix conflict on disk** — the worktree directory is reused, but its contents are reset between runs. Disk pressure is bounded.
- **Main's checkout is untouched while claude-cli edits** — the worktree is a separate checkout; the user's main editor and `:9000` daemon keep running against the primary repo checkout.

What it does **NOT** guarantee:

- **No orphan branches.** `git branch -D <branch>` is only called by `api_run_discard` (`app.py:521`). A successful merge leaves the branch in place; an interrupted drain leaves the in-flight branch behind. Run `git branch | grep '^  fix/'` to inventory.
- **Worktree cleanup on uninstall.** Removing `apps/fix-agent/` doesn't `git worktree remove` the directory. Detach manually if removing.
- **Concurrency safety.** Two simultaneous `api_run` calls fight over the same worktree. The drain serializes; ad-hoc UI use must too.

## How merges land on main

`api_run_merge` (`app.py:347-400`) enforces three gates before touching `main`:

1. **Status gate** (`app.py:358`): only `ready` runs can merge.
2. **py_compile gate** (`app.py:364-373`): every changed `*.py` is parsed with `python -m py_compile`. A SyntaxError in the diff would prevent the next daemon restart — the worst possible "blind merge" outcome. Merge aborts on any failure; status stays `ready`.
3. **Branch sanity** (`app.py:375-377`): refuses if the repo isn't currently on `main`.

Then: `git merge --ff-only <branch>` (`app.py:378`).

**Consequence for main's history:**

- `--ff-only` means main's tip moves to the branch's tip. **No merge commit is created** — the commit on main IS claude-cli's commit (typically one, occasionally more if the agent commits multiple times).
- `meta["merge_commit"] = HEAD` (`app.py:382-384`) records the new tip. This SHA is what `api_run_revert` targets.
- If the branch is behind main (e.g. another fix merged first), ff-only refuses: status stays `ready`, the error explains "branch likely needs rebase." The drain treats this as `stuck`.

## How reverts work — and what's NOT checked

`api_run_revert` (`app.py:477-507`) runs `git revert --no-edit <merge_commit>` on main. This **creates a new commit** that undoes the target's diff.

**State after a successful revert:**

```
main:  ... → A → F (fix) → R (revert of F) → ...
```

Both `F` and `R` stay in history. Nothing rewrites or deletes commits.

**On conflict** (`app.py:496-501`): `git revert --abort` restores the tree, error returned. This happens when a later fix touched the same lines. Operator must revert the overlapping fix first (or resolve by hand).

**What `api_run_revert` does NOT assert post-revert:**

- That `main` contains no true merge commits (it shouldn't — `--ff-only` is enforced — but no defensive check confirms it).
- That the revert commit's diff actually reverses the fix's diff (a corrupt or partial revert would silently succeed).
- That no orphan branch remains pointing at the reverted commit.
- That the daemon is restarted afterwards. The return value says "Restart the daemon to drop the bad fix," but nothing enforces it. Until restart, `:9000` runs the reverted code.

The drain skill (`/eos-fix-drain`) is where these post-conditions get checked — `api_run_revert` itself is intentionally minimal so manual reverts from the UI stay fast.

## Verify: how the sandbox stays in sync with main

`api_run_verify` (`app.py:402-475`) is the only path that restarts the `:9001` sandbox:

1. `_restart_sandbox_for_verify(meta)` (`app.py:451`) — calls the `dogfood-demo` plugin's `restart()`. The sandbox boots against the patched code.
2. `dogfood-agent.api_run(persona, scenario, verify_of=<last_run_id>, verify_friction={...})` (`app.py:454-462`) — re-runs the originating scenario.
3. `_poll_verify` (`app.py:93-149`) polls the dogfood run until `target_fixed` appears (line 119), max 25 min.

**Critical**: `:9000` (the main daemon running fix-agent itself) is **not** restarted. If a fix merges code that fix-agent itself imports (drain.py, app.py, helpers), `:9000` runs stale code until the user restarts it manually. The drain catches this via the **orchestrator-dirty halt** (see below).

## Failure modes — how each resolves

| Trigger | Status set | What's on main | What's left behind |
|---|---|---|---|
| claude-cli exits non-zero | `error` | unchanged | branch (no commits) |
| claude-cli runs >20 min | `timeout` | unchanged | branch |
| claude-cli edits nothing | `no-changes` | unchanged | branch (no commits) |
| py_compile fails | stays `ready` | unchanged | branch (with bad commit) |
| ff-merge refuses | stays `ready` | unchanged | branch (rebase needed) |
| verify polls past 25 min | `verify-timeout` | fix-commit landed | branch + commit (drain auto-reverts) |
| dogfood says `target_fixed: false` | `verify-failed` | fix-commit landed | branch + commit (drain auto-reverts) |
| Network/529 during claude-cli stream | `error` | unchanged | branch (partial state) |
| Drain `_drain_queue` raises | `crashed` (in drain history) | depends on iteration phase | in-flight branch + possibly a merged-but-unverified commit |

### 529 / Overloaded / network interrupt — three failure windows

The drain has three distinct windows where an interrupt has different consequences:

1. **During `api_run`** (claude-cli streaming). The exception propagates as `error`; the worktree branch may contain partial edits. `_run_one`'s reset on the next iteration wipes them. **Main is untouched.**
2. **Between `api_run_merge` and `api_run_verify`.** Rare (the calls are inline) but possible. The fix commit is on main; no verify has been attempted. The drain marks this iteration `stuck` and proceeds; the commit stays. **Manual cleanup: revert via `/fix-agent/` UI or `git revert <sha>`.**
3. **During `_wait_for_fix_agent_status` (verify poll).** The 25-min poll might exit with `{"error": "timeout"}`. The drain treats this identically to `verify-failed` and calls `api_run_revert` (`drain.py:404-414`). **This is the load-bearing safety** — an uncertain merge does not silently stay on main.

If the *drain itself* crashes (`drain.py:419-421`), the `finally` block (`drain.py:422-431`) clears `active`, records `result: "crashed"` in history, and emits `dogfood:queue_drained`. The currently-in-flight fix is whatever state it was left in — needs manual triage.

## Pre-flight invariants the drain depends on

Before launching `_drain_queue`, these must hold or the drain produces wrong-but-undetected outcomes:

1. **Repo on main, working tree clean.** `api_run_merge` checks current branch (`app.py:375-377`) but not for uncommitted changes. An uncommitted edit at drain start will be carried through reset cycles into every fix's worktree.
2. **No orphan fix branches from a prior crashed drain.** They're benign on disk but pollute `git branch` and risk confusion if a new fix's branch name collides.
3. **Both daemons reachable** (`:9000` for fix-agent, `:9001` for verify sandbox). The drain doesn't check; it crashes on first call.
4. **One verify-able fix already in the queue and proven to verify against HEAD.** A drain that runs many fixes when *none* of them can verify against current main (e.g. because the verify infrastructure itself is broken) will revert every merge and report `stuck: N` — wasted compute. The memory `feedback_drain_preflight_verify` codifies this lesson.
5. **`orchestrator_dirty` flag clear** (`data/apps/dogfood-agent/fix-drain.json`). A previous drain that touched orchestrator code leaves this set; the next drain halts on the first iteration.

## Status transitions (canonical)

```
queued → running → ready ────────────────────────────┐
                 ↘ no-changes                         │
                 ↘ error                              │
                 ↘ timeout                            │
                                                      ↓
                              ready ──→ merged (ff + py_compile)
                                          ↓
                                       verifying (sandbox restart + dogfood run)
                                          ↓
                                  ┌── verified           (drain: applied++)
                                  ├── verify-failed      (drain: revert → reverted)
                                  └── verify-timeout     (drain: revert → reverted)

User actions:
  ready    → discarded   (via api_run_discard; deletes branch)
  reverted → (terminal; main has fix-commit + revert-commit)
```

## What this contract intentionally omits

- **No retry of failed verifies.** A `verify-failed` run reverts and stops. The persona/scenario is re-queued only if the next dogfood cron run re-surfaces the same friction.
- **No automatic restart of `:9000` after merge.** That's a `restart.bat` the user runs. The orchestrator-dirty halt catches the case where stale `:9000` code would corrupt the next drain iteration.
- **No worktree garbage collection.** Disk usage is bounded (one worktree, reused) so GC isn't needed for fix-agent's own use. If you remove the app, run `git worktree remove .claude/worktrees/fix-agent` manually.
- **No multi-machine coordination.** The whole loop assumes one main daemon, one sandbox, one fix-agent. Multi-tenant drain is out of scope.

## Cross-references

- `.claude/rules/test-fix-verify-loop.md` — architectural shape: four roles, sandbox plugin contract, when to extract to SDK.
- `.claude/rules/daemon-handling.md` — why `:9000` is hands-off; why the sandbox approach exists.
- `.claude/skills/eos-fix-drain/SKILL.md` — operational runner that enforces the pre-flight invariants above and verifies the post-revert state.
- `apps/fix-agent/app.py` — implementation; line refs in this doc are stable as of 2026-05-16.
- `apps/dogfood-agent/drain.py` — `_drain_queue` loop (lines 266-436).
