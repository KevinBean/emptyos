

Evaluating an external repo/tool for borrowing: **first check for a closed verdict** — `python scripts/check_borrow_verdict.py <name>` (exit 1 = a verdict exists in `docs/OPEN-SOURCE-BORROWING-PLAN.md` / `docs/DEFERRED-WORK.md`; read it and stop). Then **ground every claim in the real source**, never the README or memory. Record the build / borrow / build-nothing decision and why; a deferral also gets a `docs/DEFERRED-WORK.md` row with its trigger. The disciplined form is `/eos-repo-extract`.

**Any conclusion drawn across many sources** — insights, audits, "state/trajectory of X", "why does Y recur" — follows `.claude/rules/deep-research.md`: baseline → first-pass → gap-pick → deep-read → refute → grade evidence.
