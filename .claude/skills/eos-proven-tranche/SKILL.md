---
name: eos-proven-tranche
description: Drive ONE item from a known backlog end to end — fix at root cause, pin it with a mutation-proven test, hand the staged diff to a hostile reviewer with a brief written for that chunk, dispose of every finding, write the receipt, commit, then stop and report. Use when working a list of known defects one at a time — a divergence table, a gap registry, an audit's findings, a review's numbered list. Say "work the next divergence", "close row N", "next item from the audit", "proven tranche". NOT for exploratory work with no named target, NOT for a one-line fix (the ceremony costs more than the fix), and NOT for the autonomous daemon-driven loop (that is eos-fix-drain / test-fix-verify-loop).
---

# Proven Tranche

One item. Fixed at the root, proven by a test that has been **seen red**,
reviewed by somebody who assumes it is wrong, committed with every finding
disposed of — and then **stop and report**.

It exists because the pieces already existed and the sequencing did not.
`eos-simplify`, `eos-adversarial-review` and `eos-mutation-verify` each do one
part; assembling them by hand every time is how a step gets skipped on the
tranche that needed it. Written after five consecutive tranches against a spec
pack's divergence table, where the hostile review found a real defect in *my
own work* every single time.

## The loop

1. **Read the item, then fact-check it.** The backlog row is a claim, not a
   specification. Open the requirement it cites and the code it describes.
2. **Resolve the contradiction before writing code.** Specs disagree with
   themselves. When they do, say which side you took and why, in the commit.
3. **Fix at the root cause** (`.claude/rules/debugging.md`). One item's worth.
4. **Pin it**, then **mutation-verify** — `.claude/skills/eos-mutation-verify`,
   whose `run_mutations.py` takes a data file. Do not re-derive the loop.
5. **Update the backlog** in the same change: strike the row only if it is
   genuinely closed, and **add a row** if the fix made the spec promise
   something now unbuilt.
6. **Stage explicitly**, never `git add -A` (`.claude/rules/environment.md`).
7. **Hostile review** of the staged diff — `.claude/skills/eos-adversarial-review`,
   with the brief below.
8. **Dispose of every finding**: fixed, or waived with a written reason.
9. **Receipt** (`scripts/review_receipt.py write`, `--repo` for another work
   tree), then **commit**.
10. **Stop. Report.** A remaining-item count is never a reason to continue
    (CLAUDE.md § Autonomous Loops).

Run `/eos-simplify` before step 7 when the tranche is large, so the reviewer
spends its attention on substance rather than lint.

## The review brief is the leverage

A generic "review this" gets a generic answer. Every real finding in the five
tranches came from a brief that named four things:

- **The constraint documents for this chunk**, by path. The reviewer cannot
  guess them, and the highest-value findings are contradictions with a
  document it would never have opened.
- **The decisions already taken**, so it challenges the reasoning rather than
  re-opening the question.
- **What this change claims to close**, so it can check the claim rather than
  the diff.
- **Specific hunts.** These four earned their place:
  - *"Where does this still NOT happen?"* Trace every path the value takes.
    The most expensive finding of the session was a conversion that covered
    two of four boundaries while the commit message said all of them.
  - *"Construct the broken implementation that still passes each new test."*
    This is how a test that reads the shape of a call, rather than its effect,
    gets caught.
  - *"Find a mutation that survives."* Ask for one more than you ran.
  - *"Did this quietly decide something that was left open?"* A fix reaches
    for the nearest reading of an unsettled question and ships it as fact.

Always forbid mutation explicitly: read-only analysis plus read-only scanners,
no edits, no git state. A reviewer that starts fixing stops reviewing.

## Failure modes this loop is built against

Each was measured, not imagined.

- **The fix covers less than the commit message says.** State the boundaries
  you actually crossed, and let the reviewer look for the ones you did not.
- **A test that passes with the feature dead.** Prefer behaviour to shape. If
  the logic sits where it cannot be tested (an app module a daemon-free suite
  must not import), *move the logic*, and leave only the join behind.
- **Overreach dressed as rigour.** "A total is withheld when a row refuses"
  became "withhold three numbers", one of which was missing nothing. Ask what
  the requirement's condition actually is, not what it is adjacent to.
- **The doc claim wider than the code.** "Every consumer downstream" is a
  universal; one uncovered path falsifies it. Name the covered set.
- **A patch script that half-applies.** A per-file writer that asserts mid-run
  leaves earlier files written: re-running then fails on anchors that already
  moved. Check what landed before re-running. Nested heredocs eat escapes —
  prefer the `Edit` tool, or `Write` the script then run it
  (`[[feedback_edit_tool_not_heredoc_patch]]`).
- **Regenerating a derived document by hand.** If a table is generated, run
  its generator (`gen_assurance_tables.py`, `gen13`-style scripts) and let the
  drift check tell you.

## When NOT to use it

- **A one-line fix, a typo, a revert.** Use `--waive-all` with the reason.
- **No named item.** If the target is "make it better", this has no
  termination condition — run an audit first.
- **A findings list you have not triaged.** False positives get fixed as
  eagerly as real ones (`.claude/rules/audits.md`).
- **Work that needs a decision you do not have.** Surface it and stop;
  deciding it inside a tranche is how it ships undiscussed.

## Cross-references

- `.claude/skills/eos-adversarial-review/SKILL.md` — step 7, and the receipt
  gate that opens the commit.
- `.claude/skills/eos-mutation-verify/SKILL.md` — step 4; its runner reports
  `MISTARGETED`, which a hand-run cannot.
- `.claude/skills/eos-simplify/SKILL.md` — the convention pass before review.
- `.claude/rules/gate-driven-fix-loop.md` — the sibling for *scanner* findings,
  where "done" is an executable gate rather than a list.
- `.claude/rules/test-fix-verify-loop.md` — the autonomous, daemon-wired
  cousin. Do not merge them: that one drives an LLM in a worktree from a
  friction queue; this one is you, one item, with a human reading the report.
- `.claude/rules/debugging.md` — root cause before fix.
- CLAUDE.md § EmptyOS Workflow — where this sits in the standard loop.
