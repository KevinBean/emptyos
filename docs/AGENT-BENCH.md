# Agent Bench — findings and methodology

Tool-use benchmark for the EmptyOS agent. Lives inside `apps/model-bench/` as a parallel surface to the text-bench that already existed. Scores multi-turn tool-use loops with **deterministic verifiers**, not vibes.

For how to build, run, and extend it, see code at `apps/model-bench/agent_bench.py` and `apps/model-bench/agent_scenarios.py`. The UI is at `/model-bench/` → Agent tab.

## Four subjects

| Subject | How it runs | Tool registry used |
|---|---|---|
| `claude-external` | `claude -p <task>` subprocess with `--cwd <scratch>` | Claude Code's built-in tools (its own Read, Edit, Bash...) |
| `eos+claude` | AgentSession, provider=`claude-cli` → native-agentic loop | Claude Code's built-in tools — **not** our 8-tool registry |
| `eos+openai` | AgentSession, provider=`openai_compat` → `run_turn` | Our registry: Read, Grep, Glob, Bash, Write, Edit, DeleteFunction, CallApp |
| `eos+ollama` | AgentSession, provider=`openai_compat` (localhost:11434) → `run_turn` | Same as eos+openai |

**Load-bearing subtlety:** `eos+claude` routes through `NativelyAgenticProvider`, which means claude-cli runs its own loop with its own tools. The "EmptyOS tool advantage" (specifically `CallApp`) applies only to `eos+openai` and `eos+ollama`. To measure claude *with* our tools, a separate subject using `AnthropicSDKProvider` would be needed.

## Scenarios (19)

| ID | Floor | Tools exercised | What it tests |
|---|---|---|---|
| `write-new-util` | 1 | Write | Create a new file matching a spec. Deterministic verifier imports and tests the function. |
| `add-temperature` | 2 | Read, Edit | Classify a `self.think()` call's task, pick temperature per CLAUDE.md rule 12, Edit it in. Diagnoses "code-review prior" drift. |
| `find-missing-tests` | 3 | Glob, Read, Write | Diff sdk/ vs tests/, write gaps manifest. Tests path handling + collection diff. |
| `explain-structure` | 1 | Glob, Read | Read-only — list subdirs with purposes. Fixture integrity is the verifier; transcript holds the prose. |
| `call-app-discovery` | 4 | CallApp, Write | **EmptyOS-moat diagnostic** — list apps via CallApp, count methods per app. Subjects without CallApp fail deterministically. |
| `grep-replace` | 4 | Grep, Edit | Find a literal pattern across files and swap it. Fixture includes a decoy that must not be touched. |
| `multi-file-refactor` | 4 | Grep, Read, Edit | Rename symbol across def+imports+calls. Decoy with similar name (`old_function`, `old_funcs`) checks word-boundary discipline. |
| `debug-and-fix` | 4 | Bash, Read, Edit | Run failing tests → diagnose → Edit → re-run. First scenario exercising the Bash-verify loop. |
| `long-context-needle` | 2 | Grep, Write | Find one `NEEDLE(bench):` marker in a 40-file tree. Tests Grep-first discipline vs brute-force Read. |
| `false-premise` | 1 | Read | Task claims a bug in `multiply()`; no bug exists. Correct answer: no edit. Tests evidence-over-instruction. |
| `error-recovery` | 4 | Bash, Read, Edit | Python traceback (typo import). Read the error, find the typo, fix, verify. |
| `progressive-dependency` | 3 | Write, Bash | Create calc.py with `add()`, then use_calc.py that imports it, then Bash to verify the chain runs. |
| `ambiguity-clarify` | 1 | Read | Task names `_validate_input`, a function that doesn't exist. Correct answer: report / create stub / don't guess at existing functions. |
| `anti-goal` | 3 | Read, Edit | Replace all `print(...)` with `log.info(...)` EXCEPT inside `hello()`. Tests honoring an explicit negative constraint. |
| `delete-with-callers` | 4 | Read, Edit | Remove a function definition AND all caller call sites; replace each with `pass`. Decoy `to_delete_v2` shares prefix and must survive. Tests deletion discipline + word-boundary precision. |
| `cross-format-spec` | 3 | Read, Write | Spec in `README.md`, defaults in `config.toml`, implementation in `impl.py`. First non-Python-only fixture. |
| `read-large-file` | 3 | Grep, Read, Edit | 1500+ line file with 60 small functions. Edit one buried function. Forces Grep-locate + offset/limit Read instead of unbounded reads. |
| `canvas-app` | 3 | Write, Edit | Build an infinite-canvas app from scratch (manifest + backend + UI + tests). Open-ended app creation; verifier scores structural completeness against the EmptyOS app shape. |
| `gesture-control-app` | 3 | Write, Edit | Build a gesture-control demo app from scratch. Same shape as `canvas-app`; the prompt mirrors the "The Harness is the Product" article. |

## Scope spread (band map)

Per the model-bench evolution plan (2026-05-23), each scenario is scored on three independent dimensions and assigned a band — the **maximum** across the three. The rubric surfaces a gap that ProgramBench-style "reliability degrades with scope" framing is hard to apply here: **12 of 19 scenarios sit in a narrow small band**.

### Rubric

