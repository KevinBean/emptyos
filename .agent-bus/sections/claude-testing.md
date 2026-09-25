

Tests (pytest + Playwright + `node --test` for browser JS) cover apps, UI components, user stories, accessibility, visual baselines. When to run what: `.claude/rules/testing.md`; how to write tests: `.claude/rules/test-authoring.md`. Fixtures in `tests/conftest.py`, assertions in `tests/helpers.py`.

```bash
python -m pytest tests/ --ignore=tests/personal -v    # CI / release-safe
python -m pytest tests/test_sys_<app>.py -v           # single app (after UI change)
python -m pytest tests/ -k "not test_ui" -v           # API-only fast path
python -m pytest -m "dogfood and not llm" -v          # dogfood — "is it usable?"
```

- **Always `python -m pytest`, never bare `pytest`** — the binary may resolve to a different Python and silently lose the playwright plugin.
- Daemon-backed tests need `:9000`. Test data uses `TEST_PREFIX = "PLAYWRIGHT-TEST-"`, cleaned by a session autouse fixture. Release/CI runs pass `--timeout=60 --reruns 2`.
- Four layers, four questions — **system** (`test_sys_<app>.py`: does each endpoint work?), **user story** (`test_user_stories.py`: one deep per-app flow), **journey** (`test_journeys.py`: cross-app event chains), **dogfood** (`test_dogfood*.py`: usable for a week?; LLM steps marked `@pytest.mark.llm`). Don't duplicate across layers: dogfood is narrative + ordered + state-threading and earns its keep only when it spans ≥2 apps or catches aggregation bugs endpoint tests miss — below that bar, `test_user_stories.py` is the home.
- A tracked test must never import a gitignored `apps/personal/` module at module scope — CI's bare `--collect-only` aborts the whole run.
- Test-fix-verify loop (dogfood-agent → fix-agent → sandbox `:9001` → verifier): `.claude/rules/test-fix-verify-loop.md`.
