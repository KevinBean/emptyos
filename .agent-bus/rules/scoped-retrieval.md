---
paths:
  - "emptyos/sdk/scoped_retrieval.py"
  - "apps/public/standard/assistant/**"
  - "apps/public/core/search/**"
---

# Scoped Retrieval — fast folder/tag slice before the whole vault

The vault is organized by PARA folder + tag, so a companion can answer *faster*
and *more precisely* by retrieving from a **narrow relevant slice** before (or
concurrently with) the whole-vault pass. This is the agent-bus L0/L2 scan→drill
shape (`.claude/rules/agent-bus.md`) applied to vault **content**.

**Primitive:** `emptyos/sdk/scoped_retrieval.py` (pure: `Scope`, `ScopedResult`,
`select_scopes_deterministic`, `scope_menu`/`parse_scope_key`, `should_escalate`)
+ `BaseApp.scoped_retrieve(query, ...) -> ScopedResult` (orchestrator — Tier-0
scope routing + Tier-1 scoped embedding search). **Consumers (Rule-9):**
assistant `_scoped_context` (#1), search `_ask` scoped-first (#2).

## The two-tier flow

- **Tier 0 — scope routing (default deterministic, no LLM):** match query tokens
  against an **uncapped** interest profile (project/area names + top tags) →
  1-3 `Scope`s (folder and/or tag). `router="llm"` does one `select()` over a
  `scope_menu` instead. No scope → caller uses the whole-vault path.
- **Tier 1 — scoped slice:** `vault_query(folder=, tags=)` the scopes → embed
  only that slice → `EmbeddingIndex.search(top_k, min_score)`. `tier="scoped"`
  with snippets is the only answerable outcome; every other tier
  (`no-scope`/`no-embeddings`/`empty-slice`/`scope-too-broad`) means escalate.

## Two-phase companion (assistant only)

Per Kevin's "full text always used, not waited in fast mode, comparison always
on": stream a **fast scoped answer** (Phase 1), run the **whole-vault answer
concurrently** (Phase 2, `_full_answer` — richer retrieval), then **reconcile by
SOURCE-NOTE RECENCY** (`_reconcile` → `select()`), NOT by which scope is
"authoritative". Four verdicts:

- `consistent` / `prefer_scoped` → the fast answer is current → `verify-ok` chip,
  no correction (this is what killed the 86% over-correction — a right fast
  answer now stands).
- `prefer_full` → a **newer** note supersedes → `verify-correction` card.
- `conflict` → notes disagree and recency is ambiguous → `verify-conflict` card
  showing **both** answers + each side's dated, clickable sources; picks neither.

Recency = `_note_date` (frontmatter `updated`→`created` → latest `## Timeline`
bullet → journal filename; **NO mtime** — git/sync resets it = false confidence).
The newer source wins **either side**. **Durable ripple** (the "ripple the truth"
direction): on a confirmed correction the user clicks "Reconcile in vault →" to
record it in the stale note's `## Timeline` (or `superseded_by`) via a
propose→diff→Apply gate (`reconcile.py` over `emptyos/sdk/diff_proposal.py`
`DiffProposalStore`) — so the conflict stops recurring. The vault's own dates +
`## Timeline` + `superseded_by` ARE the register; no new fact-store.

WS streams the two phases (+ verify verdict); HTTP blocks, returns
`{verdict, correction?, conflict?}`. `companion.preliminary_provider` pins
Phase 1 (auto mode, non-vision); Phase 2 + reconcile use the chain default —
so e.g. openai-mini (fast Phase 1) + claude-cli (free Phase 2/reconcile) is the
"control by scope" config. On a large vault, per-turn latency is
**retrieval-dominated**, not model-dominated.

## Dark-flag contract (regression: off ⇒ byte-identical)

- `[apps.assistant] feature.companion-twophase.enabled` (master, default false).
- `[apps.assistant.companion]` knobs: `router`, `preliminary_provider`,
  `min_top_score` (0.45, **above** the 0.30 inclusion floor), `max_scopes`,
  `scoped_max_notes`, `verify_max_files`/`verify_max_chars`.
- `[apps.search] feature.scoped-first.enabled` (default false).
- Off, or any non-`scoped` tier ⇒ the existing whole-vault path runs unchanged.

## Gotchas (found in live verify — don't re-derive)

1. **Don't alias a whole top-level folder** (`10_Projects` etc.) — it trips the
   per-scope breadth cap and bails the whole retrieval. Only narrow scopes
   (single project/area NAME, a bounded tag) belong in `SCOPE_ALIASES`. A
   too-broad scope is *skipped per-scope*, not fatal — narrow siblings survive.
2. **Scope routing needs an UNCAPPED profile.** `vault_interest_profile` caps
   subdirs at 30 (token economy); a late-sorted project would be invisible to
   routing. `scoped_retrieve` builds its own uncapped profile from disk.
3. **`_full_answer` must guarantee the question reaches the model.** With a
   session that doesn't yet carry the user turn, `_build_chat_messages({})`
   yields no messages → empty answer → reconcile always "consistent". Append
   the question as the final user turn when absent.

## When NOT to use it

- Pure UI/CRUD or generic queries (no scope) — falls through to whole-vault.
- A tool-loop **coding agent** — it already self-corrects by iterating; don't
  port the two-phase reconcile. For codebase scoping use `rules_for_paths`
  (`.claude/rules/path-scoped-rules.md`), the code-domain sibling.
- Cost: two-phase runs *two* answers + a reconcile per turn — trade for
  perceived latency + a consistency guarantee, measured under the dark flag.

## Cross-references
- `.claude/rules/time-dimension.md` — resolve by past→now recency; surface
  conflicts, never silently pick. The reconcile is this rule's read-side.
- `emptyos/sdk/diff_proposal.py` (`DiffProposalStore`) — the durable-ripple
  propose→diff→apply gate over `SandboxedWrite` (assistant `reconcile.py`).
- `.claude/rules/agent-bus.md` — the L0/L2 scan→drill model this mirrors.
- `.claude/rules/proposed-action.md` — the verify-correction is a soft
  appearance→truth reconcile (`.claude/rules/three-natures-lens.md`).
- `.claude/rules/model-ability.md` — why a cheap preliminary is safe (bounded
  grounded-Q&A). `.claude/rules/selector.md` — `select()` powers router+reconcile.
