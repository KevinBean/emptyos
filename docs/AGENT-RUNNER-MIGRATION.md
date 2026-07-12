# Agent Runner Migration

EmptyOS should depend on an agent-runner contract, not on Claude Code. Claude
Code remains the default implementation because it is currently the strongest
and most battle-tested path, but it must not be the architecture.

## Current State

EmptyOS already has a first-party harness in `/agent` and `eos chat`: a
provider-agnostic tool loop, permission manager, and SDK tool registry. That
path works with `anthropic_sdk`, `openai_compat`, and local OpenAI-compatible
servers such as Ollama.

The production engineering loops are still more Claude Code-specific:

- `app-builder` spawns `claude-cli` in a worktree to scaffold apps and dev
  changes.
- `fix-agent` spawns `claude-cli` in a worktree to apply dogfood fixes.
- `dogfood-agent` spawns `claude-cli` as a persona and parses its stream-json
  transcript.

Those loops rely on Claude Code's harness behavior, not just the Claude model.
The migration goal is to isolate that dependency behind an interface.

## Runner Contract

The shared contract lives in `emptyos/sdk/agent_runner.py`.

- `AgentRunSpec` describes one run: prompt, system prompt, cwd, allowed tools,
  wall/idle timeouts, stream paths, liveness callbacks (`on_stdout_line` /
  `on_stderr_line` / `on_tick` + `tick_interval_s`, which dogfood-agent needs),
  mode, and metadata. `None` means "runner default"; `mode`/`metadata` are
  advisory and a runner may ignore them.
- `AgentRunResult` normalizes returncode, timeout, duration, error, binary, and
  raw backend fields.
- `AgentRunner` is the protocol each backend implements.
- `ClaudeCliRunner` wraps the existing `agent-runtime.claude_cli_run()` path.
- `EosAgentRunner` uses EmptyOS's first-party `AgentSession + run_turn` harness
  with tool-capable providers only.

Unsupported runner ids fail explicitly so config mistakes do not silently fall
back to the default.

## Migration Order

1. **App-builder pilot**
   - Default remains `claude-cli`.
   - `app-builder` calls `resolve_app_builder_runner()` and records `runner_id`
     on each run.
   - Existing gates remain authoritative: worktree reset, scope guard,
     `py_compile`, merge gate, Store install, endpoint verify.

   Opt-in config:

   ```toml
   [apps.app-builder]
   runner = "eos-agent"              # or "eos-agent:openai"
   runner_provider = "openai"        # optional explicit tool-capable provider
   ```

2. **EosAgentRunner**
   - Implemented behind `runner = "eos-agent"` / `runner = "eos-agent:<provider>"`.
   - Support `anthropic_sdk`, `openai_compat`, and Ollama tool-capable models.
   - Keep it dark until app-builder bench data is strong enough.
   - **Worktree cwd is supported via the shared `WorktreeApp` facade**
     (`emptyos/sdk/subagent_context.py` — the same seam SubAgentTool's
     `isolate="worktree"` uses). The EOS tool registry resolves relative
     paths against `app.repo_root` (`agent_tools/base.py::resolve_path`) —
     a single choke-point — so `EosAgentRunner.run()` hands the tool loop a
     run-scoped facade whose `repo_root` is `AgentRunSpec.cwd`. The shared
     app instance is never
     mutated; concurrent `/agent/` sessions are unaffected. A nonexistent
     `cwd` is refused. Scope honesty: the proxy re-roots *relative* path
     resolution; absolute paths in tool calls can still escape the worktree,
     exactly as they can under claude-cli — the worktree diff + merge gates
     own that risk. The run result records the workspace in `raw.workspace`.
   - **Known gap — stream.jsonl schema differs.** The claude-cli path writes
     Claude stream-json; the EOS path writes `agent:*` event lines via
     `_RunnerEventRecorder`. Consumers of `stream.jsonl` (run drawer, transcript
     parsers) must not read an EOS run's different schema as "agent did nothing".

3. **Fix-agent**
   - Implemented behind the same `AgentRunner` contract.
   - Default remains `claude-cli`.
   - `/fix-agent/api/run` accepts optional `runner` and `runner_provider`
     fields for dark per-run overrides. App-builder's `/api/run` and
     `/api/dev_change` take the same fields.
   - Run records store `runner_id`, `runner_config`, and `runner_provider` —
     the pair is resolved by the shared `effective_runner_config()` at queue
     time and frozen on the record, so a config edit between queue and run
     can't shift a queued run (bench attribution). Both harnesses use the
     shared `resolve_app_runner()` bound as a method; app-side `runner.py`
     files are thin re-exports.
   - Keep drain default on `claude-cli` until verify/revert behavior is stable
     under non-Claude runners.

4. **Dogfood-agent**
   - Migrate last. It needs transcript normalization, persona result parsing,
     and WebFetch/Bash parity before it can safely leave Claude Code's native
     stream-json path.

## Bench Gates

Runner promotion is evidence-based:

- Manual opt-in: any runner that completes the lifecycle without unsafe writes.
- App-builder default: at least 80% of Claude baseline on scaffold/dev-change
  benches and no unsafe scope failures.
- Fix-agent drain default: close to Claude baseline, with stable verify-failed
  and revert behavior.