| Dimension | Trivial | Small | Medium | Large |
|---|---|---|---|---|
| Files the model substantively interacts with (read + write + modify) | ≤1 | 2–3 | 4–6 | 7+ |
| LoC the model is expected to write or modify | ≤3 | 4–15 | 16–60 | 61+ |
| `expected_tool_floor` | ≤2 | 3–5 | 6–10 | 11+ |

**Band rule**: max across the three. A 200-LoC single-file generator is `large` because of LoC; a 2-LoC edit across 8 files would be `large` because of file count.

**Not counted**: fixture scale (file size, directory breadth) the model navigates but doesn't substantively interact with. That's captured by Axis 5 (Scale coverage) below. Two scenarios — `long-context-needle` (40-file fixture, 1-line write) and `read-large-file` (1500-line file, ~2-LoC edit) — deliberately decouple the two axes to test navigation discipline. They band low here even though their fixture surface is large; that's the rubric working, not the rubric missing.

**Why no automated band calculation**: the per-scenario file count needs human judgement (`find-missing-tests` setup creates 6 files but the model substantively touches only the writeable ones; counting all 6 over-estimates). Manual scoring once, revisit when scenarios are added/removed.

### Per-scenario bands

| Scenario | Files | LoC | Floor | Band |
|---|---:|---:|---:|---|
| `write-new-util` | 1 | ~8 | 1 | Small |
| `add-temperature` | 1 | ~12 | 2 | Small |
| `find-missing-tests` | ~7 | ~40 | 3 | **Medium** |
| `explain-structure` | ~4 | 0 | 1 | Small |
| `call-app-discovery` | 1 | ~10 | 4 | Small |
| `grep-replace` | 3 | ~4 | 4 | Small |
| `multi-file-refactor` | 3 | ~6 | 4 | Small |
| `debug-and-fix` | 2 | ~2 | 4 | Small |
| `long-context-needle` | 1 | ~1 | 2 | Trivial |
| `false-premise` | 1 | 0 | 1 | Trivial |
| `error-recovery` | 1 | ~1 | 4 | Small |
| `progressive-dependency` | 2 | ~5 | 3 | Small |
| `ambiguity-clarify` | 1 | ~2 | 1 | Trivial |
| `anti-goal` | 1 | ~12 | 3 | Small |
| `delete-with-callers` | 2 | ~4 | 4 | Small |
| `cross-format-spec` | 3 | ~20 | 3 | **Medium** |
| `read-large-file` | 1 | ~2 | 3 | Small |
| `canvas-app` | ~5–8 | ~150–200 | 3 | **Large** |
| `gesture-control-app` | ~5–8 | ~150–200 | 3 | **Large** |

### Distribution

| Band | Count | Share | Scenarios |
|---|---:|---:|---|
| Trivial | 3 | 16% | `long-context-needle`, `false-premise`, `ambiguity-clarify` |
| Small | 12 | 63% | `write-new-util`, `add-temperature`, `explain-structure`, `call-app-discovery`, `grep-replace`, `multi-file-refactor`, `debug-and-fix`, `error-recovery`, `progressive-dependency`, `anti-goal`, `delete-with-callers`, `read-large-file` |
| Medium | 2 | 11% | `find-missing-tests`, `cross-format-spec` |
| Large | 2 | 11% | `canvas-app`, `gesture-control-app` |

### The gap

**The bench has an empty "medium-large" band.** Between the 60-LoC `cross-format-spec` ceiling and the 150–200-LoC app generators is a ~90-LoC zone where most real EmptyOS coding work actually lives: touching one app's `app.py` + `pages/index.html` + a test file at roughly 80 LoC across 3 files. We can't currently see how reliability changes across that range.

**The trivial band is thin (3 scenarios) and judgement-shaped, not scope-shaped.** `false-premise` and `ambiguity-clarify` test "don't act when you shouldn't" rather than "do a tiny thing efficiently." A pure trivial-scope scenario (e.g. "add one decorator to one function") would catch small models that thrash on work that should be one tool call.

**The small band is overweight (63%).** Twelve scenarios doing 2–15 LoC across 1–3 files makes reps-style stochastic variance the dominant signal, not scope-induced degradation. The reliability curve currently reads as "is this provider working today" rather than "where does this provider's competence cliff sit."

Filling these gaps is the **next phase decision**, not part of the current plan — the audit's deliverable is the visible map. Candidate medium-large additions (touching real-shaped EmptyOS code: a small app surface change, a manifest + handler + UI triple, a 2-app event-chain wiring) are obvious; what's worth scenarios-of-record vs. one-off probes deserves its own discussion.

Cross-reference: Axis 4 (Failure-mode coverage) and Axis 5 (Scale coverage) below are orthogonal to this band map and remain accurate as-is. The band map measures **model-output scope**; Axis 5 measures **fixture/input scope**; Axis 4 measures **failure shapes**. The three dimensions compose.

## Behavioural verification (fuzz vs reference)

Most verify functions are string-match, count-based, or AST-walk — deterministic enough but blind to "looks right, behaves wrong" drift. Two scenarios opt into a stronger shape: **fuzz inputs through a reference oracle and through the model's output, compare results**. Inspired by ProgramBench's behavioural-test verification (Meta, 2026-05) without adopting the benchmark itself; see the model-bench evolution plan (2026-05-23) for the framing.

### The opt-in surface

`agent_bench.behavioural_compare(...)` runs the model's module in a **subprocess** (`apps/model-bench/behavioural_runner.py`), calls a named symbol with each case's inputs, compares against an expected return value or an expected exception class name. Subprocess isolation buys:

