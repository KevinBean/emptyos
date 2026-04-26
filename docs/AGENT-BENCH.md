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

## Scenarios (17)

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
