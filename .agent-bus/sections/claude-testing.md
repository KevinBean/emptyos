

Tests (pytest + Playwright) cover apps, UI components (modal/sidebar/chat), user stories, accessibility, visual baselines, edge cases. System suite is CI-safe; personal tests gitignored. See `tests/conftest.py` for fixtures and `tests/helpers.py` for shared assertions.

```bash
python -m pytest tests/ --ignore=tests/personal -v    # CI / release-safe
python -m pytest tests/test_sys_<app>.py -v           # single app (after UI change)
python -m pytest tests/ -k "not test_ui" -v           # API-only fast path
python -m pytest -m "dogfood and not llm" -v          # dogfood — "is it usable?"
```

Always invoke as `python -m pytest`, never bare `pytest` — the `pytest` binary may resolve to a different Python than the one running the daemon (common on Windows with multiple Python installs), causing pytest-playwright plugin discovery to fail silently (`fixture 'page' not found`).

Requires daemon at `localhost:9000`. One-time setup: `pip install playwright pytest-playwright pytest-timeout pytest-rerunfailures httpx && playwright install chromium`. Test data uses `TEST_PREFIX = "PLAYWRIGHT-TEST-"`, cleaned by session autouse fixture. Pass `--timeout=60 --reruns 2` on release / CI runs: `--timeout` kills hung tests, `--reruns 2` retries UI flakes that surface when the daemon is under heavy parallel load — 1 retry isn't always enough because the immediate retry runs while the daemon is still swamped; 2 retries gives the daemon a chance to catch up.

### Four layers, four questions

| Layer | Files | Answers |
|---|---|---|
| **System** | `test_sys_<app>.py` | Does each button/endpoint work? (smoke) |
| **User story** | `test_user_stories.py` | Does one deep per-app flow work end-to-end? |
| **Journey** | `test_journeys.py` | Do cross-app event chains ripple? |
| **Dogfood** | `test_dogfood.py` + `test_dogfood_<app>.py` | Could I use this for a week/month without noticing something broken? |

Don't conflate or duplicate across layers. Dogfood is narrative + ordered + state-threading; earns its keep when it spans ≥2 apps or catches aggregation bugs endpoint tests miss. Below that bar, `test_user_stories.py` is the right home. LLM-hitting steps use `@pytest.mark.llm` so `-m "dogfood and not llm"` stays fast and free. CI runs the non-LLM dogfood suite on every push. Full workflow: `.claude/rules/testing.md`.

### Test-fix-verify loop

EmptyOS tests/fixes/verifies itself by composing four roles via the event bus: **friction source** (today `dogfood-agent`) → **fix-driver** (`apps/fix-agent/`, worktree-per-fix, py_compile-gated merge) → **sandbox** (`:9001` via `plugins/dogfood-demo/`, restarted between merge + verify) → **verifier** (dogfood-agent re-runs scenario, auto-reverts on failure). Main daemon never restarted by the loop. Contract + safety invariants: `.claude/rules/test-fix-verify-loop.md`.