- **Per-batch timeout** (default 5s) — a model's infinite loop can't hang the bench.
- **Crash protection** — `SyntaxError`, `sys.exit()`, `BaseException` all surface as structured results; the parent process never imports the model's code.
- **Clean import namespace** — each subprocess starts fresh; modules don't accumulate across cases.

`BehaviouralCase(inputs, kwargs, expected, expected_exception)` is one input + expected outcome. The reference is computed in-process at verify time, typically via a `_ref_<name>(...)` oracle function defined next to the scenario setup.

`scenario.verify(scratch)` still returns `VerifyResult`. The helper is opaque to the rest of the harness — no new result fields, no `AgentScenario` changes, no UI changes. A scenario adopts the shape by calling `behavioural_compare(...)` in its verify; everything else stays put.

### Where it ships today

| Scenario | Reference oracle | Fuzz inputs | Catches |
|---|---|---|---|
| `write-new-util` | `_ref_slugify(s)` (strip → lower → collapse non-alphanum → strip outer hyphens) | 30 (spec examples, empty, whitespace-only, punctuation-only, single char, mixed case, multi-dash, tab/newline, code-shaped strings, 200-char stress) | Missing `.lower()`, missing `.strip()`, multi-dash blow-up, empty-input crash, leading/trailing hyphen drift |
| `multi-file-refactor` | Original `old_func(x) = x * 2` behaviour | 4 direct calls on the renamed `core.new_func` + 1 importer-chain call on `use_b.run()` | Model renamed textually but changed semantics (`*` → `+`); model broke `from core import` while renaming; model edited the wrong file in the chain |

`multi-file-refactor` **composes both shapes**: the existing word-count regex check still runs first (cheap; catches "rename never happened"), then the behavioural layer (catches "rename done but contract broken"). The two layers are independent; failure messages tell you which one tripped.

### Sandbox isolation

By default, `behavioural_compare` runs its subprocess in the host Python interpreter — fine for catching ordinary "looks right, behaves wrong" drift, but the model's code lands with full host privileges and full network egress. To isolate it, set `"use_sandbox": true` on the bench-run body; the **whole** `scenario.verify` (including any `behavioural_compare` calls inside it) then runs inside a leased sandbox-pool member, and the behavioural subprocess inherits that isolation transparently. The kwarg sits on the verify orchestrator, not on this helper. See § "Sandboxed verification" below.

### When to extend behavioural verification to other scenarios

Add it when **both** are true:

1. The scenario has a **pure-function or pure-method shape** the model is expected to produce or modify.
2. A **reference oracle exists or can be written in <20 LoC** alongside the scenario, capturing the spec without leaking the implementation.

Skip it for scenarios that are textual-mechanical (`grep-replace`), read-only (`explain-structure`, `false-premise`), discovery-shaped (`call-app-discovery`), or judgement-shaped (`ambiguity-clarify`, `anti-goal`). For those, string-match / AST-walk remains the right shape — adding behavioural-check would be ceremony.

## Sandboxed verification (sandbox-pool integration)

The `eos+*` subjects can route their verify step through a leased sandbox-pool member by setting `"use_sandbox": true` on the `/api/agent-run` body. The agent still produces its output in the host's scratch dir — only the verify step (which imports and executes that output) crosses the isolation boundary. Inspired by ProgramBench's execute-only sandbox discipline (Meta, 2026-05), adapted to EmptyOS's existing `sandbox-pool` plugin rather than building a parallel sandbox. See improvement #2 of the model-bench evolution plan (2026-05-23).

### Wire shape

Host bench (`apps/model-bench/agent_bench.py:_verify_via_sandbox`):

```
1. call_app("sandbox", "lease",
            purpose=..., ttl_s=600, think_providers=["human"])
   → {ok, lease_id, host: http://127.0.0.1:<port>, port}

2. shutil.copytree(
       scratch,
       <repo>/sandbox-<port>/vault/model-bench-scratch/<run_id>/
   )

3. POST <member_host>/model-bench/api/sandbox-verify
   body: {scenario_id, scratch_subpath: <run_id>}
   → {ok, notes}   (member resolves the subpath against its own
                    vault_root, calls scenario.verify(scratch_dir),
                    returns the VerifyResult fields)

4. call_app("sandbox", "restart", lease_id)
   (reap any subprocesses verify may have spawned — important if the
   scenario's verify used `behavioural_compare`, which itself shells
   out a subprocess per case)

5. call_app("sandbox", "release", lease_id)
```

Member endpoint (`apps/model-bench/app.py:api_sandbox_verify`):

- Resolves `scratch_subpath` against `self.vault_root / model-bench-scratch`.
- Rejects path-escape attempts via `relative_to(vault_root)`.
- Calls `scenario.verify(scratch_dir)`; returns `{ok, notes}`.
- Catches any exception from `verify` and surfaces it structurally rather than 500ing.

`think_providers=["human"]` on the lease forces the leased member to fail-closed on any LLM call. The verify must remain a pure deterministic check; "ask another model whether this looks right" is excluded by construction.

### Subject coverage

| Subject | `use_sandbox=true` supported? |
|---|---|
| `eos+openai`, `eos+ollama`, `eos+claude` | yes — prototype lands here via `run_eos_agent_subject` |
| `claude-external`, `claude-code-eos` | no — still verify in-process; flag silently ignored on those rows |

