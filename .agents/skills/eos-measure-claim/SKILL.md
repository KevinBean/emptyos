---
name: eos-measure-claim
description: Settle a performance, capacity, or hardware claim by measuring it on this machine instead of reasoning about it — and by measuring the code path the system actually uses, from a clean baseline. Use when the user asks "would X be faster", "should I buy/upgrade Y", "is Z the bottleneck", "why is this slow", or when you are about to recommend a purchase, a config change, or an optimisation on the strength of an argument rather than a number. NOT a correctness-bug hunt (use eos-bug-audit), NOT a code-quality pass (use eos-simplify), NOT an external-repo evaluation (use eos-repo-extract).
---

# Measure the Claim

A claim about speed, capacity, or hardware is settled by a number from **this
machine**, not by an argument. This skill exists because the arguments are
persuasive and wrong at a high rate, and the failure is invisible: a confident
recommendation reads exactly like a correct one.

Origin: a 2026-07-25 session that evaluated an RTX 5090 purchase. Nine
pro-purchase angles were argued; every one that got measured came back negative,
and **three separate errors were made along the way — all in the same direction
(favouring the purchase)**. Each dissolved the moment something was actually run.
The three failure modes below are those errors, generalised.

## When to run it

- The user asks whether hardware, a config change, or an optimisation would help
- You are about to recommend spending money, or say "X is the bottleneck"
- A vendor/community/benchmark figure is being used as the basis for a decision
- Something is "slow" and the cause is assumed rather than located

## When NOT to

- The measurement costs more than the decision is worth (a $20 call, a one-line
  revert). Ship it and watch.
- Nothing on this machine can produce the number (a GPU you don't own) — then say
  so plainly and price the *rental* of an hour's evidence instead of guessing.
- The claim is about correctness, not performance. Different skill.
- The claim is about a *capability* — "model/LoRA/technique X can do Y". That is a
  borrow decision, so it runs `eos-repo-extract` (step 1b), which reuses failure
  mode 1 below but ends in a logged verdict rather than a number.

## The three failure modes, in the order they bite

### 1. You measured a code path the system does not use

**The error:** the ollama provider was diagnosed as "verbose, times out, falls
through to paid cloud" from a raw `POST /api/generate` that took 27 s and emitted
3,061 tokens. EmptyOS never calls that path — it forces `think: false`, where the
same prompt takes **1.4 s and 73 tokens**. A 13× "win" was proposed for a bug that
did not exist.

**The discipline:** before timing anything, find the call site. Grep for the
function the app actually invokes and read what it passes. Convenience APIs
(`/api/generate`, a bare CLI, a direct HTTP call) are rarely what the daemon uses.

```bash
grep -rn "def execute\|_execute_\|async def think" <provider-or-plugin>
grep -rn "<the-thing>(" apps/ plugins/ --include=*.py | head
```

If you cannot name the exact function the system calls, you are not ready to time it.

### 2. You measured from a dirty baseline

**The error:** klein-4b took **280.6 s** and klein-9b **27.7 s**, "proving" the 9B
model was faster. The card was at 15.16/16 GiB when 4B ran (leftover LLM weights
from an earlier test in the same session) and had drained to 11.32 GiB by the time
9B ran. From a clean floor the real numbers are **12.6 s and ~22 s** — the
opposite ordering.

**The discipline:** record the starting state, reset it before every run, and
alternate order. If a result inverts your expectation, suspect the harness before
the hypothesis.

```
free/reset → record baseline → run A → free/reset → run B → free/reset → run B → run A
```

The 22× gap between the dirty and clean 4B runs turned out to be the session's most
valuable finding. **Contamination is data** — when a run is anomalous, measure the
contamination rather than discarding it.

### 3. You quoted a summary instead of reading the source

**The error:** "Klein 9B is downloaded but not wired into the plugin" — read from
the one-line hook in `MEMORY.md`. The note it points to says **WIRED, with 11
tests**, and the feature had shipped in the most recent commit. The stale index
line was then repeated into two other documents.

**The discipline:** an index line, a docstring summary, or your own earlier
message is a pointer, not evidence. Open the file. Check `git log`. This is the
same rule CLAUDE.md applies to repo evaluation ("ground every claim against the
real source") and `.claude/rules/time-dimension.md` ("read the past before acting").

## The loop

1. **State the claim as a number.** "A 5090 would help" is unmeasurable. "It would
   raise the LTX clip cap from 4.84 s to ~6 s" is testable — and immediately
   invites the question that killed it: *how long are the scenes actually?*
2. **Find the ground truth already on disk** before generating any. Real
   storyboards, `billing/api/usage`, prior bench results in `data/apps/model-bench/`,
   `nvidia-smi`. Most claims die here, for free.
3. **Locate the real code path** (failure mode 1).
4. **Measure from a clean baseline, alternating order** (failure mode 2).
5. **Re-measure anything surprising** before reporting it. A result that flatters
   the conclusion you were leaning toward deserves a second run, not a victory lap.
6. **Report the number, the method, and the caveat.** Sample size, confounds, what
   you could not measure.
7. **Correct the record loudly** when you were wrong, and say which direction the
   error ran. An error that favoured your conclusion is worth more than one that
   opposed it.

## What "measured" means here

| ✗ not a measurement | ✓ a measurement |
|---|---|
| a vendor benchmark | a run on this box |
| "should be ~2× faster" | 4.87 s → 2.46 s, n=2, same input |
| one run | clean-baseline runs, alternated order |
| a summary line in an index | the file, opened |
| wall-clock of a mixed workload | the isolated stage, with the baseline recorded |

## Reporting

Lead with the number and the method. State the caveat in the same breath —
sample size, what was confounded, what you could not test. If the measurement
contradicts something you said earlier in the session, correct it before
continuing rather than at the end.

Record the verdict where it will be found again (`.claude/rules/`, a memory note,
or a `docs/` entry with reopen triggers), because the same claim returns in six
months with the same arguments. Include the mistakes — they are the part that
stops a future session repeating them.

## Cross-references

- `.claude/rules/audits.md` — false-positive discipline; calibrate on known-healthy
  cases before trusting a signal
- `.claude/rules/deep-research.md` — the reasoning discipline this is the
  measurement arm of (baseline → gap-pick → deep-read → refute → grade)
- `.claude/rules/debugging.md` — root cause before fix; same "find the real path"
  requirement
- `.claude/rules/time-dimension.md` — read the past before acting (failure mode 3)
- `project_rtx5090_verdict` (memory) — the worked example, including all three errors
