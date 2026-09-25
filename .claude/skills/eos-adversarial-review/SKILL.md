---
name: eos-adversarial-review
description: Pre-commit hostile review — spawn subagents that assume the staged diff is WRONG and hunt six named defect classes. Runs between /eos-simplify and the commit. Use when the user says "adversarial review", "hostile review", "review before commit", "/eos-adversarial-review", or whenever you are about to stage and commit meaningful work.
---

# Adversarial Pre-Commit Review

The last gate before a commit. `/eos-simplify` asks "is this idiomatic?"; this
asks **"is this wrong, and can I prove it?"** — from a reviewer who did not
write the code and starts from the assumption that it is defective.

It exists because the defects that survive a normal self-review are exactly the
ones a *self*-review cannot see: a constant that looks plausible, a test that
passes because it is vacuous, a doc sentence that describes intent rather than
code. Those all read fine to the author.

## Where it sits in the loop

CLAUDE.md § EmptyOS Workflow, between **simplify** and **commit**:

```
build -> conform -> walk -> simplify -> ADVERSARIAL REVIEW -> commit -> live-verify
```

Do not run it before `simplify` — let the convention pass clean up the noise
first, so the hostile reviewers spend their attention on substance.

## The brief (verbatim — do not paraphrase when spawning)

> You are a hostile reviewer. You did not write this code and you assume it is
> wrong. Read the diff plus the surrounding files and the design-constraints
> doc. Flag: (a) any numeric constant not traceable to a cited source, (b) any
> hand-rolled UI markup where a shared helper exists, (c) any behavior that
> contradicts a documented constraint, (d) any escaping/quoting in inline event
> handlers, (e) any test whose tolerance is loose enough to pass a broken
> implementation, (f) any claim in docs or commit message not supported by the
> code. Output a numbered findings list with severity and file:line. If clean,
> say CLEAN and nothing else.

## How to run it

1. **Scope.** `git diff --cached` if anything is staged; otherwise the full
   uncommitted set (`git status --short` + `git diff` + every untracked file).
   Untracked files have no diff — the subagent must `cat` them.
2. **Chunk by coherence, not by size.** One subagent per coherent unit (a new
   app + its engine + its tests; a shared-helper extraction + all its migrated
   call sites; a storage change + its tests). Spawn them in ONE message so they
   run concurrently. A chunk that spans unrelated subsystems produces shallow
   findings in both.
3. **Name the design-constraints docs per chunk.** This is the highest-leverage
   part of the prompt and the reviewers cannot guess it. Always CLAUDE.md, plus:

   | Chunk shape | Constraint docs to name |
   |---|---|
   | Engineering calculator | `docs/TRUST-LOOP.md`, `docs/ENGINEERING-APP-WORKFLOW.md`, the app's own `ALGORITHM.md` + `SOURCE-PACK.md` |
   | Frontend / shared bundle | `docs/FRONTEND-DESIGN-LANGUAGE.md`, `.claude/rules/shared-frontend.md`, `app-ui-patterns.md`, `list-card-density.md`, `standalone-distribution.md` (CSP) |
   | Storage / persistence | `.claude/rules/atomic-persistence.md`, `dev-gotchas.md` § async atomicity |
   | Tests | `.claude/rules/testing.md`, `audits.md`, `vault-operator.md` (fixture leak) |
   | Docs / CLAUDE.md / rules | the file being changed, plus `scripts/preflight.py` to check gate-vs-advisory claims |

4. **Tell them to run the read-only scanners** relevant to the chunk and report
   output — `check_onclick_args.py`, `check_route_500.py`, `check_undefined_names.py`,
   `check_helper_bindings.py`, `check_text_tokens.py`, `check-csp-inline.py`,
   `check_engineering_assurance.py`, `check-personal.py`, `check-branding.py`.
   The subagent finds by reading; the scanner finds by construction. Use both.
5. **Forbid mutation explicitly.** "Do NOT fix anything. Do NOT run git
   add/commit/stash/checkout. Read-only analysis plus read-only scanners only."
   A reviewer that starts fixing stops reviewing, and a hostile reviewer editing
   the tree mid-review is how you lose the other session's work.
6. **Demand file:line and a concrete failure**, not a style opinion. Severity
   CRITICAL / HIGH / MEDIUM / LOW. Uncertain findings must say what would settle
   them.

## Sharpening each axis

The six letters are the standing contract; these are the shapes that actually
recur in this repo, and naming them in the prompt raises the hit rate.

- **(a) constants** — the trap is not the *uncited* constant, it is the
  **decoratively cited** one: a number carrying a clause reference that does not
  actually contain that number. Tell the reviewer to verify the citation says
  it, not merely that a citation exists.
