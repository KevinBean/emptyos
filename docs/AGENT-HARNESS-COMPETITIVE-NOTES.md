# Agent-Harness Competitive Notes — oh-my-pi primitives vs EmptyOS

**Purpose.** Map the primitives of `oh-my-pi` (OMP, an external agent harness) onto
EmptyOS's existing agent stack, and decide for each: **have / borrow / not-fit /
spike**. The discipline (`.claude/rules/` → `eos-repo-extract`): borrow the
*primitive*, not the product; justify every "borrow" by the **bench failure it
moves** (`docs/AGENT-BENCH.md`), never by feature-parity for its own sake.

This is the design contract behind the 2026-06-09 change that added a hash-anchor
Edit mode and worktree-isolated SubAgent. It is not a port plan and not a roadmap
to match OMP feature-for-feature.

## Stance

EmptyOS already has a mature agent harness — `/agent/` + `eos chat` share one tool
loop, tool registry, and permission manager (`docs/AGENT.md`); 18 tools in
`emptyos/sdk/agent_tools/`; a deterministic-verify benchmark (`docs/AGENT-BENCH.md`).
So the move is **absorb a few primitives, not swap the substrate**. Most of what
OMP markets (TUI, ACP, 13 LSP ops, 27 DAP ops) is either already covered, a poor
fit for this codebase, or speculative here.

## Have — already covered (do nothing)

| OMP primitive | EmptyOS equivalent |
|---|---|
| Character-tolerant edit matching | `edit.py` `_split_lf*` + 3-stage exact/line-fuzzy/similarity. Bench-validated `edit_not_found` −86% (`AGENT-BENCH.md:427-453`). |
| Absolute-path glob | `glob.py` routes absolute patterns through stdlib `glob`. Bench-validated 0/3→5/5 (`AGENT-BENCH.md:454-466`). |
| Name-in / span-out delete | `DeleteFunctionTool` (AST def-boundary delete). Bench-validated (`AGENT-BENCH.md:468-485`). |
| Read-only subagent | `SubAgent` `readonly=` allowlist (`subagent.py:49`). |
| Isolated worktrees | `emptyos/sdk/worktree.py` (`ensure_worktree`/`git_run`/`py_compile_files`); used by `fix-agent`. |
| Unsupervised tool isolation | `emptyos/sdk/tool_isolation.py` (dark-flagged). |
| Browser / fetch / screenshot | `browse` capability + `Fetch` + `Screenshot` tools. |

The first three are the exact failures OMP-style fixes target — already fixed and
bench-proven here. They are the **precedent** for how a harness fix lands in
EmptyOS (find the bench regression → fix the primitive → prove the delta), not open
gaps.

## Borrow — this change (each tied to a bench failure)