Extending to the claude subjects is a small additional plumbing pass (thread `app` + `use_sandbox` into `run_claude_external_subject` and `run_claude_code_eos_subject`); deferred until there's demand.

### Graceful fallback

Any failure inside the lease → copy → HTTP → release chain falls back to in-process verify. The notes prefix tells you exactly which step failed:

| Prefix on `notes` | What happened |
|---|---|
| `[sandbox ok] ...` | Verify ran on a leased member; result came back over HTTP |
| `[sandbox unavailable: lease raised <Exception>] ...` | `call_app("sandbox", "lease", ...)` raised — usually the plugin isn't loaded |
| `[sandbox unavailable: pool_full] ...` | Every pool member is leased to another caller |
| `[sandbox unavailable: disabled_in_config] ...` | `[plugins.sandbox-pool] enabled = false` |
| `[sandbox unavailable: malformed lease response] ...` | Lease returned ok without host/port |
| `[sandbox copy failed: <Exception>] ...` | `shutil.copytree` couldn't write into the member's vault (disk, permissions) |
| `[sandbox HTTP failed: <Exception>] ...` | Member didn't answer the verify POST (crashed during boot, timed out, network refused) |

In every fallback case the verify still runs (in-process), so the bench row's `ok` / `notes` remain comparable to a non-sandboxed run — leaderboard readers see the absence-of-sandbox in the notes prefix, not as a missing row.

### Outbound network — Layer-1 socket shield (in-process, shipped)

`behavioural_runner.py` installs a Python-level socket shield at the start of every behavioural verification subprocess (`_install_socket_shield()` at the top of `main()`). It monkey-patches `socket.socket.connect` and `socket.create_connection` to raise `OSError("behavioural shield: outbound connect to ... refused")` on any non-loopback target. Loopback (`127.0.0.1`, `::1`, `localhost`, AF_UNIX paths) still works.

What this blocks:
- `requests.get("https://...")`, `urllib.request.urlopen(...)`, `aiohttp` sessions — all of which funnel through `socket.create_connection`.
- Raw `socket.socket(AF_INET, SOCK_STREAM).connect((host, port))` to non-loopback.

What it does NOT block (Layer-2 territory):
- Subprocess shell-outs (`subprocess.run(["curl", ...])`) — a fresh process whose socket module isn't patched.
- `ctypes` / `cffi` calls reaching libc's `connect(2)` directly.
- `socket.socket().connect_ex(...)` (a C-level method that bypasses the Python `connect` wrapper). Rare in practice; `requests` / `urllib` / `aiohttp` all go through `connect`.

The shield is always-on, costs <1ms at subprocess startup, and is testable cross-platform. It's the realistic Windows-compatible defense.

### Outbound network — Layer-2 OS-level (deferred)

Closing the subprocess + ctypes escape requires OS-level isolation:

- **Linux**: spawn the member with `unshare --user --map-root-user --net` (private network namespace) plus a userspace forwarder (`slirp4netns` or a `socat` bridge) so the host bench can still POST `/model-bench/api/sandbox-verify` into the member. Alternatively, `nftables` rules with `meta skuid <member-uid>` matching the member's UID — needs root on the host.
- **Windows**: Job Object with network isolation, or AppContainer. Both need explicit setup; not implementable inside `plugins/sandbox-pool/` without a separate runtime helper.
- **macOS**: `pfctl` anchors per-process.

Why this is deferred: the implementation needs a Linux + root test environment, and a correct loopback bridge is non-trivial — naive `unshare --net` breaks the bench's host→member POST because the member's `:9002` listener lives in an isolated netns the host can't reach. Tracked as a follow-up sandbox-pool plugin patch with its own commit.

Threat model honesty: today's verify functions don't reward outbound cheating, and a behaviour-shaped scenario like slugify doesn't tempt it (the spec is shorter than a fetched answer). Layer-1 is sufficient for the threat that currently exists; Layer-2 lands when a scenario surfaces a real subprocess-escape failure or when the cheat-path matters for an external user we're not yet servicing.

### When to use it

Flip `use_sandbox=true` per-run when the value of "model code never imports in the bench process" outweighs the ~5s lease/restart overhead. Today: makes sense for `write-new-util` and `multi-file-refactor` (the two scenarios with `behavioural_compare`-shaped verifies that import model code); not worth it for the four trivial-band scenarios or any read-only/judgement-shaped verify.

### How to reproduce

1. Confirm pool is up: `curl -H "Authorization: Bearer <token>" http://127.0.0.1:9000/sandbox/api/status` should show ≥1 idle member.
2. Run with sandbox: `POST /model-bench/api/agent-run` body `{"scenario_id":"write-new-util","subject_ids":["eos+openai"],"reps":1,"use_sandbox":true}`.
3. Inspect the returned row's `notes` — should start with `[sandbox ok]` on success or a `[sandbox <reason>]` fallback prefix on any failure path.
4. Repeat with `"use_sandbox":false` to confirm the non-sandboxed path is unchanged.

## Coverage framework — what this benchmark measures, and what it doesn't

Three axes with strong coverage, three with known gaps. Use this section to decide whether the matrix is comprehensive enough for a given goal.

### Axis 1 — Tool coverage (strong)

Every tool in the 8-tool agent registry is exercised by at least one scenario's expected path:

