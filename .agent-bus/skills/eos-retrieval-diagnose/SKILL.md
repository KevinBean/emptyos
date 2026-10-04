---
name: eos-retrieval-diagnose
description: Diagnose why a vault/KB retrieval surface returns off-topic, missing, or duplicated results — separate a query-shape fault from an index-coverage fault from a ranking fault before changing anything. Use when search, the assistant, a companion, or any RAG-backed feature "answers about the wrong thing", "can't find a note I know exists", cites a note back at itself. NOT for a feature that returns an error (that is a bug — use eos-bug-audit), NOT for embedding/index code changes you already know you want.
---

# Diagnose a Retrieval Fault

The default failure mode is **blaming the model for what the query or the index
did**. A RAG surface that answers off-topic looks like a prompt problem, so the
prompt gets rewritten — repeatedly — while the actual cause is that the search
never saw the right note. Two separate incidents on 2026-08-17 both presented as
"the AI writes irrelevant summaries" and were both retrieval faults; no amount of
prompt wording would have fixed either.

Work the ladder in order. **Each rung is a measurement, not a guess**, and each
rules out a whole class before you touch code.

## Rung 0 — reproduce with the raw retrieval, not the finished answer

Call the *search* endpoint directly with the same query the feature uses, and
look at the paths it returns. If the retrieved notes are wrong, stop: nothing
downstream can recover, and the prompt is innocent.

```
GET /search/api/search?q=<query>&top=6&mode=embed     # vector path
GET /search/api/search?q=<query>&top=6                # keyword path
```

## Rung 1 — is the query the user's, or yours?

**The single highest-yield check.** Many endpoints use one string as *both* the
retrieval query and the instruction prompt (`/search/api/ask` embeds the whole
`q`). Wrapping a user's sentence in 150 words of instructions means retrieval
matches your boilerplate.

A/B it — same endpoint, bare user text vs the wrapped prompt:

- Different top hits ⇒ **boilerplate is steering retrieval**. Fix by separating
  the retrieval query from the instruction, not by rewording the instruction.
- A hit that appears *only* in the wrapped run names the contaminating phrase.

## Rung 2 — is the index actually complete?

Coverage failures are invisible: retrieval returns confident results from
whatever subset it holds. Read the accounting the endpoint reports.

```
indexed=5000  truncated=True     # ← searching 18% of a 27k-note vault
```

Then probe a note you *know* should match and check membership directly, rather
than inferring from rank. Flat, undifferentiated scores (everything ~0.55) are
the tell for "best of an arbitrary subset". If a note is absent, no ranking or
prompt change will ever surface it.

## Rung 3 — is the query shape right for the retrieval mode?

Recall characteristics differ sharply, and a full sentence is usually the worst
input to either:

| Query | Typical outcome |
|---|---|
| full sentence, embed | diluted; misses exact terms |
| full sentence, keyword | frequency-ranked noise (long PDFs win) |
| **one specific term** | the actual note, ranked 1–2 |

If a single distinctive keyword finds the note and the sentence does not, the fix
is a **term-extraction step** ahead of retrieval, not a better prompt. A stopword
heuristic is usually not good enough (it prefers "learn" over "vim"); a short
local model call picks the subject far better and costs well under a second.

## Rung 4 — self-retrieval and near-duplicates

Two shapes that only appear once coverage is decent:

- **Self-retrieval** — the note being annotated is its own top hit, so the model
  reads the user's sentence back to them. Exclude the source note by name.
- **Backup / snapshot / `originals/` copies** — a duplicate embeds almost
  identically to the note it copies, so it competes head-to-head and can outrank
  the original. Exclude these outright; ranking them "cold" is not enough,
  because cold only affects *selection*, never *scoring*.

## Rung 5 — only now, cost

If results are right but slow, measure the parts before optimising:

- candidate discovery + file reads (usually dominant)
- vector scoring (pure-Python cosine is ~0.5s at 12k; numpy is ~500× faster)
- model latency (watch for a local runtime swapping models between calls —
  an outlier query is often a model reload, not your code)

A per-query cap is a symptom: caps exist because per-query work is expensive. Move
the work to a scheduled background index and the cap stops being a tradeoff.

## Verify like an audit

Per `.claude/rules/audits.md`, prove the fix in **both** directions before
believing it: re-run the queries that were wrong AND a set that was already
right, so a precision fix that quietly destroys recall is caught. Widening
coverage in particular tends to surface a *new* class of noise (duplicates), so
re-inspect the top hits rather than only the counts.

## When NOT to reach for this

- The surface throws an error or returns nothing at all — that's a bug
  (`eos-bug-audit`), not a relevance question.
- You already know the index is stale — just rebuild it.
- The retrieved notes are right and only the wording is poor — that is genuinely
  prompt work (CLAUDE.md rule 12), and this ladder will exonerate retrieval fast.

## Cross-references

- `.claude/rules/scoped-retrieval.md` — scope routing + the recency-based
  reconcile; rung 3's structured sibling.
- `.claude/rules/debugging.md` — root cause before fix; this is that discipline
  applied to retrieval.
- `.claude/rules/audits.md` — false-positive discipline for the verification step.
- `apps/public/core/search/indexer.py` — the background-index answer to rung 5.
