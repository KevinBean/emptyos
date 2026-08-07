---
name: eos-graduate-audit
description: Turn a one-off heuristic into a permanent, low-false-positive checker registered in scripts/preflight.py — measure the FP rate, tune or narrow the signal, triage every hit on the live tree, then ship it with tests that pin both "fires on the real regression" and "silent on healthy code". Use when an ad-hoc grep or scan just found a real bug and you want it to keep finding it, or when the user says "graduate this audit", "make this a check", "add a preflight check", "stop this from recurring", or the same class of defect has broken the build more than once. NOT for running an existing checker (just run it), NOT for a one-time exploratory scan you will never repeat (that is research — print it and move on), and NOT for judgment-shaped reviews that need a human read (use eos-architecture-review / eos-bug-audit / eos-simplify).
---

# Graduate an Audit

A print-statement loop that found a real bug and then got deleted is research,
not infrastructure. `.claude/rules/audits.md` names the two failure modes —
heuristics that fire on healthy apps, and audits that never run again — and this
skill is the procedure that avoids both. It is doctrine executed by hand; the
judgment is the value, so nothing here is automated.

**The bar:** a checker that is silent on a healthy tree and fails the moment the
defect returns. Anything noisier gets muted within a month, and a muted checker
is worse than none — it looks like coverage.

## When to reach for this

- An ad-hoc grep just found a real defect, and the same class could recur.
- The same shape of breakage has landed twice (that is the trigger, not the
  first occurrence — one bug is a bug, two is a class).
- A rule in `.claude/rules/` states an invariant that nothing enforces.

Skip it when the class is already covered by an existing test layer, when the
defect was a one-off mistake in a refactor (a regression test on that file is
enough), or when the heuristic still misfires after tuning. **Abandoning a
heuristic with a >30% false-positive rate is a success, not a failure** —
you learned the signal isn't separable.

## The procedure

### 1. Name the defect class, not the instance

Write one sentence: *what is true of every instance, and false of every healthy
case?* If you can't, stop — you have an instance, not a class.

### 2. Measure the false-positive rate BEFORE writing the checker

Prototype the match in a throwaway script and count hits on the live tree.
This step is the whole skill. A naive pattern usually returns hundreds of hits;
a stale-app-path scan returned 632, of which 626 were dotted module paths,
runtime `data/` state, and identifiers that merely began with the same prefix.

```bash
python - <<'PY'   # prototype, don't commit
# ... regex/AST over the real tree ...
print("candidates:", n, "| suspected:", len(hits))
for h in hits[:40]: print("  ", h)
PY
```

### 3. Narrow until the signal separates

Prefer a **verifiable** predicate over a textual one. "This path does not exist
on disk" beats "this path looks hardcoded" — the first is checkable, the second
is a guess. Tighten until the live tree is quiet, then read what remains.

**After every narrowing, re-check that the founding case still fires.** A
checker can become *more wrong by becoming quieter*, and the metric you are
watching — false-positive count — moves the right way while it happens, so it
looks like progress.

Measured, 2026-07-28, building `check_field_authors.py`. Round 2 flagged 12
false positives from one app whose fields are set through the settings service,
so round 3 excluded any declaration in a file containing `app_config(`. The FP
count dropped and the output looked clean. But nearly every app calls
`app_config(`, so the exclusion also silenced **the two real findings the check
was written for** — the check now passed on the exact defect that motivated it.
The fix was to require that the loop variable be genuinely interpolated into
the settings lookup, which excludes the settings-backed case without swallowing
anything else.

So keep the motivating case as a fixture from the first line of code, re-run it
after each tightening, and ship it as a test (step 6). "Fewer hits" is only
progress if the hits you wanted are still among them.

### 4. Triage every survivor by hand

For each hit ask: *real defect, deliberate exception, or noise?* Never assume.
This is where the design gets decided:

| Survivor | Action |
|---|---|
| Real defect | Fix it, and let the checker keep it fixed |
| Deliberate exception | Give the checker an **inline opt-out marker** (`// <check-name>: ignore <thing>`) and record the intent at the site. Not a central allowlist someone must maintain |
| Noise you can't separate | Narrow further, or abandon (see the bar) |

### 5. Split by confidence, and gate only the confident half

If some hits are provable and others ambiguous, do not gate on the ambiguous
ones. A gate that fails whenever someone writes a test fixture will be turned
off. Report the ambiguous class as an advisory note that never affects the exit
code. `check-test-app-paths.py` is the reference: an app id found elsewhere in
the tree *proves* a move and gates; an id found nowhere is indistinguishable
from a fixture and only prints a note.

### 6. Ship it with tests that pin both directions

A checker without tests is a checker that will silently stop working. Pin:

- **It fires** — reconstruct the actual historical breakages as fixtures.
- **It is silent** — a healthy fixture, plus every noise class you dismissed in
  step 2 (that is what stops someone "simplifying" the guard back into a
  false-positive machine).
- **The live tree passes** — but assert only the *gating* class. Asserting the
  exact advisory list turns another session's legitimate work into a failure.

### 7. Register it — this is the graduation

One row in `scripts/preflight.py`'s `CHECKS`, with a comment saying why it
exists and why it does or doesn't gate:

```python
{"script": "check-my-thing.py", "scope": ["always", "release"], "gate": True},
```

`gate: True` only if the class is deterministic and its exit already breaks
something (CI, a release). Otherwise `gate: False` and let a human read it.
Scopes are cheap; pick the narrowest that will actually get run.

Emit the agent-cli envelope via `scripts/scanner_lib.py::emit_json` so `--json`
is parseable, and keep the human output actionable — name the fix, not just
the finding.

## Anti-patterns

- **Skipping step 2.** Writing the checker first and measuring later means you
  tune the tree to the checker instead of the reverse.
- **A central allowlist of exceptions.** Every new legitimate case becomes a
  build break plus a chore. Use an inline marker at the site, where the intent
  lives.
- **Gating on an ambiguous signal** because it feels stricter. It gets disabled.
- **Leaving it in `scripts/` unregistered.** Then it is a one-off that found a
  bug once, which is exactly what this skill exists to prevent.
- **Asserting the live tree's exact finding list in a test.** It fails on other
  people's correct work.

## Cross-references

- `.claude/rules/audits.md` — the doctrine (false-positive discipline + the
  three graduation homes: `tests/`, `scripts/check-*.py`, or a platform fix).
- `.claude/rules/self-audit-loops.md` — the umbrella: turn EmptyOS's own tools
  back on EmptyOS. Step 7 is that rule's "load-bearing" graduation step.
- `.claude/rules/agent-cli.md` — the `--json` envelope + exit-code-as-signal.
- `scripts/preflight.py` — the registry every graduated check lands in.
- `scripts/check-test-app-paths.py` — reference for the confidence split (step 5).
- `scripts/check-settings-panel-drift.py` — reference for the inline opt-out
  marker (step 4) and for testing a scanner's own false-positive classes.
