---
name: eos-index-drift-sweep
description: Sweep an index for one-liners that are directionally right but drop the operative constraint — the exclusion, threshold, precedence or condition that reverses a decision. Use when the user says "index drift", "lossy summaries", "sweep MEMORY.md", "why did you miss that constraint". NOT for stale/expired entries (use check_memory_rot.py) or for missing entries (that is coverage, not lossiness).
---

# Index Drift Sweep

Find index entries whose summary would make an agent **decide wrongly** if it
never opened the underlying file.

This is not a tidiness pass. The defect is specific and it is worse than a gap:
a missing entry causes a lookup, while a plausible-sounding entry causes
**confident wrong filtering with no signal that anything was missed**.

## The failure signature

Calibrate every sweep against this confirmed instance (2026-08-27):

| | |
|---|---|
| **File says** | corridor is North Shore + CBD · *"Explicitly out of scope — Western Sydney: Seven Hills, Huntingwood, Parramatta, Blacktown"* · *"Salary does not override the corridor"* |
| **Index said** | `hard geo constraint, remote-from-Sydney OK` |
| **What happened** | Seven Hills *is* Sydney, so the index-level rule **passed** an excluded employer. A whole session was spent researching it, including confirming its Huntingwood address and reading that as evidence of identity rather than as the disqualifier it was. |

The summary was **directionally right and operationally wrong**, and because it
read complete it *suppressed* opening the file.

## The one test

> **If an agent acted on this line alone and never opened the file, would it
> decide correctly?**

Not "is the summary shorter" — every summary is shorter. Four things must
survive compression; dropping any one produces this bug:

| Must survive | Typical loss |
|---|---|
| **Exclusions** | "X only" keeps X, drops "not Y" |
| **Thresholds** | keeps the rule, drops the number |
| **Precedence** | drops "A never overrides B" |
| **Conditions** | *the most common* — "X only if Y" keeps X, drops Y |

Also flag entries that are **actively wrong**, not merely thin: a line saying
"still unfixed" about something fixed, or prescribing a remedy the underlying
file records as having **failed**. Both were found in the first real sweep.

## Corpora this applies to

`MEMORY.md` is the obvious one, but the shape recurs wherever a short entry
stands in for a longer source:

- `MEMORY.md` / `MEMORY-archive.md` → the memory files
- `{vault}/10_Projects/emptyos/log/_next/_index.md` → per-track briefs
- `docs/DEFERRED-WORK.md` rows → the verdict that produced them
- A skill's `description` frontmatter → its own body
- CLAUDE.md's one-line summaries → the `.claude/rules/*.md` they point at

## Process

### 1. Name the signature, with a reference instance

Do not start sweeping until you can state the defect in one sentence *and* show
one confirmed example with both sides quoted. Agents given an abstract brief
return tidiness findings; agents given the geo example return real ones.

### 2. Scope and split the corpus

Split by **decision cost**, not alphabetically. For `MEMORY.md`:

- `feedback_*` + `user_*` — behavioural constraints that must fire
- `project_*` career/life/immigration/finance — expensive to get wrong
- everything else — usually informational, sweep last or not at all

Skip closed-verdict entries unless the one-liner **misstates the verdict**.

### 3. Delegate the sweep in parallel

One agent per slice. The prompt must carry: the signature, the reference
instance with both sides quoted, the one test, and this output contract —

```
- file name
- the current index one-liner (quoted VERBATIM)
- the operative constraint that is MISSING (quoted VERBATIM from the file)
- a concrete one-sentence scenario where acting on the one-liner goes wrong
- a proposed replacement carrying the operative half
```

Demand **precision over volume**, and forbid reporting anything that cannot be
quoted from *both* sides. That single constraint is what keeps the output
actionable — an unquoted claim is usually the agent paraphrasing a summary it
finds unsatisfying, which is not this defect.

### 4. Verify, rank, repair

Spot-check the quotes before applying — you are editing a durable record. Rank
by damage: irreversible consequences first (money, legal, deleted data, a
constraint whose window is already running), then confidently-wrong routing,
then rework.

Correctness beats brevity in the replacement. **The index is read every
session; the file is read almost never.**

### 5. Handle the size ceiling — tier, do not shave

Expect to hit a limit. `MEMORY.md` has a hard ~24.4KB read ceiling, and a hook
that asks for ≤17.1KB. **That ceiling is what manufactures the defect**: every
growth cycle squeezes the longest part of each line, which is always the
constraint. In the first sweep, 52 of 125 lines ended in a mid-sentence `…`.

Trimming references does not work — measured, the file got *bigger*. Tier
instead:

| Tier | Rule |
|---|---|
| Hard constraints / gates | **Never compress.** Full operative text |
| Live project state | One line, but keep the blocking condition |
| Closed verdicts, dev tooling, references | Roll up or move to the archive |

All size pressure is absorbed by tier 3. `MEMORY-archive.md` does not auto-load
but stays greppable, so **archive ≠ delete**.

### 6. Record the governing lesson once

Write or update `feedback_memory_index_must_carry_the_constraint` rather than
re-deriving the rule. Where a constraint is mechanically checkable, **name the
command in the line itself** — the geo line now ends with
`check_career_verdict.py "<co>" --location "<suburb>"`.

## Verify before finishing

```bash
cd "$(dirname "$(python -c 'import pathlib;print(pathlib.Path.home())')")" 2>/dev/null
python - <<'PY'
from pathlib import Path
import re
p = Path.home()/".claude"/"projects"/"D--emptyos"/"memory"/"MEMORY.md"
t = p.read_text(encoding="utf-8")
lines = [l for l in t.splitlines() if l.strip()]
links = re.findall(r"\]\(([A-Za-z0-9_\-]+\.md)\)", t)
missing = sorted({l for l in links if not (p.parent/l).exists()})
print(f"{len(lines)} entries, {p.stat().st_size} bytes, headroom ~{24400-p.stat().st_size}B")
print("malformed:", [l[:50] for l in lines if not l.startswith('- ')] or "none")
print("broken links:", missing or "none")
print("still truncated:", sum(1 for l in lines if l.rstrip().endswith(('…','...'))))
PY
```

Report entries, size, headroom, broken links, and the remaining truncation
count — the last one is the honest measure of what the sweep did *not* cover.

## When NOT to use this

- **Stale or expired entries** — dates that have passed, "awaiting decision" on
  something decided. That is `scripts/check_memory_rot.py`, a different defect.
- **Missing entries** — an unindexed memory is a coverage problem, and the rot
  scanner already reports it. Coverage was measured at 501/502; it is not the
  problem.
- **A summary that is merely terse** but carries its operative rule. Most are.
  Rewriting those burns budget the real constraints need.
- **Prose documents.** This targets an *index over a corpus*, where the entry
  substitutes for a file nobody opens. A rule file's own prose is not an index.

## Cross-references

- `feedback_memory_index_must_carry_the_constraint` — the governing lesson
- `scripts/check_memory_rot.py` — the sibling defect (staleness, not lossiness)
- `scripts/check_career_verdict.py` — the pattern of naming a checkable
  constraint's command inside the entry
- `.claude/rules/audits.md` — false-positive discipline; the "precision over
  volume" constraint above is that rule applied to a delegated sweep
- `.claude/rules/deep-research.md` — move 1 (*baseline first*) is why the
  reference instance is mandatory before sweeping