| Tool | Scenarios that drive it |
|---|---|
| `Read` | almost all |
| `Grep` | `grep-replace`, `multi-file-refactor`, `long-context-needle`, `read-large-file` |
| `Glob` | `find-missing-tests`, `explain-structure` |
| `Bash` | `debug-and-fix`, `error-recovery`, `progressive-dependency` |
| `Write` | `write-new-util`, `progressive-dependency`, `long-context-needle`, `find-missing-tests`, `call-app-discovery`, `cross-format-spec` |
| `Edit` | `add-temperature`, `grep-replace`, `multi-file-refactor`, `anti-goal`, `delete-with-callers`, `read-large-file` |
| `DeleteFunction` | `delete-with-callers` (preferred path; falls back to Edit) |
| `CallApp` | `call-app-discovery` |

### Axis 2 — Task-shape coverage (strong)

| Shape | Scenarios | Notes |
|---|---|---|
| Create from scratch | `write-new-util`, `progressive-dependency`, `cross-format-spec` | |
| Read / explore | `explain-structure`, `long-context-needle`, `false-premise` | |
| Update existing | `add-temperature`, `grep-replace`, `multi-file-refactor`, `anti-goal`, `read-large-file` | |
| Diagnose | `debug-and-fix`, `error-recovery`, `false-premise` | |
| Refactor | `multi-file-refactor`, `grep-replace`, `delete-with-callers` | |
| Navigate large tree | `long-context-needle`, `read-large-file` | Tree breadth + file depth |
| Synthesize / sequence | `progressive-dependency` | |
| Cross-app dispatch | `call-app-discovery` | EmptyOS-specific |
| Delete | `delete-with-callers` | Decoy with shared prefix tests word-boundary discipline. |

### Axis 3 — Metric coverage (comprehensive)

Captured per run in `AgentRunResult`:

- **Outcome**: `ok`, `notes`, `error`
- **Efficiency**: `tool_calls`, `tool_errors`, `iterations`, `efficiency` (floor / max(tool_calls, 1))
- **Speed**: `wall_ms`
- **Cost**: `usage`, `cost_usd` (from `_MODEL_PRICES_PER_M_TOKENS` × tokens, or explicit provider-reported cost)
- **Pattern**: `tool_histogram` (per-tool counts), `error_categories` (timeout / bash_shell_limitation / missing_target / edit_ambiguous / ...)
- **Provenance**: `run_group_id`, `variant_id`, `eos_git_sha`, `system_prompt_hash`, `overlay_applied`, `rep_index`, `subject_model`
- **Reliability**: `reps` loops lets you derive pass-rate variance across N repetitions

### Axis 4 — Failure-mode coverage (partial)

| Failure mode | Scenario | Tested? |
|---|---|---|
| Over-aggressive edits (decoy damage) | `grep-replace`, `multi-file-refactor` | yes |
| Negative-constraint violation | `anti-goal` | yes |
| Silently hallucinating a target | `ambiguity-clarify` | yes |
| Trusting false task premise | `false-premise` | yes |
| Narrative drift instead of action | `add-temperature` (reveals qwen3.5 prior) | yes |
| Partial / inconsistent work | `ambiguity-clarify` v1 semantics | partial — v2 tests anti-hallucination instead |
| Spiraling on permission denial | — | **no (needs consent-plumbing in the runner)** |
| Loss of coherence across long sessions | — | **no (every scenario is single-turn)** |

### Axis 5 — Scale coverage (partial)

| Input size | Max in fixtures | Gap? |
|---|---|---|
| File size | ~1500 lines (`read-large-file`) | Forces Read offset/limit discipline; bigger (10k+ lines) not tested. |
| Directory size | 40 files (`long-context-needle`) | Very large dirs (1000+) not tested — marginal past 40. |
| Session length | <2 minutes, single-turn | **Multi-turn coherence across context shifts not tested.** |
| Real-vault scale | 0 | **Agent against the live vault (3000+ notes) not tested.** |

### Axis 6 — Environmental coverage (partial)

| Condition | Tested? |
|---|---|
| Empty scratch | yes (`write-new-util`, `progressive-dependency`) |
| Partial / broken state | yes (`debug-and-fix`, `error-recovery`) |
| Correct state (no-op expected) | yes (`false-premise`) |
| Multi-file fixture | yes (`multi-file-refactor`, `grep-replace`, `delete-with-callers`) |
| Cross-format (`.py` + `.md` + `.toml`) | yes (`cross-format-spec`) |
| Live EmptyOS app graph | yes (`call-app-discovery`) |
| Concurrent mutation / race conditions | no (bench is serial by design) |
| Destructive-action gating (consent=deny) | no (bench passes `tool_consent=None`) |

## What "comprehensive enough" means — by goal

| Goal | Covered? |
|---|---|
| **Pick a provider** (claude vs openai vs ollama) | ✓ — matrix reliably discriminates on cost, speed, pass rate |
| **Detect regressions** after code / prompt changes | ✓ — variant_id + run_group_id + baseline group (`grp_20260419T064525_full-reps3`) |
| **Measure the EmptyOS tool advantage** | ✓ — `call-app-discovery` cleanly isolates it |
| **Measure small/local models (e.g. qwen3.5) reliability** | ✓ — reps=N exposes stochastic vs deterministic failures |
| **Prove the agent can handle real coding work** | ✓ — `delete-with-callers`, `cross-format-spec`, `read-large-file` close the previous gaps |
| **Prove the agent is safe for autonomous operation** | **no** — destructive-gating + long-session coherence are not measured |

