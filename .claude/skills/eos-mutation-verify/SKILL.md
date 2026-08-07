---
name: eos-mutation-verify
description: Prove a new test actually pins the behaviour it claims, by reverting the fix and watching that test go red, then restoring and watching it go green. Use when you have just written a regression test, a both-direction checker pin, or a bug fix's test and are about to call it verified — a passing test is not evidence until you have seen it fail. NOT for exploratory test writing, NOT for a test that is already failing (that red IS the evidence), and NOT a replacement for running the suite.
---

# Mutation-Verify

A test that passes proves the code does *something*. It does not prove the test
would notice if the code stopped doing it. The only cheap way to know is to
break the fix on purpose and watch that specific test fail.

This is the mechanic behind the both-direction rule in
`.claude/rules/audits.md` and `.claude/skills/eos-graduate-audit` ("ship with
tests pinning both directions"). Those say *what* to ship; this says how to
prove you shipped it.

## When to Use

- Just wrote a test pinning a bug you fixed this session
- Just wrote a checker's "fires on the defect" case (audits.md calibration)
- A test passed on the first run and you have not yet seen it red
- Reviewing someone else's regression test and want to know it bites

## Why it earns its keep

Real cases from this repo, all of which looked green:

- A music-studio suite where a truncated class body re-parented **24 tests**
  into the preceding function. They were never collected; the enclosing test
  passed on its own two asserts. CI was green over 1,197 dead lines.
- A comfyui-performance session whose first cut had **2 tests passing against
  the old behaviour** — they asserted something both versions satisfied.
- A `check_plan_staleness` calibration pin that passed because the scanner's
  *bug* and the test's expectation happened to agree on a two-task fixture.

None of these are caught by writing more tests. They are caught by watching one
fail.

## The loop

For each claim the test makes:

1. **Back up the file you are about to break.** `git stash` and
   `git checkout --` are wrong here — a brand-new file is untracked, so git
   cannot restore it, and stashing drags in a parallel session's work. Copy to
   the scratchpad instead.
2. **Mutate narrowly** — revert exactly the fix, nothing else.
3. **Run only the affected test** with `-k`, so the signal is unambiguous.
4. **Assert it FAILED.** A test that still passes is not pinning what you think.
5. **Restore in the same command**, so an interrupted turn cannot leave a
   mutated file behind.
6. **Re-run the full file** at the end and confirm green.

```bash
SCRATCH="<your scratchpad dir>"
cp path/to/file.py "$SCRATCH/file.bak"

python - <<'PY'
import pathlib
p = pathlib.Path("path/to/file.py"); s = p.read_text(encoding="utf-8")
s = s.replace("<the fixed line>", "<the original buggy line>")   # narrow revert
p.write_text(s, encoding="utf-8")
PY

python -m pytest tests/test_x.py -q -k "the_specific_pin" 2>&1 | tail -4
cp "$SCRATCH/file.bak" path/to/file.py        # restore, same command
```

Then, once every mutation is done:

```bash
python -m pytest tests/test_x.py -q          # must be fully green again
git diff --stat path/to/file.py              # must show only your intended fix
```

## Reading the result

| Outcome | Meaning |
|---|---|
| **FAILED** on the mutation | The pin is real. This is what you want. |
| **PASSED** on the mutation | The test does not pin this behaviour. Rewrite it — usually it asserts something both versions satisfy. |
| **ERROR** (import/syntax) | The mutation was too broad. A file that will not import fails everything; it proves nothing about this test. Narrow it and redo. |
| Test not selected (`0 selected`) | The `-k` pattern missed. A vacuous run reads exactly like a pass in a summary line. |

That last row is the quiet one — always check the count, not just the exit code.

## Cost control

Mutation-verify every *claim*, not every assert. One mutation per distinct
behaviour the test file pins is enough; three or four on a new checker is
normal. If a mutation takes minutes to run (a slow perf pin, a GPU path), a
direct measurement of the old and new behaviour is acceptable evidence instead
— record the numbers rather than re-running the loop.

## When NOT to use

- **The test is already red.** That is the evidence; fix the code.
- **Exploratory or scaffolding tests** you have not decided to keep.
- **Tests over third-party behaviour** you cannot mutate.
- **A daemon-backed suite** (`test_sys_*`) where the mutation would have to
  land in a running process — those hit `:9000` and test the daemon's loaded
  modules, not your worktree. Pin the logic in a `test_unit_*` and mutate that.
- **Flaky tests.** A pass/fail flip may be the flake, not the pin. Stabilise
  first.

## Cross-references

- `.claude/rules/audits.md` — the both-direction rule and false-positive
  discipline this proves.
- `.claude/skills/eos-graduate-audit` — where a graduated checker's pins are
  required.
- `.claude/rules/testing.md` — the four test layers; this applies to the unit
  and system layers, not to visual baselines.
- `.claude/rules/debugging.md` — "write the failing test first" is the same
  idea arrived at from the other direction; when you do that, the red you
  already saw IS the mutation check.
