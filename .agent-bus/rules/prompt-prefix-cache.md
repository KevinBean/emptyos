---
paths:
  - "emptyos/sdk/agent_loop.py"
  - "emptyos/capabilities/**"
  - "apps/**"
---
# Prompt-Prefix-Cache Discipline — stable prefixes are money

Any multi-turn LLM loop (agent loop, rooms session, staff run) resends its
whole history every round-trip. Providers with prefix caching (OpenAI
automatic, Anthropic `cache_control`, DeepSeek) charge cached prompt tokens at
25–50% — but only if the prefix is **byte-identical** to the previous call.
One dynamic token near the top of the prompt (a timestamp, a live counter, a
reshuffled tool list) invalidates the cache for everything after it, silently
multiplying input cost.

**Lineage:** borrowed 2026-07-03 from DeepSeek-Reasonix (`esengine/
DeepSeek-Reasonix`, `docs/SPEC.md`) via the repo-borrow discipline. We kept
the four rules and the "cache-hit rate is the key observability signal"
posture, not their code. EmptyOS already did most of this by accident —
this rule makes it deliberate so a refactor doesn't regress it.

## The four rules

1. **Prepend-only session growth.** Never reorder, rewrite, or delete prior
   messages mid-run. Append. (`emptyos/sdk/agent_loop.py` `session.messages`
   is the reference impl.)
2. **Stable prefix within a run.** System prompt and tool schemas are built
   **once per turn/run** and passed unchanged to every iteration — no
   timestamps, live state, or per-iteration context injected into `system=`
   on paid paths. Per-turn context belongs in the **user message**, where it
   only invalidates the tail. (`run_turn` builds `system` + `wire_tools`
   before the iteration loop; keep it that way.)
3. **Compaction is the only cache-reset point** — deliberate and rare, never
   incidental. And it must **archive what it destroys**: `_compact_history`
   callers pass `archive=` and persist elided originals via
   `_archive_compacted` (→ `data/apps/<app>/compaction-archive/<session>.jsonl`).
   Rationale is also empirical: EdgeBench (ByteDance, 2026) shows cumulative
   retained context beats restart-fresh sampling at equal time budget —
   accumulated experience is an asset, don't silently delete it.
4. **One model = one session.** Don't interleave two models' turns in one
   message history — each model gets its own session so neither disturbs the
   other's prefix. (Rooms per-participant sessions and Reasonix's dual
   planner/executor sessions are the same shape.)

## Where the machinery already lives

| Piece | Where | Status |
|---|---|---|
| Anthropic `cache_control` (system + tools blocks) | `emptyos/capabilities/providers/anthropic_sdk.py` (`_apply_system_cache`, `cache_system=True`) | Built; only active when a chain names `anthropic`/`anthropic_sdk` — the default chain uses claude-cli (opaque, self-caching) |
| OpenAI automatic prefix caching | provider-side, no markers needed | `openai_compat.py` reads `prompt_tokens_details.cached_tokens` + bills cache hits at 50% (`_calc_cost_with_cache`) |
| Cache-hit observability | `cached_tokens` flows on `think:executed` → billing accumulates it (`daily_stats`/`app_stats.cached_tokens`); `cache_hit_pct` on `/billing/api/today` + `/api/usage`, hero chip on `/billing/` | The Reasonix "key signal" — watch it after prompt refactors |
| Exact-match response cache | `emptyos/sdk/think_cache.py` (`think(cache=True)`) | Different axis — skips the call entirely; not a prefix cache |
| Compaction archive | `agent_loop.py` `_compact_history(archive=)` + `_archive_compacted` | Rule 3's mechanism |

## When NOT to apply

- **Local providers (ollama)** — no billing, and llama.cpp prefix reuse is
  managed server-side; don't contort prompts for it.
- **One-shot `think()` calls** — no second call to hit the cache; a dynamic
  system prompt is fine.
- **claude-cli subprocess** — manages its own caching internally; opaque to
  EmptyOS, nothing to do.
- **Freshness-critical context** (今天's date in a scheduling prompt) — put it
  in the user message, not `system=`; that's rule 2, not an exception.

## The audit question

Before merging a change to a loop's prompt assembly, ask: *does anything
byte-vary between iterations that doesn't have to?* If yes, move it to the
user message or hoist it above the loop. Then watch `/billing/` cache-hit %
— a refactor that tanks it from ~80% to ~0% on an agent-heavy day is a real
cost regression even though every test passes.

## Cross-references

- `.claude/rules/staged-pipeline.md` — per-run budgets; cache discipline is
  how a run stays *under* them.
- `emptyos/sdk/agent_loop.py` — reference implementation (stable system,
  prepend-only messages, archived compaction).
- `docs/CONTEXT-PACKING.md` — the token-budget sibling (what to send);
  this rule is about *how* to send it (stably).
- `docs/OPEN-SOURCE-BORROWING-PLAN.md` § DeepSeek-Reasonix — the verdict
  that produced this rule.
