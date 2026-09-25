

A feature request implies the **whole loop by default** — don't stop at "code written" and wait to be told to continue. Scope down only when the user explicitly narrows it or a step can't apply (no UI → skip the walk). The loop is **build → conform → walk → simplify → adversarial review → commit → live-verify**:

1. **Build** the feature (app / plugin / engine / SDK).
2. **Conform** — write system tests (`tests/test_sys_<app>.py`) and run the relevant slice (`.claude/rules/testing.md`).
3. **UI walk** — exercise the real surface in a browser with screenshots when the UI changed (`/eos-ui-walk`).
4. **Simplify** — `/eos-simplify` against EmptyOS conventions; apply the flagged fixes.
5. **Adversarial review** — `/eos-adversarial-review`: hostile reviewers hunt six defect classes (listed in the skill); it runs after simplify so reviewers spend attention on substance. Every finding ends fixed or waived **with a written reason**, recorded with `python scripts/review_receipt.py write` — that is what opens the commit gate (`scripts/guard_adversarial_review.py`).
6. **Commit** with a scoped message (§ Git / Version Control).
7. **Live-verify** against the daemon — never restart `:9000` yourself; lease a sandbox member (`.claude/rules/sandbox-driven-testing.md`), or ask the user to restart.

Full loop: `docs/ENGINEERING-WORK-LOOP.md`; autonomous form: `.claude/rules/test-fix-verify-loop.md`.
