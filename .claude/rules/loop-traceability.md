---
paths:
  - "scripts/ui_walk_report.py"
  - "scripts/ui_walk_promote.py"
  - "emptyos/sdk/fix_queue.py"
  - "apps/extension/dev/dogfood-agent/**"
  - "apps/extension/dev/fix-agent/**"
---

# Loop Traceability — need → use case → milestone → step → finding → fix → receipt → learning

One stable identity chain connects a **real user need** to the **durable
learning** the loop produced from it. This rule is the contract for that chain
across the existing primitives — it adds *fields and one bridge*, never an
orchestrator or a parallel issue app. Testing stays **need-first**: a walk
starts from what the user must accomplish (a months-long project with
milestones), not from the apps EmptyOS happens to provide.

Origin: the 2026-07-17 engineering-loop audit
(`data/engineering-loop-audits/2026-07-17.md`) — evidence-backed manual walks
existed, the fix loop existed, but findings died in the report HTML unless a
person hand-rewrote them, and nothing carried a use-case identity end to end.

## The trace identity

| Field | Format / example | Meaning |
|---|---|---|
| `usecase_id` | slug — `riverside-bess-132kv` | the months-long user need (one per manual-walk `scenario.md`) |
| `milestone_id` | slug — `month-0-design-basis` | lifecycle checkpoint within the use case |
| `step_id` | `s<int>` — `s3` | step within the milestone (string, so frontmatter parsers never drop it as an int) |
| `walk_id` | walk dir name — `2026-07-17-1631` | which evidence-producing walk (anchors steplog/screenshot paths) |

**Trace key** (dedup identity for promoted findings):
`ui-walk::<usecase_id>::<milestone_id>::<step_id>` → filename via
`slug_for_key()`. All fields are **optional everywhere** — legacy artifacts
load unchanged; legacy rows derive identities deterministically
(scenario.md frontmatter → H1 slug → walk dir name; free-text `usecase` →
slug), so re-listing a legacy walk always yields the same keys.

## Where each identity lives (the artifact map)

| Artifact | Carries | How |
|---|---|---|
| Manual-walk `scenario.md` | `usecase_id`, `need`, `milestones:` list | block-style YAML frontmatter (see `eos-ui-walk` Step 1) |
| Steplog row (`steplog.jsonl`) | `usecase_id`, `milestone_id` (+ existing `step` int) | optional row fields; `ui_walk_report.py` renders them as a chip |
| Fix-prompt frontmatter | `source: ui-walk`, all four ids, `key` (trace key), `evidence`, `shot` | written by the promote bridge via `emptyos/sdk/fix_queue.py` |
| Fix-agent run / verify run | the whole set, verbatim | `_parse_prompt_meta` plucks them → stored as `verify_context` at queue time |
| Done ledger row | ids + `disposition` + `learning_outcome` | `FixPromptQueue.move_to_done(..., info=...)` → `done/_ledger.jsonl` |
| Loop receipt | `trace` sub-dict + `disposition` + `learning_outcome` | receipts projection (ctx → frontmatter → ledger fallback) |

## The triage bridge — promotion is a human act

`scripts/ui_walk_promote.py` is the only path from a manual-walk finding into
the fix loop. **Rendering a report never mutates any queue** — the report is a
terminal evidence artifact.

- `list` — promotable `fail | confusing | missing` rows + computed trace key +
  current state (`new / triaged:<action> / queued / closed:<disposition>`).
- `promote --step-key <k>` — writes the fix-prompt (kind mapping `fail→bug`,
  `confusing→confusing`, `missing→missing`) with trace + evidence (URL,
  screenshot, console lines, steplog path, the `need` line). Re-promoting a
  pending finding **updates the same file** (count bump), never duplicates. A
  finding already closed in the ledger is **refused** with its prior
  disposition; `--force-regression` reopens it as `<slug>-rN.md` with
  `regression_of:` set.
- `dismiss | defer | decline --step-key <k> --reason "..."` — sidecar-only
  (`<walk>/triage.jsonl`, append-only, last action wins); the finding never
  enters the queue. Per-walk on purpose: a NEW walk re-surfacing the same gap
  re-asks; the cross-walk guard is the ledger refusal.
- `close <filename> --disposition ... [--learning-outcome ...] [--evidence ...]`
  — stamps the close into the prompt frontmatter, then
  `move_to_done(disposition=...)` with the full ledger row.

