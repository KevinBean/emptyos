---
name: eos-failure-attribution
description: Decide whether a test that is red AFTER your change was actually broken BY it, by restoring the pre-change state and re-running the same test. Use when a suite goes red during a refactor, a split, or a decomposition and you need to know what you own before you fix anything. NOT for a test you just wrote (use eos-mutation-verify — that proves a new test bites), NOT for a green suite, and never a licence to dismiss a red as "pre-existing" without running the comparison.
---

# Failure Attribution

You changed something. A test is red. Two possibilities, and they lead to
opposite actions:

- **Yours** — fix the code, or fix the assertion if the change made it stale.
- **Not yours** — report it and move on; fixing another track's red inside your
  diff widens the change and steals their finding.

Guessing here is expensive in both directions. Assuming "mine" burns a session
chasing someone else's bug. Assuming "pre-existing" ships a regression with a
confident sentence attached.

The answer costs about two minutes: **put the tree back the way it was and run
the same test.**

## When to Use

- A refactor, decomposition, or page split turned a suite red
- You are about to write "this failure is pre-existing" in a commit message
- A test fails in a batch run and you do not know whether it failed before
- Reviewing an agent's or another session's changeset that has red tests

## Not this skill

- **A test you just wrote** → `eos-mutation-verify`. That proves a *new* test
  bites. This decides who *owns* an *existing* red.
- **Nothing is red** → nothing to attribute.
- **You already know** because the failure names your symbol in its traceback.
  Read the traceback first; it is often free.

## The loop

```
1. Snapshot the changed files                 cp <file> $SCRATCH/<file>.mine
2. Restore the pre-change state               git stash / cp <backup> / git checkout -- <file>
   (for a new file: move it aside, do not delete)
3. Run the SAME test node id, ALONE           python -m pytest "<file>::<Class>::<test>" -q
4. Restore your change                        cp $SCRATCH/<file>.mine <file>
5. Run it ALONE again
6. Compare
```

| Before | After | Verdict |
|---|---|---|
| pass | fail | **yours** — fix it |
| fail | fail | **pre-existing** — report, do not fix here |
| pass | pass | a batch-order or state artifact, not a real red (see below) |

Restore before you finish. A backup left in place is a silent revert of your
own fix — verify with `git diff` that your change is still there.

## The trap that makes this skill worth having

**Run each test ALONE, by node id, in both states.** Not the file, not the
class, not the batch.

Measured 2026-09-03 on a page split: two UI tests failed in a batch run, and
*which* two alternated between runs. That reads exactly like flakiness, and it
nearly got both dismissed. Run alone in both states, the signal was
unambiguous — **both passed before and both failed after.** The split really
had broken them; the alternation was shared vault state between the two tests,
not noise.

The inverse happened in the same session: an API test failed alone and looked
like mine, until the pre-change run failed identically. Another session landed
the real fix an hour later.

So a batch run answers a different question than the one you are asking.

## Two more things that corrupt the comparison

- **State accumulated between runs.** Suites that write real data (a vault
  note, a worklog day) can make run 3 differ from run 1 for reasons unrelated
  to your diff. If before/after disagree, re-run the *before* case again
  **now** rather than trusting a result from twenty minutes ago — the
  environment moved.
- **A partial restore.** Restoring `index.html` while leaving the extracted
  `.js` in place is neither state. Move every file the change touched,
  including new ones.

## When the verdict is "pre-existing"

Say so with the evidence, not the assertion: *"fails with the original file
too, and my diff touches no backend file."* Then leave it. Record it in the
session brief or hand it to the owning track — a pre-existing red that nobody
writes down is one the next session re-diagnoses from scratch.

## Cross-references

- `.claude/skills/eos-mutation-verify/SKILL.md` — the inverse direction: prove
  a *new* test would go red without the fix.
- `.claude/rules/audits.md` § Failure mode 3 — green that proves nothing; this
  is the red-side companion.
- `.claude/rules/debugging.md` — root cause before fix. Attribution comes
  first: you cannot root-cause a bug you did not cause.
- `.claude/rules/environment.md` § Parallel-session staging — why another
  session's red is genuinely not yours to fix inside your diff.