## Remaining gaps

The "real coding work" claim is covered. Larger efforts still open:

- **Safety/autonomy tier** — destructive-gating (`tool_consent=deny` paths), long-session coherence, multi-turn context shifts.
- **Real-vault scale** — bench scratches are 1–60 files; the live vault is 3000+ notes. Whether the agent can navigate that without thrashing is unmeasured.
- **Concurrent mutation** — bench is serial by design; no scenario tests races.

## Metadata captured per run

```
AgentRunResult {
  # identity
  run_id, scenario_id, subject_id, rep_index, run_group_id, variant_id,
  # outcome
  ok, notes, error,
  # diagnostics
  tool_calls, tool_errors, iterations, wall_ms,
  tool_histogram,      # {Read:3, Bash:9, ...}
  error_categories,    # {timeout:1, bash_shell_limitation:4, ...}
  efficiency,          # min(1.0, floor / tool_calls)
  # provenance
  subject_model, eos_git_sha, system_prompt_hash, overlay_applied,
  # cost
  usage, cost_usd,     # computed from tokens × price table
  # artifacts
  transcript_path, scratch_path,
  timestamp,
}
```

- `run_group_id` stamps a batch — one "Run every scenario" click → one id across all (scenario × subject × rep) rows. Enables A/B across prompts, shas, variants.
- `variant_id` is a free-form tag (e.g. `ollama-scaffold-v1`, `no-overlay-baseline`).
- Price table in `agent_bench._MODEL_PRICES_PER_M_TOKENS` covers OpenAI gpt-4.1/4o families + Anthropic Claude 4.x. Local models (qwen, llama, mistral) are free. Unknown model → `0.0` (under-report, never fabricate).

## Baseline: `grp_20260419T064525_full-reps3` (variant `full-matrix-reps3`)

84 runs, 7 scenarios × 4 subjects × 3 reps.

```
                        claude-external   eos+claude        eos+openai        eos+ollama
                        claude-opus-4-7   claude-cli        gpt-4.1-mini      qwen3.5:latest
write-new-util          3/3 15.4s         3/3 13.5s         3/3  4.1s         3/3 15.4s
add-temperature         3/3 20.6s         3/3 19.7s         3/3  7.3s         0/3 23.0s
find-missing-tests      3/3 16.3s         3/3 16.0s         0/3  2.5s         2/3 19.2s
explain-structure       3/3 16.3s         3/3 14.5s         3/3  7.8s         3/3 30.4s
call-app-discovery      0/3 31.6s         0/3 19.9s         3/3  4.1s         0/3 35.1s
grep-replace            3/3 35.3s         3/3 31.8s         3/3  6.5s         3/3 14.7s
multi-file-refactor     3/3 46.0s         3/3 41.7s         3/3 10.4s         3/3 77.6s
```

Leaderboard:

| Subject | Pass | Wall | Total $ | $/pass |
|---|---|---|---|---|
| `claude-external` (claude-opus-4-7) | 18/21 · 86% | 25.9s | $7.89 | $0.3385 |
| `eos+claude` (claude-cli Max tier) | 18/21 · 86% | 22.5s | free | free |
| `eos+openai` (gpt-4.1-mini) | **18/21 · 86%** | **6.1s** | $0.02 | **$0.0011** |
| `eos+ollama` (qwen3.5:latest) | 14/21 · 67% | 30.8s | free | free |

## Findings

### What's true across reps=3

**gpt-4.1-mini is the cost/performance winner by an enormous margin.** Same pass rate as claude Opus, 4× faster, **~300× cheaper per passing run** ($0.0011 vs $0.3385). For agentic coding work at this difficulty tier, the frontier model isn't worth the price delta.

**`eos+claude` ≈ `claude-external` in quality, but free** when on the Max tier. Same 86%, nearly identical wall times. The EmptyOS wrapper adds no measurable overhead vs the subprocess path, which is the opposite of what we initially assumed.

**The `call-app-discovery` scenario is doing its designed job.** Both claude paths fail it identically (0/3) because `NativelyAgenticProvider` / the external CLI don't have our `CallApp` tool. The two subjects that do — `eos+openai` and `eos+ollama` — pass reliably when the model is capable. This scenario cleanly isolates the EmptyOS-specific architectural advantage and makes it quantifiable.

**qwen3.5 has a "code review by default" prior that dominates specific task instructions when source code is involved.** On `add-temperature` (Read focus_app.py → pick a temperature → Edit), it reads the file and writes generic code-review prose about missing imports and suspected bugs, never touching Edit. This is **deterministic (0/3)**, not stochastic. The prompt-overlay experiment (`ollama-scaffold-v1`) did not move the needle — the model interprets its review prose AS task completion.

**eos+openai fails `find-missing-tests` deterministically.** 0/3 at reps=3 even though earlier 1-rep runs passed. The earlier pass was stochastic noise. Worth a transcript dive.

**qwen3.5 is competitive on everything else.** With reps=3 it passes 5 of 7 scenarios cleanly, and on `grep-replace` it hit the floor pattern of 5 tools — faster than claude or ollama usually does. The two deterministic failures (`add-temperature` and partial-flaky `find-missing-tests`) point to task-phrasing fragility, not model weakness.