Never promote in bulk without reading each row — the human judgment IS the
triage. Promoted items are queue-only (like trace-miner's), not dogfood
Issues-view entries.

## Feature gaps (`missing`) are not bugs

A `missing` finding says a needed capability/handoff/review-state/deliverable
step is absent — even when every visible control works. It closes honestly
through a **planning decision**, never a pretended fix:

- Lifecycle (reusing the gap-analysis registry vocabulary — no 5th one):
  pending in queue = `open`; close dispositions `planned | deferred |
  declined | shipped`, plus `dismissed` for noise/not-a-bug (distinct from
  `declined` = a real gap deliberately not built).
- A `deferred` close/triage **proposes** a `docs/DEFERRED-WORK.md` row
  (printed, never auto-written).
- Receipts project a dispositioned close as `closed-<disposition>` — never
  `verified`, never `failed`.
- The autonomous **drain skips `kind: missing` at selection**
  (`feature_gaps_skipped` in the drain summary), for the same reason it skips
  `ui-walk`: handing a gap to claude-cli asks it to design an absent
  capability unattended, then auto-reverts whatever it built when the persona
  scenario cannot verify something that was never specified. Only a human
  decides `planned / deferred / declined / shipped`. This was left implicit
  until 2026-08-16, when scheduling the drain turned it from a hazard a
  watching operator would catch into a nightly one.

## Verification for ui-walk-sourced fixes — manual attestation

There is no persona scenario to re-run and no syslog signature to watch, so:

- The autonomous **drain skips** `source: ui-walk` prompts at selection
  (`manual_only_skipped` in the drain summary). Without this the drain would
  verify against the default persona scenario and auto-revert good fixes.
- `POST /fix-agent/api/runs/{id}/verify` on a ui-walk run returns
  `manual_verify_required` **without mutating anything**.
- `POST /fix-agent/api/runs/{id}/attest` body
  `{target_fixed, evidence, note?}` records the human re-walk. A passing
  attestation **requires evidence validated end-to-end** — a re-walk steplog
  locator (`data/ui-walk/usecases/<new-walk>/steplog.jsonl::<trace key>`)
  whose file is a real steplog, whose key matches the run's trace identity,
  whose walk is FRESH (not the originating `walk_id`), and whose matching row
  passed (`pass`/`slow`). Row keys derive via the shared
  `steplog_row_trace` in `emptyos/sdk/fix_queue.py` (same derivation the
  promote bridge uses). The queue item is retired (`shipped`,
  `by=human-attest`, evidence in the ledger row) BEFORE `verified` is
  persisted — a failed queue close mutates nothing and the attestation stays
  retryable. Writes `verify_mode="manual-rewalk"` + `verify_detail`
  (projected into receipts as `gates.verify.evidence/note/attested_by`). A
  failing attestation records `verify_failed_reason` and leaves the item
  pending.

## The done ledger — bounded history

Every close appends one JSON line to `fix-prompts/done/_ledger.jsonl`
(`ts, filename, disposition`, + trace/learning/`by` when known; `by` ∈
`human | auto-verify | human-attest`). Append-only — corrections are new
rows, last row per filename wins. Receipts read it as a single bounded file,
so a completed/dismissed/deferred item stays inspectable without enumerating
`done/`. The ledger append is fail-soft: it never blocks a retire.

## Learning outcomes — closing the loop explicitly

Stamped at close time (frontmatter + ledger), projected by receipts:
`learning_outcome` ∈ `regression-test | conformance-case | lesson | rule |
design-principle | none` (+ `learning_ref` naming the artifact). Default
`none` — honest about closes that taught nothing durable. Upgrading later is
an appended ledger row, not a rewrite.

## The seven concerns — don't conflate them

| Concern | What it is | Home |
|---|---|---|
| **Need-first project use case** | the months-long real outcome + milestones (`usecase_id`) | manual-walk `scenario.md` (+ `eos-new-usecase` for dogfood scenarios) |
| **Manual evidence-backed UI walk** | human/agent-as-Kevin walk producing steplog + screenshots + report | `eos-ui-walk` skill → `data/ui-walk/usecases/<walk>/` |
| **Deterministic smoke walk** | Playwright preset walks, reproducible, **never feed the queue** | `dogfood-agent/ui_walk.py` |
| **Persona dogfood scenario** | LLM-persona run whose friction auto-enters the queue (deduped) | `dogfood-agent` runs + `behavior.py` |
| **Issues vs feature gaps** | `bug/confusing` = fix candidates; `missing` = gaps with a planning lifecycle | fix-prompt queue + dispositions (this rule) |
| **Fixes + verification** | fix-agent attempts, gates, merge, scenario / passive-syslog / manual-rewalk verify, revert | `fix-agent` + `.claude/rules/test-fix-verify-loop.md` |
| **Receipts + learning extraction** | read-only projection answering "what produced this, what did we decide, what did we learn" | `dogfood-agent/receipts.py` (flag `feature.loop-receipts.enabled`) |

## When NOT to use this

- Don't stamp trace ids on **dogfood persona scenarios** — their identity is
  the persona/scenario slug + friction key; forcing walk ids onto them adds
  noise. The chain applies to *manual, need-first* walks.
- Don't auto-promote. If a walk produces 20 findings, that's 20 human reads,
  or a decision to leave them as report evidence.
- Don't build a gaps registry app or an issue tracker — the queue +
  dispositions + ledger are the whole mechanism until they demonstrably
  can't carry the load.

## Cross-references

- `.claude/rules/test-fix-verify-loop.md` — the fix-prompt contract this
  extends (trace keys are optional frontmatter there).
- `.claude/rules/self-audit-loops.md` + `emptyos/sdk/loops.py` — the loop
  registry; receipts are the proof objects.
- `.claude/skills/eos-ui-walk/SKILL.md` — Step 1 (scenario frontmatter),
  Step 5b (triage bridge).
- `.claude/skills/eos-app-gap-analysis/SKILL.md` — the disposition vocabulary
  this reuses.
- `docs/DEFERRED-WORK.md` — where `deferred` gaps propose rows.
