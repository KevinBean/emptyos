# Context Packing

Context Packing is EmptyOS's native context compression domain. It borrows the
useful idea from tools like Headroom, but not the transparent proxy approach.
It never sits between a provider and its wire protocol. It prepares material
before an EmptyOS LLM call, stores the original material for retrieval, and
fails open when anything goes wrong.

## Goals

- Reduce input tokens for large `self.think()` calls and agent tool results.
- Keep the implementation centralized in one domain, not scattered through
  apps, CLI commands, and provider adapters.
- Preserve task quality by compressing only low-risk context blocks.
- Preserve recoverability by storing originals behind stable references.
- Never cause an LLM call to fail. Packing failure returns the original input.

## Non-Goals

- Do not proxy Claude Code, OpenAI, Anthropic, Ollama, or any provider API.
- Do not compress system prompts, developer rules, tool schemas, permissions,
  consent text, or the current user request.
- Do not replace Agent Context Bus. The bus decides which long-term rules or
  skills are relevant; context packing decides how selected material fits a
  budget.
- Do not claim global 60-95% savings. Normal short calls should be untouched.

## Why recoverability is load-bearing (evidence)

ByteDance's EdgeBench (2026, 134 day-long tasks, ~38,000 agent-hours) found
that one continuous 12h agent run with retained context/workspace beats six
independent 2h restarts best-of at the same time budget, and that a 1M-context
model stays above its 200k sibling at every checkpoint — accumulated context
is measurable capability, not overhead. That is why packing/compaction must
**store originals behind stable references** (and why `agent_loop`'s session
compaction archives elided tool results — `.claude/rules/prompt-prefix-cache.md`
rule 3): destroying accumulated experience to save tokens trades a visible
cost for an invisible one. See `docs/OPEN-SOURCE-BORROWING-PLAN.md`
§EdgeBench.

## Domain Boundary

All packing logic lives under a dedicated package:

```text
emptyos/context/
|-- __init__.py
|-- blocks.py          # ContextBlock, PackedBlock, PackRequest, PackResult
|-- budget.py          # token estimation, budget allocation, thresholds
|-- packer.py          # orchestration and fail-open API
|-- store.py           # reversible original storage + ref lookup
|-- reducers/
|   |-- __init__.py
|   |-- logs.py
|   |-- json_data.py
|   |-- jsonl.py
|   |-- diff.py
|   |-- search_results.py
|   |-- code.py
|   `-- markdown.py
`-- trace.py           # stats, diagnostics, billing-facing metadata
```

Callers may use the domain, but must not implement ad hoc reducers locally.

## Mental Model

The unit of work is a typed block, not a raw prompt string.

```python
ContextBlock(
    kind="tool_output",
    subtype="log",
    text=daemon_log,
    source="data/daemon-restart.log",
    priority="medium",
    reversible=True,
)
```

The packer returns text suitable for a prompt, plus metadata:

```python
PackResult(
    text=packed_prompt,
    refs={"ctx_01H...": OriginalRef(...)},
    stats={...},
    degraded=False,
)
```

The model sees compact summaries such as:

```text
[ctx:ctx_01HLOG daemon-restart.log, original 6115 est tok -> 875 est tok]
Daemon log summary:
- 7x ERROR: Input redirection is not supported...
- Kernel started. 114/135 apps loaded...
- Warning: failed to load billing...
Ask for ctx_01HLOG if exact lines are needed.
```

The original remains available to EmptyOS, not to an external provider by magic.
Apps or agent tools can expose an explicit retrieval path when useful.

**Refs are recoverable only inside an agentic loop.** The "Ask for ctx_X"
hint is actionable when the model can take another turn (the agent tool loop,
via a `ContextRef` tool). A one-shot `self.think()` cannot follow up, so
think-call packing is **lossy-final**: the packed summary is the only context
the model gets. This is why tool-output packing ships before think packing
(see Rollout).

## Block Kinds

Protected blocks are never compressed:

| Kind | Policy |
|---|---|
| `system` | Never compress |
| `developer_rule` | Never compress |
| `permission` | Never compress |
| `consent` | Never compress |
| `tool_schema` | Never compress |
| `current_user_request` | Never compress |