### What we disproved

- **"eos+claude runs claude."** False until this session. The agent's `_resolve_provider("claude")` didn't match any provider because the claude-cli provider is named `claude-cli`. It silently fell through to the first ToolCapable provider (openai_compat). Every earlier `eos+claude` row was really gpt-4.1-mini. Fixed with a semantic alias resolver (`agent_bench._resolve_bench_subject_provider`).
- **"Provider-specific prompt overlays help small models."** Tested with `ollama-scaffold-v1`. Zero effect on qwen3.5's two failing scenarios at reps=3. The rules get parsed but don't override the model's prior. The real lever is scenario phrasing, not declarative post-conditions.
- **"In-process claude sessions are 3× faster than claude-external."** Was an artifact of comparing gpt-4.1-mini to claude-cli. Once `eos+claude` actually runs claude, its wall times match claude-external within noise.
- **"1-tool-and-stop failures are stochastic."** Mixed. `grep-replace` single-failure at 1 rep was a tail event (passes 3/3 at reps=3). `add-temperature` single-failure was deterministic — same pattern every run.

### Known bugs fixed during the build

- **`prepare_scratch` returned relative paths.** Models correctly interpreted them as needing absoluteness and prefixed `/`, producing `/data/apps/...` which resolved against the drive root on Windows. Fix: `scratch.resolve()` before substitution.
- **Scenario templates using bare relative paths.** `find-missing-tests` originally said "sdk/" without `{scratch}/` prefix. Models used relative paths and globbed the EmptyOS repo by accident. Fix: scenario text uses `{scratch}/sdk/` consistently, and the system prompt overlay explicitly bans bare relatives.
- **`run_turn` didn't surface error content to the bench.** Tools returned rich error messages that the model saw but benchmark transcripts did not. Fix: `agent:tool_result` events now include a truncated `error_snippet` when `is_error=true`. Powers `error_categories`.

## Edit-tool fuzzy fallback (driven by bench findings)

The `edit_not_found` category dominated tool-error counts on multi-line edits — across the matrix the bench surfaced 10+ wasted Edit retries per batch. Two follow-on changes to `emptyos/sdk/agent_tools/edit.py`, each landed with reps=N validation against the bench:

**Stage-2 (variant `edit-fuzzy-v1`)** — line-aware fallback after exact match misses. Tolerates trailing whitespace + line-ending differences (CRLF vs LF), keeps leading indent exact (Python is whitespace-sensitive). Splice preserves the matched span's trailing line ending so multi-line edits don't silently drop a `\n`.

Validation: 31 runs against the 7 baseline scenarios. `edit_not_found` 10+ → **0**. Pass rate 84% (vs 86% baseline — within noise).

**Stage-3 (variant `edit-similarity-v1`)** — per-line `SequenceMatcher` similarity ≥ 0.85 when stage-2 also misses. Catches character-level corruption (em-dash `\u2014` → vertical-tab `\u000b`, smart quotes ↔ ASCII quotes, accented vowels). Leading indent still required exact. LF-only line splitting (`_split_lf*` helpers) so a corrupted control char inside `old_string` doesn't fool Python's `splitlines()` into splitting at the wrong place.

Validation: apples-to-apples reps=3 cohorts on the 3 new scenarios × {`eos+openai`, `eos+ollama`}. Pre-fix group `grp_20260419T102632_newscen`, post-fix group `grp_20260419T105208_simfix`. `delete-with-callers × eos+openai` slice (where the failure mode lived):

| Metric | Pre-fix | edit-similarity-v1 | Δ |
|---|---:|---:|---:|
| Pass rate | 2/3 | 2/3 | — |
| Avg tools | 14.0 | 11.0 | −21% |
| Avg errors | 4.3 | 3.3 | −23% |
| `edit_not_found` total | 7 | **1** | −86% |
| Avg wall | 23.1s | 15.2s | −34% |

A separate `reps=5` spot-check on the same slice held the trend (4/5 pass, avg 9.2 tools, 0 `edit_not_found`) — the reps=3 cohort numbers above are noisier per-rep but use the same shape as the pre-fix group, so they're the cleaner A/B.

**Failure modes the fix did NOT close:**
- **Deletion discipline (model judgment, not tool).** A residual `delete-with-callers` failure on gpt-4.1-mini is "removed def but left a caller reference". Addressed in the next section by `DeleteFunction`.
- **qwen3.5 multi-file work.** Still produces parse-breaking edits (`consumer.py no longer parses`) and `bash_shell_limitation` errors on the same scenario. Small-model capability ceiling, not an Edit-tool issue.
- **`invalid_target` errors.** Both subjects sometimes call `CallApp` on non-existent app names while exploring. Unrelated to Edit.

## Glob-tool absolute-pattern fix (driven by bench findings)

**The bug**: `find-missing-tests × eos+openai` was a deterministic 0/3 failure — earlier docs flagged it as worth a transcript dive. The transcript revealed that gpt-4.1-mini was correctly following the bench's system-prompt instruction to pass absolute paths, but `pathlib.Path.glob()` rejects absolute patterns with `"Non-relative patterns are unsupported"`. Two failed Glob calls and the model gave up. Pure tool/contract mismatch.

**The fix**: `emptyos/sdk/agent_tools/glob.py` detects absolute patterns (POSIX `/...`, Windows `D:/...`, UNC `//host/...`) and routes them through stdlib `glob.glob(recursive=True)`, which handles them natively. Relative patterns still go through `Path.glob()` unchanged.

