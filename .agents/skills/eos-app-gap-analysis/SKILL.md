---
name: eos-app-gap-analysis
description: Maintain the standing per-app market gap analysis — compare an EmptyOS app against 3-5 front-tier market alternatives (task→Todoist, expense→YNAB, fix-agent→SWE-agent…), write/update its gap note in the vault registry, and track every gap's lifecycle (open→shipped/deferred/declined) across re-runs. Use when the user says "gap analysis", "compare <app> to the market", "what are competitors doing", "app benchmark vs alternatives", "/eos-app-gap-analysis", or "continue gap coverage". NOT for internal quality scoring without a market lens (the app_optimizer_scan/dev-app-optimizer sprint covers that), AI-nativeness (use eos-ai-native-audit), evaluating ONE external repo for borrowing (use eos-repo-extract), or building the suggested features (separate explicit sessions).
---

# EmptyOS App Gap Analysis

A standing registry: every app compared against the current front-tier market alternatives, with suggested changes and gap lifecycle tracked across re-runs. The deterministic substrate is `scripts/check_gap_freshness.py` (coverage/staleness) + `scripts/app_optimizer_scan.py` (the internal 0-120 score — **read it, never re-derive it**); this skill is the judgment layer: market research, honest benchmarking, gap triage.

**Registry:** `{vault}/30_Resources/EmptyOS/gap-analysis/` — one note per app + `_index.md` coverage dashboard. **Ledger lens:** `gap` (`scripts/insights_ledger.py`). **Rubric:** the competitive-benchmark discipline from `skills/dev-app-optimizer/SKILL.md` Phase 2 (don't duplicate it here — reference it).

## Invocation

```
/eos-app-gap-analysis <app-id>     # (re-)analyze one app
/eos-app-gap-analysis batch 5      # next 5 by priority (unanalyzed flagships first)
/eos-app-gap-analysis continue     # default — next 5-8 stalest/unanalyzed
```

Keep batches to **5-8 apps per session** — each app needs real web research; more than that and the comparisons degrade to guesses.

## When NOT to run

- Internal quality sprint with no market lens → `dev-app-optimizer` / `app_optimizer_scan.py`.
- "Is this ONE external repo worth borrowing from" → `/eos-repo-extract` (a gap note may *cite* a borrow verdict, never re-derive one).
- AI-integration coverage → `eos-ai-native-audit`.
- Building a suggested change → separate explicit session; this skill only proposes.

## The registry contract (note format)

One note per app at `{vault}/30_Resources/EmptyOS/gap-analysis/<app-id>.md`:

```markdown
---
tags:
  - gap-analysis
app: expense
track: public/standard
market: consumer-finance
competitors:
  - YNAB
  - Monarch Money
  - PocketSmith
score: 64
grade: B
last_reviewed: 2026-07-10
open_gaps: 3
author: ai
---
# expense — gap analysis

One-line: what the app is, in market terms.

## Competitors
| Alternative | Tier | Why front-tier | Source |
|---|---|---|---|
| YNAB | front-tier | category leader, zero-based budgeting | https://… |

## Benchmark
| vs | Our score | Their score | Gap | Key missing |
|---|---|---|---|---|
| YNAB | 64/120 | ~100 | -36% | bank feeds, multi-currency |

## Gaps
| ID | Gap | Severity | vs | Suggested change | Status |
|---|---|---|---|---|---|
| expense-bank-import | No bank/CSV feed import | high | YNAB | CSV importer first, feeds later | open |

## Strengths (where EmptyOS wins)
- vault-native plain-markdown data, local-first, reactor ripples…

## Change Log
- 2026-07-10 — initial analysis vs YNAB/Monarch/PocketSmith (score 64, 3 gaps)
```

Contract rules:

- **Frontmatter is flat, tags block-style** (CLAUDE.md gotchas). `score` comes from `scorecard-latest.json`; **`grade` is derived from `score`** by `check_gap_freshness.py` (`GRADE_BANDS`) — write your best guess and let `--write-index` normalise it, never hand-tune it to feel right; `open_gaps` = count of `open` + `planned` rows; `market` is a category slug, or `none` for apps with genuinely no external analogue.
- **Gap IDs are stable slugs** (`<app>-<slug>`) — never renumber or rephrase an ID; re-runs and the ledger match on them.
- **Status vocab:** `open / planned / deferred / shipped / declined / regressed`. `planned` = user said build it; `deferred` = has a DEFERRED-WORK row; `declined` = user or posture says no (keep the row — it prevents re-proposing).
- **Every competitor claim carries a source URL** (`feedback_scan_results_with_links`). No URL → the claim is graded `inferred` and must say so.
- **`market: none` notes** get a one-paragraph rationale instead of a forced comparison (e.g. pure EmptyOS glue with no external product category). Be honest but reluctant — most "internal" apps DO have analogues (fix-agent→SWE-agent/OpenHands, store→Obsidian community plugins, rooms→Slack+bots, sandbox→devcontainers).
- Competitor brand names are fine here — Rule 14 governs user-facing UI text, not vault analysis notes.

## The pass

### Phase 0 — Baseline (always first)

```bash
python scripts/check_gap_freshness.py          # coverage: analyzed / stale / missing
python scripts/insights_ledger.py scorecard gap
```

Read `_index.md` if it exists. A gap **recurring** across runs without being acted on is a stronger signal than a fresh one (deep-research move 1). Reconcile the scorecard: acted-on → note it, still-relevant → carry forward, stale → drop.

### Phase 1 — Pick targets

From the arg; else stalest/unanalyzed first, **flagship/daily-use apps before labs/chrome**. Wheel posture (Rule 16): when picking among equals, prefer apps serving thin dimensions over more intellectual/occupational tooling.

### Phase 2 — Facts (never score from memory)

Per app:
- `manifest.toml` (description, capabilities, provides.*, contributes.*) + a skim of `app.py` route surface and `pages/`.
- Internal score + dimension breakdown: the app's row in `{vault}/30_Resources/EmptyOS/app-optimizer/scorecard-latest.json` (run `python scripts/app_optimizer_scan.py` first if absent/old). This is the `counted`-grade number — do not hand-score.
- Prior gap note, if any: the current Gaps table is the diff base.

### Phase 3 — Market research

WebSearch/WebFetch the **current** front-tier alternatives (3-5). "Front-tier" = category leaders a reviewer in 2026 would actually name, not historic defaults. For each: 2-4 signature capabilities EmptyOS lacks, and what EmptyOS has that they don't (local-first, vault-native, event ripples, no subscription — the Strengths section is mandatory; a gap analysis that only lists losses misreads the product). Every claim row gets a source URL.

Before proposing any gap whose fix smells like "adopt/borrow tool X": `python scripts/check_borrow_verdict.py <name>` — a closed verdict is cited, never re-litigated.

### Phase 4 — Write / update the note

- New gaps append with fresh stable IDs.
- **Prior gap status flips require re-verification against current code** — `shipped` only when the feature verifiably exists now (grep/read it); `regressed` when a previously-shipped one is gone. Don't trust the old note as ground truth (three-natures discipline: the note is a dated appearance, the code is the source).
- One dated Change Log line per run summarizing the delta (`+2 gaps, 1 shipped (expense-csv), score 64→71`).
- Idempotent: re-running on an unchanged app changes only `last_reviewed` + one Change Log line.

### Phase 5 — Route outcomes (proposals only, never auto-build)

- **Build-now candidates** → ranked list in the chat summary; Impact×Effort per `dev-app-optimizer` Phase 3. The user picks; building is a separate session.
- **Substantive deferred gaps** → propose a `docs/DEFERRED-WORK.md` row (trigger + reference alternative + source link). Propose the row text; add it only with user approval in-session.
- **Declined** → status `declined` in the note with a one-line why (posture, wheel, closed verdict).

### Phase 6 — Record + index

```bash
python scripts/insights_ledger.py record gap "<app>: <gap one-liner>" ...
```

Record **every** `open` gap, not just the build-now shortlist — the note and the ledger must reconcile, and a silently-capped record reads as "that's all there is" on the next pass (`.claude/rules/audits.md` § no silent caps). Rank in the chat report instead.

(or `--from <note>` if the note carries a `## Suggested next steps` section). Then regenerate the coverage index deterministically:

```bash
python scripts/check_gap_freshness.py --write-index
```

which rewrites `{vault}/30_Resources/EmptyOS/gap-analysis/_index.md` — analyzed apps first (`app | track | market | score | open gaps | last_reviewed`), then the unanalyzed todo tail, with header coverage stats.

### Phase 7 — Report to chat

Headline numbers (coverage, batch scores), top 3-5 build-now candidates across the batch, the note paths. Long output stays in the notes, never inline. Grade evidence: benchmark rows sourced from a URL are `read-verified`; unsourced impressions are `inferred` and say so.

## Guardrails

- **Don't inflate scores** — an honest 55% vs Todoist beats a fake 85% (`dev-app-optimizer`: the gap you hide is the gap you keep).
- **Suggested changes respect EmptyOS's posture** — local-first, vault-native, "with you not for you". "Add a cloud sync subscription tier" is a competitor feature, not a gap.
- **Never auto-edit DEFERRED-WORK, never auto-build, never flip a ledger status silently** — propose; the human decides (`.claude/rules/proposed-action.md`).
- Full 131-app coverage accrues across sessions via `continue` — it is deliberately not a single-run goal.

## See also

- `scripts/check_gap_freshness.py` — coverage/staleness substrate (preflight `--scope apps`, advisory).
- `scripts/app_optimizer_scan.py` + `skills/dev-app-optimizer/SKILL.md` — the internal score + benchmark rubric this reuses.
- `scripts/insights_ledger.py` (lens `gap`) — the recheck loop; `eos-insights` Step 0 reads it too.
- `docs/DEFERRED-WORK.md` / `docs/OPEN-SOURCE-BORROWING-PLAN.md` — where deferred gaps and borrow verdicts live.
- `.claude/rules/self-audit-loops.md` — the umbrella pattern this registers under.
- `.claude/rules/deep-research.md` — baseline → deep-read → refute → grade; applied to market claims here.
