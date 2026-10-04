# Testing Rule

Run tests at well-defined checkpoints during development. See CLAUDE.md § Testing for the suite structure.

## When to run tests

- **After any UI change in an app** — run that app's system test file:
  `python -m pytest tests/test_sys_<app>.py -v`
- **After changing an app's API/backend** — run the API slice:
  `python -m pytest tests/ --ignore=tests/personal -k "not test_ui" -v`
- **Before committing a feature that touches multiple apps** — run the full system suite:
  `python -m pytest tests/ --ignore=tests/personal -v`
- **When adding a new app** — add a matching `tests/test_sys_<app>.py` with 10+ use cases (API + UI workflows)

## Writing tests

Adding a `test_sys_<app>.py`, story tests, browser-JS tests (`node --test`),
precondition guards (skip, don't fail, when CI lacks a dep/app/browser), and
the PWA cross-browser matrix → `.claude/rules/test-authoring.md` (loads on
`tests/**`).

## A new test is not evidence until it has been red

A test that has only ever passed pins nothing (`.claude/rules/audits.md`
§ Failure mode 3). Break the thing it names, watch that test fail, restore.

**Use `.claude/skills/eos-mutation-verify` — do not re-derive the loop.** For a
batch, its `run_mutations.py` takes a data file of `(label, old, new, expect)`
rows. That runner exists because the same ~70-line loop was hand-rolled four
times in one session and the copies disagreed; it was then hand-rolled three
more times in a later session, which is why this pointer now sits here, at the
moment a test gets written, rather than only in the simplify checklist.

It reports one outcome a hand-run cannot: **MISTARGETED** — the named test
passed but another test in the file caught the mutation, so the behaviour *is*
pinned and only the `expect` filter is wrong. Without that re-check a mis-aimed
`-k` is indistinguishable from unpinned behaviour.

It also catches the shape a hand-run reads as success: running several suites
and calling a mutation caught if *any* of them goes red. Measured 2026-09-09 —
a contract test asserting "no object is sent with a zero distance" could never
fail, because the fixture it read always supplied real distances; a sibling
JS test was the one going red, and the hand-rolled loop reported it caught.

**Story test file map:**
- `test_user_stories.py` — multi-step flows with verification at each step (primary)
- `test_journeys.py` — cross-app event chains (capture → task → project)
- `test_accessibility.py` — keyboard-only flows, ARIA, mobile viewport
- `test_visual.py` — screenshot baselines (regenerate with `python -m pytest tests/test_visual.py` after intentional UI changes)
- `test_components.py` — modal/sidebar/chat component lifecycle

## A tracked test must not import a gitignored app at module scope

`apps/personal/` is gitignored, so it is absent in every public clone and in
CI — and CI's first step is a bare `pytest --collect-only`, which aborts the
**whole run** on one collection error. A single unguarded
`from apps.personal.… import …` therefore turns the entire suite red
(measured 2026-09-01: 15358 tests collected, 1 error, exit non-zero).

Guard it:

```python
_APP = ROOT / "apps" / "personal" / "<app>" / "<module>.py"
needs_app = pytest.mark.skipif(not _APP.exists(), reason="apps/personal/<app> is gitignored")
if _APP.exists():
    from apps.personal.<app>.<module> import thing
else:
    thing = None
pytestmark = needs_app
```

Same shape as `test_unit_clip_chaining.py` / `test_unit_music_studio_frames.py`.
Verify with a real archive, never the working tree —
`git archive HEAD | tar -x -C <scratch>` then collect there.

## Requirements

- EmptyOS must be running on `localhost:9000` to run tests (not required for `--collect-only`)
- `pip install playwright pytest-playwright httpx` + `playwright install chromium` (one-time)
- CI (`.github/workflows/tests.yml`) runs `--collect-only` on every push to catch import/syntax errors
- CI (`.github/workflows/dogfood.yml`) boots the daemon against a throwaway vault and runs `pytest -m "dogfood and not llm"` on every push to `main` and every PR. Non-LLM only; LLM dogfood runs locally or on demand.

