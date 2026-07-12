

A feature request implies the **whole loop by default** — when the user asks to build/implement/add a feature, run all six steps without being asked to; don't stop at "code written" and wait to be told to test, walk, simplify, or verify. Scope down only when the user explicitly narrows it ("just draft it", "no need to test yet") or the change can't reach a step (no UI → skip the walk). The standard loop is **build → conform → walk → simplify → commit → live-verify**:

1. **Build** the feature (app / plugin / engine / SDK).
2. **Conform** — write standard-conformance / system tests (`tests/test_sys_<app>.py`) and run the relevant slice (`.claude/rules/testing.md`).
3. **UI walk** — exercise the real surface in a browser with screenshots when the UI changed (`/eos-ui-walk`).
4. **Simplify** — `/eos-simplify` against EmptyOS conventions; apply the flagged fixes.
5. **Commit** with a scoped message (see § Git / Version Control).
6. **Live-verify** against the daemon — never restart `:9000` yourself; lease a sandbox member (`.claude/rules/sandbox-driven-testing.md`), or ask the user to restart if it's offline.

The full strategy→review loop is `docs/ENGINEERING-WORK-LOOP.md`; the autonomous form is `.claude/rules/test-fix-verify-loop.md`.