- **(b) hand-rolled UI** — the reviewer must grep `eos-components.js` for the
  helper inventory FIRST, or it will accept reinvention it cannot recognise.
- **(c) contradicted constraints** — the highest-value form is a doc claiming a
  check *gates* when `scripts/preflight.py` registers it advisory.
- **(d) escaping** — the two sinks are **inverses**: a hand-written `on*=""`
  needs `EOS_UI.jsArg()` (a bare `JSON.stringify` closes the attribute, and the
  handler then silently never binds — renders fine, does nothing on click, no
  console error), while `EOS_UI.entityCard({onClick})` escapes for you and wants
  the bare `JSON.stringify`. `statCards` `onClick` must be a *function*; a
  string is silently ignored. Never let a reviewer apply one rule to the other
  sink.
- **(e) vacuous tests** — make the reviewer **construct the broken
  implementation that still passes** and name it. Two specific shapes: a golden
  fixture back-filled from the implementation instead of transcribed from the
  published source (self-consistency, not correctness), and a guard tested only
  in the healthy direction. Per `.claude/rules/audits.md`: a scanner at zero
  findings has proved nothing until it has been watched go **red**.
- **(f) doc claims** — check every named file, script, flag, app id, method and
  **count**. A stale line in CLAUDE.md is load-bearing: it loads into every
  session.

## Disposition — the part that is not optional

The review is not done when findings arrive. Every finding ends in exactly one
of two states before the commit:

- **Resolved** — fixed in the tree. Re-run the affected chunk's reviewer if the
  fix was substantial.
- **Waived** — with a **written reason** recorded in the receipt.
  "Pre-existing", "out of scope for this commit", "false positive because X"
  are all legitimate; silence is not.

Then write the receipt so the commit gate opens:

```bash
python scripts/review_receipt.py write --summary "3 findings: 2 fixed, 1 waived" \
    --waive "engines/foo.py:88 - constant is from the vendor datasheet, cited next commit"
```

The receipt is keyed to a **hash of the diff being committed**. Change the diff
after writing it and the gate re-closes — which is the point: a receipt attests
to the bytes that were reviewed, not to the fact that a review once happened.

Committing into the nested `apps/personal` repo? Pass `--repo` so the receipt
keys on that work tree rather than the parent index:

```bash
python scripts/review_receipt.py write --repo apps/personal --summary "..."
```

The gate reads `git -C <dir>` off the command line and keys on the same tree, so
the two agree. Without the flag you would write a receipt for whatever happens
to be staged in the PARENT repo — which opens somebody else's gate on work
nobody reviewed, and still leaves your commit blocked.

## The commit gate

`scripts/guard_adversarial_review.py` (PreToolUse, sibling of
`guard_git_safety.py`) denies `git commit` when no receipt matches the staged
diff. Same fail-open posture as its siblings — a bug in the guard can never
hard-block every command.

The key comes from the work tree the commit actually targets, so
`git -C apps/personal commit` is gated on the nested index, not the parent's
(add `--repo` when writing that receipt, as above).

Escape hatches, in order of preference: write the receipt (normal path);
`python scripts/review_receipt.py write --waive-all "<reason>"` for a commit
that genuinely does not warrant a review (a typo fix, a revert); or
`EOS_SKIP_REVIEW_GATE=1` for an emergency. Note the env var must be set in the
hook's own environment (`.claude/settings.json` → `env`) — the hook runs
*before* the shell, so an inline `EOS_SKIP_REVIEW_GATE=1 git commit …` has no
effect. Removing the hook entry from `.claude/settings.json` turns it off
permanently.

## When NOT to run it

- Trivial single-line edits, typo fixes, reverts — use `--waive-all` with the
  reason. Ceremony on a one-liner trains the user to skip the gate on the
  commit that needed it.
- A commit of generated or derived artifacts only (regenerated docs, a lockfile).
- Mid-session checkpoint commits on a scratch branch nobody will merge.
- Before `/eos-simplify` — the ordering is deliberate.

## Cross-references

- `.claude/skills/eos-simplify/SKILL.md` — runs immediately before this.
- `.claude/skills/eos-agent-diff-review/SKILL.md` — the sibling for reviewing
  work *another agent* wrote from a plan; hunts specification loss by executing
  old-vs-new. Reach for that one when the author was Codex or an earlier
  session; this one when the author was you.
- `.claude/rules/audits.md` — false-positive discipline; a hostile reviewer is
  a heuristic and fires on healthy code too. Triage before reporting.
- `CLAUDE.md` § EmptyOS Workflow — where this sits in the loop.