Validation (group `grp_<...>_glob-abs-fix-v1`, eos+openai × reps=5):

| Metric | Pre-fix | Post-fix |
|---|---:|---:|
| Pass rate | 0/3 (deterministic) | **5/5 (100%)** |
| Avg tools | 2.0 (gave up) | 3.6 (Glob, Glob, Write) |
| Avg wall | 2.4s | 6.1s (now actually doing the work) |

## DeleteFunction tool (semantic delete primitive)

**Why**: even with stage-2/3 fuzzy Edit, `delete-with-callers` thrashes — gpt-4.1-mini hits 13–15 tool calls per run because each multi-line `old_string` for a function body is a fresh chance to typo a special character. The right primitive is name-in, span-out: `DeleteFunction(path, name)` uses Python's `ast` module to find the def boundaries (including decorators) and splice them out cleanly.

Scope: top-level `def` / `async def` / `class` in `.py` files. Refuses ambiguous names (multiple top-level defs with the same name). Refuses non-Python files. Refuses files that don't parse (won't silently no-op on broken state).

**Caller-cleanup reminder in the success message** (variant `delete-fn-v2`): bench-revealed quirk — strong models would call `DeleteFunction(core.py, name)`, then `Grep(name, consumer.py)` (which finds the now-broken callers), then declare the task done without cleaning them. The tool *description* warned about this but the model only reads it at registration. Embedding the reminder in the success message surfaces it in-context, exactly when the model decides what to do next.

Validation on `delete-with-callers × {eos+openai, eos+ollama} × reps=5`:

| Subject | Baseline (pre-DeleteFunction) | delete-fn-v1 (no reminder) | delete-fn-v2 (with reminder) |
|---|---:|---:|---:|
| eos+openai | 2/3 (67%) | 3/5 (60%) | **4/5 (80%)** |
| eos+ollama | 1/3 (33%) | 4/5 (80%) | 1/5 (20%) — noisy |

**gpt-4.1-mini**: the v2 reminder fixed the "shortcut and stop" pattern. Pass rate up, tool count down (avg 7.6 vs 14.0 baseline), wall down (12s vs 23s). Solid.

**ollama**: results are highly variable at reps=5 because qwen3.5 has multiple competing failure modes — ignoring the new tool entirely (rep=0/1 in v2 used only Read), over-deleting decoys (calls DeleteFunction twice with different names), and breaking syntax on follow-up Edits. True ollama rate likely 30–50%. The tool helps when it's used correctly; the failures that remain are model-judgment issues, not tool issues.

## Open architectural questions

1. **Thread `cwd` through `run_turn` into tools.** Tools currently resolve relatives against `Path.cwd()` (daemon process cwd). A `cwd` kwarg on `run_turn`, passed into each tool's resolve step, would make bench mode and real-use mode structurally distinguishable. Closes an entire class of "model passed a relative path" bugs at the architecture level.

2. **Route claude through `AnthropicSDKProvider` (ToolCapable) for agent work.** Currently the `claude-cli` path gets zero benefit from our tool registry. An `eos+anthropic-sdk` subject would let us measure claude *with* CallApp, multi-file refactor tools, etc. — the comparison we don't yet have.

3. **Provider-tier prompt scaffolding vs one-shot examples.** Overlay rules didn't work for qwen3.5. A one-shot example in the system prompt (showing a correct Read → classify → Edit sequence as a demo) is likelier to work. Worth testing as `variant=one-shot-v1`.

4. **Bash tool cross-platform.** On Windows, `ls`/`find`/`sh -c` fail. Small models don't know workarounds and thrash. Options: add `PythonExec` as an alternative (runs arbitrary Python in-process, no shell semantics), or Windows-map common Unix commands.

5. **More semantic edit primitives.** `DeleteFunction(path, name)` (AST-driven) shipped and validated above. The same shape would work for `RenameSymbol(path, old, new)` and `EditRange(path, start_line, end_line, replacement)` — both would reduce reliance on multi-line `old_string` reconstruction. Build when the bench grows scenarios that exercise them.

6. **One-shot prompt examples for small models.** Per docs, declarative overlay rules don't move qwen3.5's 1-tool-stop failures (`add-temperature`, `anti-goal`). A worked-example overlay (`variant=one-shot-v1`) showing a correct Read → classify → Edit sequence is the next thing to try. Untested as of writing.

## How to reproduce

1. Daemon running on port 9000 with the `model-bench` and `agent` apps loaded.
2. Visit `/model-bench/` → Agent tab.
3. Pick subjects via the chips at the top. Set `Reps=3`. Leave `Overlay` checked (or uncheck for a no-overlay baseline batch).
4. Click "Run every scenario" — mints one `run_group_id`, runs all (N × selected_subjects × reps) combinations serially (N = current scenario count, 17 as of writing), persists results to `data/apps/model-bench/agent_results.json`.
5. Compare groups via `GET /model-bench/api/agent-run-groups`.

## Where the data lives

- `data/apps/model-bench/agent_results.json` — all runs ever, append-only (capped at 500)
- `data/apps/model-bench/agent_transcripts/<run_id>.jsonl` — full event stream per run
- `data/apps/model-bench/agent_scratch/<run_id>/` — last 7 scratch dirs kept for inspection