- Dogfood default: only after transcript extraction and scenario-verdict
  reliability match the Claude path.

Bench groups should cover:

- Scaffold a small app.
- Scaffold an app with backend, UI, and test.
- Modify an existing app across backend + UI + test.
- Fix a known dogfood issue.
- Multi-file refactor.
- False-premise no-op.
- Long-context rule following.

## Bench Log

**2026-06-12 — Round 1: scaffold-a-small-app (`bench-quotes-*` specs, identical
quote-box CRUD, data-json).** Run on `:9000` via the per-run override.

| | claude-cli | eos-agent + openai-mini |
|---|---|---|
| Status | `ready` | `no-changes` |
| Wall-clock | 218 s | 168 s |
| Output | 4 files, +200 lines — all 4 acceptance endpoints, emit, test file, INTENT.md | 0 files in worktree |
| Scope safety | clean | **escaped** — wrote manifest/app.py/INTENT.md to `D:\mnt\d\emptyos\` (drive root) |

Root causes found and fixed the same day:

1. `agent_tools/base.py::resolve_path` silently rebased rooted-driveless paths
   (`/mnt/d/...`) to the drive root on Windows — now raises, which the agent
   loop feeds back as a self-correcting tool error. This bug also affected
   `/agent/` generally, not just the runner.
2. The in-process loop had no cwd grounding — claude-cli learns its root from
   the real process cwd; `EosAgentRunner` now appends a `[workspace]` block to
   the system prompt stating the root + relative-path rule.

Verdict: eos-agent+mini is 0% of baseline this round — but the round's purpose
was harness validation, and it surfaced two real harness gaps. Re-run after the
fixes before drawing model-quality conclusions; also consider a stronger
provider (`openai`) for the quality axis, since gpt-4o-mini is `standard`-tier
and scaffolding is a `strong`-shaped task (`.claude/rules/model-ability.md`).

**2026-06-13 — Round 2: leg-2 re-run after the round-1 fixes (same spec,
eos-agent + openai-mini).** Status `no-changes` again — but this time that
status was a **misreport**: the agent produced a full scaffold *inside the
worktree* (manifest, app.py, INTENT.md, test file), ran `py_compile`
successfully, and used only relative paths — the round-1 grounding +
`resolve_path` fixes held. 62 s wall-clock. What failed was the last step:
every `git add && git commit` died with exit 128, the loop-guard fired after
3 attempts, and the run ended uncommitted — which the capture step counted as
"no changes".

Root causes found and fixed the same day:

3. `agent_tools/bash.py::_find_shell` resolved `bash` via PATH, which in the
   daemon's environment is the **WSL launcher** (`System32\bash.exe`) — WSL
   git cannot read a Windows worktree's `gitdir:` file. Metachar commands
   (`&&`, `;`) ran under WSL and failed; no-metachar commands took the direct
   exec path with Windows git and worked, which made the failures look
   random. Now derives Git Bash from `git.exe`'s install dir, immune to PATH
   order. (Fixes `/agent/` Bash on Windows generally.)
4. `_run_one`'s capture required the *agent* to have committed. Now, if the
   run leaves uncommitted work, app-builder stages + commits it daemon-side
   (`auto_captured: true` on the record) — same shape as SubAgentTool's
   `_capture_worktree_result`. The harness owns capture, not the agent's git
   competence. (fix-agent's capture has the same exposure — migrate the same
   fallback when touched.)

Verdict: the EOS harness is one restart away from a fair quality round —
round 2 proved grounding, path discipline, worktree isolation, and
`py_compile` all work under a `standard`-tier model; only environment-shape
bugs (shell, capture) blocked a `ready`.

**2026-06-13 — Round 3: full three-way scorecard (after the round-2 shell +
capture fixes).** All three legs reached `ready`; every leg self-committed
with its own git (the capture fallback was never needed once Git Bash was
selected).

| | claude-cli | eos-agent + openai-mini | eos-agent + openai |
|---|---|---|---|
| Status | `ready` | `ready` | `ready` |
| Wall-clock | 218 s | **45.5 s** | **37.4 s** |
| Acceptance routes (4) | 4/4 | 4/4 | 4/4 |
| Emit + empty-text guard | ✓ | ✓ | ✓ |
| Manifest parses | ✓ | ✓ | ✓ |
| `py_compile` | ✓ | ✓ | ✓ |
| Tests written | 7 | 3 | 4 — **but filename has hyphens** (`test_sys_bench-quotes-eos2.py`), so pytest can't import it |
| Lines (app.py) | 69 | 69 | 84 |

Reading: on the *smallest* bench task, the EOS harness now clears the
"completes the lifecycle without unsafe writes" gate, and even mini hits full
acceptance coverage at ~5× less wall-clock than claude-cli. Differences show
up in the periphery — test depth (7 vs 3/4) and convention fidelity (the
hyphenated test filename). Expect the gap to widen on the harder bench groups
(multi-file dev-change, false-premise no-op, long-context rule following) —
run those before any promotion decision. Static endpoint/compile checks are
necessary-not-sufficient: none of this verifies runtime behaviour (that is
the merge → Store install → endpoint-verify step's job).

## Near-Term Rule

New engineering loops should call an `AgentRunner`, not `claude_cli_run()`
directly. Claude Code is the default runner, not the substrate.