Compressible blocks are reducer candidates. **Phase 1 ships only the five
query-independent reducers** (top group). A deterministic reducer has no query,
so it cannot decide what is "relevant" — the deferred kinds (bottom group) would
drop the very section the model needs. They wait for an LLM reducer or a real
consumer that supplies relevance.

Phase 1 — deterministic, query-independent:

| Kind | Reducer | Safe Target |
|---|---|---:|
| `log` | Group duplicate lines, preserve errors, warnings, startup/shutdown, first/last window | 70-90% |
| `jsonl` | Count rows, group by stable fields, preserve anomalies and examples | 80-95% |
| `json` | Preserve schema, top-level counts, selected scalar fields | 75-95% |
| `search_result` | Group by file/source, keep matched lines (already determined) and nearby context | 50-80% |
| `diff` | Preserve files, hunks, signatures, all changed lines; thin context only | 30-70% |

Deferred (need query-awareness or an LLM reducer — pass through verbatim today):

| Kind | Reducer | Safe Target |
|---|---|---:|
| `code` | Preserve imports, symbols, signatures, call sites, selected bodies | 30-60% |
| `markdown` | Preserve frontmatter, headings, tasks, links, relevant sections | 20-50% |
| `conversation_history` | Preserve recent turns verbatim, summarize older turns with refs | 30-70% |

## API

The domain exposes one primary function:

```python
from emptyos.context import pack_context

result = pack_context(
    blocks=[
        ContextBlock(kind="current_user_request", text=prompt, protected=True),
        ContextBlock(kind="log", text=log_text, source="data/daemon-restart.log"),
    ],
    budget=ContextBudget(max_input_tokens=12000, reserve_output_tokens=2000),
    profile="think",
    store_root=kernel.config.data_dir / "context",
)
```

`pack_context()` is deterministic and synchronous unless a future reducer
explicitly opts into async retrieval. The first implementation should avoid LLM
summarization entirely.

**Acceptance floor:** a reduction is accepted only when it saves more than
`ContextBudget.min_reduction_ratio` (default 5%) of the block's tokens —
otherwise the original is kept verbatim and no ref is minted. The ctx header +
recovery-ref line eat the gains of a marginal pack, so near-1.0 ratios are a
net loss in both tokens and fidelity. (Discipline borrowed 2026-06-13 from
OpenHuman's TokenJuice ratio>0.95 pass-through; reimplemented from the idea
only — upstream is GPL-3.0.)

**Deferred knob — failure-widens-window:** TokenJuice preserves a *wider*
head/tail window when the compacted command failed (nonzero exit), because
failures are where detail matters. EmptyOS has no consumer yet — only Grep
content output packs in the agent tool loop, and Grep has no failure
semantics. Build it (a `preserve_on_failure`/`is_failure` hint on `pack_text`
that widens reducer windows or skips packing) when Bash/CLI output gets a
reducer. The `log` reducer's never-drop-ERROR rule already covers the
line-level half.

## Integration Points

### 1. Internal Think

`BaseApp.think()` gets a narrow, centralized hook after agent resolution and
before provider routing:

```python
packed = context.pack_think_call(app=self, prompt=prompt, kwargs=kwargs)
prompt = packed.prompt
kwargs = packed.kwargs
```

The hook is off by default. It activates only when a caller passes
`context_pack=True` or when settings enable it for a small pilot set:

```text
context_pack.enabled = false
context_pack.app.search = true
context_pack.app.kb = true
context_pack.min_est_tokens = 8000
```

This keeps ordinary calls untouched.

### 2. Agent Tool Results

Agent tools that can produce large output call the context domain before
returning content:

- `Read`
- `Grep`
- `VaultQuery`
- future `Fetch`
- topology/system inspection commands

The tool should still expose exact retrieval through normal tool calls:

- `Read(path, offset, limit)` for files
- `VaultQuery(section=...)` for notes
- `ContextRef(ref_id)` for packed ephemeral objects

### 3. CLI/API Output For External Agents

Commands likely to be consumed by Claude Code or Codex add an explicit packed
output mode:

```bash
eos event log --packed --limit 500   # built — JSONL → jsonl reducer
eos context pack data/daemon-restart.log --kind log   # built (Phase 1 CLI)
eos context show ctx_01HLOG          # built (Phase 1 CLI)
```

This does not intercept Claude Code's own prompt. It shortens the outputs
Claude Code chooses to read. `eos event log --packed` serializes the event
history to JSONL and runs it through the `jsonl` reducer (group by event type,
keep anomalies + first/last), printing the summary + a `ctx_` ref — a 341-event
log compresses ~99% while the full log stays recoverable via `eos context show`.
This is the **first realized think-vs-tool-independent consumer**: app `think()`
calls carry prose/markdown (no structural reducer yet), and the structural kinds
flow through agent **tool output** (Integration Point 2) and these CLI surfaces —
not through app think context. `eos topology --packed` is a natural sibling but
topology is a web-only surface today (no CLI command), so it waits for one.

**Two gotchas any future `--packed` command must heed** (caught live):

- **Print with `markup=False, highlight=False`.** Packed summaries use literal
  `[ctx:...]` / `[jsonl summary: ...]` header lines; Rich's `console.print`
  eats `[...]` as style markup and silently drops them. Plain `print()` works
  too.
- **Recovery must resolve the same `data_dir`.** `eos context show` honors
  `EOS_CONFIG` so a ref written under one config (a sandbox member, or a
  `--packed` run with `EOS_CONFIG` set) is recoverable under the same env.

## Reversible Store

Originals live under the kernel data root:

```text
data/context/
|-- refs/
|   `-- 2026-06-07/
|       `-- ctx_01HLOG.json
`-- traces/
    `-- pack-20260607.jsonl
```

Each ref record contains:

```json
{
  "id": "ctx_01HLOG",
  "created_at": "2026-06-07T...",
  "source": "data/daemon-restart.log",
  "kind": "log",
  "sha256": "...",
  "original_text": "...",
  "ttl_hours": 24,
  "privacy": "local-only"
}
```

Refs are local-only. They are for EmptyOS retrieval, auditing, and follow-up
tool calls, not provider-side hidden memory.

**TTL is lazy-reap-on-read in v1** (the autopilot-grant pattern): an expired ref
is deleted when a read misses or sweeps past it. There is no boot or scheduled
reaper yet — add one only if stale-ref accumulation becomes a real problem.

## Budget Policy

Packing should not run on small prompts. Initial thresholds:

- `min_est_tokens`: 8,000
- `target_est_tokens`: 6,000 for packed add-on material
- `reserve_output_tokens`: 2,000
- Protected blocks are budgeted first.
- Recent conversation turns are budgeted before older turns.
- If protected blocks alone exceed the budget, the packer returns them unchanged
  and records `degraded=true`.

Token estimation starts with `ceil(chars / 4)`. Provider-specific tokenizers can
be added later, but the first version should stay dependency-light.

## Reducer Rules

Reducers must be deterministic, explain what they removed, and include at least
one retrieval hint when reversible.

Each reducer returns:

```python
ReducerResult(
    text=summary,
    original_tokens=...,
    packed_tokens=...,
    refs=[...],
    warnings=[],
)
```

Reducers must not:

- invent facts
- rewrite user instructions
- reorder diff hunks unless the output says it did
- drop errors or failed tests from logs
- drop unmatched search results silently
- parse untrusted text with unsafe eval

## Async Boundary

The packer's pure logic is synchronous and deterministic. That is a property of
the *library*, not a license to call it inline on an async path. Every entry
point that runs inside the event loop — `BaseApp.think()`, the agent tool loop —
**must** offload the pack call:

```python
packed = await asyncio.to_thread(pack_context, blocks, budget, store_root=root)
```

The reducer regex over large text and the store's disk writes are exactly the
"sync work in the request path" that wedged the blacklisted Headroom plugin. The
store write happens *inside* the threaded call (the packer mints + persists refs
synchronously), so a single `to_thread` covers both compute and I/O. Never `await`
nothing and call `pack_context` directly from an async handler.

This rule composes with Fail-Open below: offload, then fail open.

## Fail-Open Contract

Every public entry point follows this shape:

```python
try:
    return _pack(...)
except Exception as exc:
    trace_fail_open(exc)
    return original_input
```

Fail-open is not optional. A context packing bug must never break:

- `self.think()`
- `think_stream()`
- agent tool loops
- user-visible CLI/API commands

## Observability

Each packed call emits a local trace:

```json
{
  "ts": "...",
  "app": "kb",
  "profile": "think",
  "original_est_tokens": 24400,
  "packed_est_tokens": 8700,
  "reduction_pct": 64.3,
  "reducers": ["markdown", "search_result"],
  "refs": 8,
  "degraded": false,
  "fail_open": false
}
```

`think:executed` can include the packing stats later, but the trace source of
truth stays in `emptyos/context/trace.py`.

## Rollout

Ordered by **risk and recoverability**, not by the order the integration points
were listed. Tool-output packing is recoverable (the agent can re-read via a
`ContextRef` tool) and low-stakes; think-call packing is lossy-final and
quality-sensitive, so it comes after the library is proven where mistakes are
cheap.

Phase 1: Library + CLI. **(built)**

- Add `emptyos/context/`.
- Implement `log`, `jsonl`, `json`, `search_result`, and `diff` reducers.
- Add tests with fixed sample inputs (`tests/test_unit_context_pack.py`).
- Add `eos context pack/show` for manual testing.

Phase 2: Agent tool-output packing (first real consumer — recoverable). **(built)**

- Hook in `emptyos/sdk/agent_loop.py` (`_maybe_pack_tool_output`, called after
  the post-tool hooks) packs the model-facing `content` via
  `await asyncio.to_thread(...)`, leaving `display` + the UI emit untouched.
- Kind inference is honest about Phase-1 reducers: only **Grep in `content` mode**
  (`path:line:text`) maps cleanly to the `search_result` reducer today, so that
  is the one tool that packs. Read / VaultQuery / Fetch produce numbered dumps or
  markdown with no query-independent reducer — they pass through until a
  content-aware/markdown reducer lands (the hook already routes them, no-op).
- `ContextRef(ref_id)` agent tool (`emptyos/sdk/agent_tools/context_ref.py`,
  registered in `build_registry`) makes the "Ask for ctx_X" hint real — it
  returns the byte-exact original until TTL.
- Exact range/read operations (`Read(path, offset, limit)`) remain the other
  retrieval path.

Phase 3: Opt-in think packing (lossy-final). **(built — no pilot consumer yet)**

- `BaseApp.think()` gains `context_pack: bool = False` + `context: list | None`.
  **The prompt is never packed** (it's the protected `current_user_request`);
  the caller hands LARGE low-risk material as typed `context` blocks, which are
  compressed and prepended as a delimited "Reference Context" section before the
  untouched request. This realizes the doc's "unit of work is a typed block, not
  a raw prompt string" — a monolithic prompt can't be auto-split safely.
- Hook (`_maybe_pack_think_context`) runs after agent resolution, before provider
  routing, via `asyncio.to_thread`. Fails open to the original prompt.
- Enabled per call (`context_pack=True`) or per app via settings
  (`context_pack.app.<id>` or global `context_pack.enabled`); off by default.
- Billing is unchanged: the `think:executed` `prompt_len` reflects what is
  actually sent (request + compressed context); the original-vs-packed ratio
  lives in the pack trace under `data/context/traces/`.
- **No pilot consumer wired yet** — `search` / `kb` / `app-builder` /
  `feature-pipeline` adopt it by passing `context=[...]` when they have large
  low-risk material; until then the path is dormant.

Phase 4: Policy and UI.

- Add settings UI for per-app packing.
- Add trace viewer to the System or Billing app.
- Compare packed vs unpacked quality through `model-bench`.

## Acceptance Criteria

- A packing failure never fails an LLM call.
- Protected blocks are byte-identical in packed output.
- `ctx_*` refs can retrieve the exact original text until TTL expiry.
- Reducer tests cover logs, JSONL, JSON, search output, and diffs.
- Pilot apps show useful reductions on large prompts with no increase in parse
  failures or task regressions.
- The default install remains behaviorally unchanged until packing is enabled.