| Primitive | What | Bench target | Flag |
|---|---|---|---|
| **Hash-anchor Edit mode** | `Read(include_hashes=true)` emits a short per-line content hash; `Edit(start_line, end_line, expected_hash)` splices by line span and **rejects on stale anchor**. A *mode on existing tools*, not a new tool — avoids tool-surface bloat (OMP's own thesis is *fewer* tools). | `large-edit-anchored` (new) — replace one function in a ~520-line file; run flag off vs on, compare `tool_calls`/`wall_ms`. Graduation = a measured drop with no correctness loss. | `[apps.agent] feature.hash-edit.enabled` |
| **Worktree-isolated SubAgent** | `SubAgent(isolate="worktree")` runs a sub-turn's file edits in a per-run git worktree branch (reset→clean→`checkout -B subagent/<id> main`, copied from `fix-agent`). v1 leaves branch+diff for review (no auto-merge). | Not bench-measurable — the bench's `verify(scratch)` is scratch-dir-based and incompatible with "edits land in a worktree, not scratch." Proven instead by a unit test (`WorktreeApp` delegation) + the sandbox end-to-end in the change's verification. | `[apps.agent] feature.subagent-worktree.enabled` |

Both ship **dark**; the flag flips only on evidence (a measured bench win for
hash-edit; a clean sandbox run for worktree).

### Why hash-anchor is a *mode*, not a new tool

OMP's pitch is fewer, better tools with unified entry points. A parallel `HashEdit`
alongside `Edit` would force the model to choose between two edit tools — surface
bloat that undercuts the very thesis. So it folds in: Read gains an optional hash
column (transparent when off — `agent_loop.py` passes Read output verbatim), Edit
gains an early-dispatch fourth addressing mode that leaves the `old_string` pipeline
byte-identical. The genuine win is **token savings on large edits** (cite a line
span + 1 hash vs reproduce a 50-line `old_string`) plus an **exact** stale signal;
file-level staleness already exists (`SandboxedWrite`), so the justification is the
token delta, measured on the bench — not "OMP has it."

## Not-fit — declined

| OMP primitive | Why not |
|---|---|
| **LSP navigation (13 ops)** | Recorded finding: Serena (LSP server) was removed because "the codebase is dynamic-dispatch heavy (string-keyed `call_app`, `emit`, manifest contributions), so the interesting cross-references are **Grep-shaped, not LSP-shaped**" (`.claude/rules/multi-cli-participants.md`). `jedi`/`pyright` symbol nav (definition/references/rename) hits the same wall. At most a future **diagnostics-only** gate (`pyright --outputjson`), and even that overlaps the existing `py_compile` gate. |
| **DAP debugging (27 ops)** | No demand; the bench measures task completion, not step-debugging. Far below hash-edit/worktree in value. |

## Spike — parked (revisit on evidence)

| OMP primitive | Why parked |
|---|---|
| **Structured resource URLs** (`pr://`, `issue://`, `agent://` → here `vault://`, `app://`, `run://`, `kb://`) | Speculative refactor with real migration cost (rule 9: don't build the abstraction before the pain is real). The adjacent primitive already exists (`ContextRefTool`, `context_ref.py`) plus `vault_query`/`call_app`/`read`. Revisit only if the bench shows tool-proliferation is hurting routing. |
| **Worktree auto-merge** | v1 leaves branch+diff for review; `fix-agent` already owns the merge-gate shape (py_compile → regression test → ff-merge) if a consumer needs it. The regression step (`[apps.fix-agent] feature.regression-gate.enabled`) requires a daemon-free test that fails at the merge base and passes on the branch — the only deterministic evidence in that loop, since the persona "verifier" measured 2% self-recurrence on an unchanged system. |
| **Browser/control polish** | P3; `Fetch`/`Screenshot`/`browse` cover today's needs. |

## Build order = which bench failure each primitive moves

1. **`edit_not_found`, absolute-glob, semantic-delete** → already shipped + bench-proven (the precedent).
2. **Large-edit token cost** → `large-edit-anchored` scenario → hash-anchor Edit mode (flip flag on a measured drop).
3. **Parallel-write safety** → not bench-shaped → worktree SubAgent (flip flag on a clean sandbox run).
4. Resource URLs / LSP / DAP → no bench failure currently points at them → not built.

A primitive that no bench failure points at does not get built. When the bench
grows a new failure class, that is the signal to revisit the parked/declined rows.

## Cross-references

- `docs/AGENT.md` — the shared `/agent/` + `eos chat` loop, tool registry, permission manager.
- `docs/AGENT-BENCH.md` — the deterministic-verify benchmark; §427-485 = the already-fixed precedents.
- `.claude/rules/multi-cli-participants.md` — the Serena-removal finding (why LSP nav is not-fit).
- `emptyos/sdk/agent_tools/{read,edit,subagent}.py`, `emptyos/sdk/subagent_context.py` — the borrowed primitives.
- `apps/extension/dev/model-bench/agent_scenarios.py` — `large-edit-anchored`.
