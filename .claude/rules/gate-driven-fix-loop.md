---
paths:
  - "check_done.sh"
  - ".loop-regression-tests.txt"
  - ".STOP"
---

# Gate-Driven Fix Loop — an executable definition of "done"

An operator loop for working a backlog of **hard-gate scanner findings** to
zero. It is the general form of the ad-hoc "fix the preflight failures" pass,
and it exists because that pass has one recurring failure: without an
executable definition of done, it stops when the operator gets tired, and
nothing records which findings were fixed versus quietly skipped.

Registered in `emptyos/sdk/loops.py` as `gate-driven-fix`
(`eos loops show gate-driven-fix`). Family: self-audit. Sibling of `preflight`
(which surfaces the findings) — this loop is what *closes* them.

## The protocol

Per iteration, in this order:

1. **Check `.STOP` first.** A file named `.STOP` in the repo root halts the
   loop immediately with a summary. It is checked at the top of every pass,
   never only at the end, so a human can stop a running loop without killing
   the session mid-commit.
2. **Pick one finding** from the gate output.
3. **Check ownership before touching anything** (see below).
4. **Fix at the root cause**, not the symptom (`.claude/rules/debugging.md`).
5. **Pin it with a regression test red-proven in both directions**, then
   register the node id in `.loop-regression-tests.txt`.
6. **Verify the user-visible behaviour by executing it** — a browser walk when
   there is a web surface, the CLI when the change is a CLI, the checker's own
   count when it is neither. Never from reading the diff.
7. **Commit, citing the test name**, staging only your own files.
8. **Re-run `./check_done.sh`.**

Terminate on: gate exit 0, a stated iteration cap, or `.STOP`. A remaining-item
count is never a termination condition (CLAUDE.md § Autonomous Loops).

## The gate has two halves, and both are load-bearing

`check_done.sh` exits 0 only when **(1)** every hard-gate scanner in scope
passes **and** **(2)** every regression test the loop has added still passes.

Half (2) is the anti-cheat. Scanners alone can be satisfied by deleting the
offending code, silencing the check, or widening an allowlist — all of which
turn the gate green while making the system worse. Requiring the accumulated
pins to stay green means every fix has to stay proven.

Half (1) covers **hard-gate scanners only**. Advisory scanners are excluded
deliberately: `.claude/rules/audits.md` is explicit that an ambiguous signal
must never gate, and the advisory set carries known false positives. Including
them makes "done" unreachable and pushes the operator toward gaming warnings
instead of fixing defects.

## Three mistakes this loop is built to prevent

**Capture the checker's exit code directly, never through a pipe.** In a
pipeline `$?` reports the LAST command, so `preflight | tail` reports `tail`'s
status. Measured on the first run of this loop: it printed `EXIT=0` while two
hard gates were red, which would have declared the objective met at iteration
zero.

**Red-prove each pin, once per failure shape.** A test that passes on a healthy
tree has proved nothing until you have watched it fail for the reason you
claim (`.claude/rules/audits.md` § "green because it checks nothing"). This is
not theoretical here: a mirror-drift guard written during this loop skipped any
file that was *dirty* — and drift makes a file dirty by definition, so it
skipped precisely the case it existed to catch and passed with deliberate drift
injected. It was caught by injecting the drift, not by re-reading the code.

**Tune the heuristic, don't "fix" the tree.** The same guard compared raw
bytes and reported two false positives, because `.gitattributes` normalises
some paths while `core.autocrlf` rewrites others — identical text, different
newline bytes. The fix was to normalise the comparison. A check that fires on
a healthy target is a checker bug.

## Ownership is the standing limit, not budget

The loop runs against a shared working tree. A gate can be red because of work
that is **not yours** — another session's uncommitted files, or drift that
needs content-merge judgment. Those are reported, never forced.

Before touching a file the gate names:

- `git status --short -- <path>` — dirty means another session may be mid-edit.
- Compare **committed** state (`git show HEAD:<path>`) to separate pre-existing
  drift, which you own, from in-flight drift, which you do not.
- Never run a **whole-store regenerator** (`eos bus import`,
  `generate_skills_doc.py`) without measuring its blast radius first. During
  this loop `eos bus import` touched 29 files and pulled another session's
  in-flight `CLAUDE.md` and `.claude/rules/` edits into the mirror; the correct
  fix was to copy the 3 files that were actually stale. Likewise a docs
  regeneration was correct in isolation but would have published a catalog
  entry for an untracked skill, so it was reverted and reported instead.
- A one-directional diff is safe to sync; a diff where **both** sides hold
  unique content is not. Beware the crude classifier: a line *edited* on one
  side appears as one deletion plus one addition and reads as two-way
  divergence when it is not. Read the lines before deciding.

## When NOT to use it

- **No executable gate exists.** If "done" cannot be expressed as a command
  that exits 0, this loop has nothing to terminate on — write the checker
  first, or run the work as a normal session.
- **The backlog is feature gaps, not defects.** "Fails before, passes after" is
  honest for a bug and dishonest for a feature build; and autonomously
  designing features without the user is out of scope.
- **One obvious fix.** The ceremony costs more than the fix.

## Cross-references

- `.claude/rules/audits.md` — false-positive discipline, and the
  green-because-it-checks-nothing failure mode this loop red-proves against.
- `.claude/rules/self-audit-loops.md` — the umbrella; `preflight` is the
  finding source this loop consumes.
- `.claude/rules/debugging.md` — root cause before fix.
- `.claude/rules/environment.md` § Parallel-session staging — the ownership
  rules above.
- `.claude/rules/test-fix-verify-loop.md` — the autonomous, daemon-wired
  sibling. That one drives an LLM in a worktree against a friction queue; this
  one is operator-driven against scanner findings. Do not merge them.
