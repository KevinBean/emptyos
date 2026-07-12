# Testing Rule

Run tests at well-defined checkpoints during development. See CLAUDE.md § "Testing (pytest + Playwright)" for full details on suite structure.

## When to run tests

- **After any UI change in an app** — run that app's system test file:
  `python -m pytest tests/test_sys_<app>.py -v`
- **After changing an app's API/backend** — run the API slice:
  `python -m pytest tests/ --ignore=tests/personal -k "not test_ui" -v`
- **Before committing a feature that touches multiple apps** — run the full system suite:
  `python -m pytest tests/ --ignore=tests/personal -v`
- **When adding a new app** — add a matching `tests/test_sys_<app>.py` with 10+ use cases (API + UI workflows)

## Adding tests for a new app

When a new app is created under `apps/`, follow the pattern from existing `test_sys_*.py` files:

1. Create `tests/test_sys_<app>.py` with an `@pytest.mark.api class Test<App>API` and an `@pytest.mark.interactive class Test<App>UI`.
2. Aim for 10+ use cases total — mix of API CRUD tests and real UI workflows (click button → fill form → verify toast/list updates).
3. Use helpers: `assert_ok`, `assert_dict_response`, `assert_list_response` from `helpers.py`.
4. Use UI helpers: `switch_tab`, `click_first`, `wait_for_toast`, `assert_no_js_errors` from `page_helpers.py`.
5. Register all created test data using `TEST_PREFIX` — it will be cleaned up by `cleanup_after_all` in conftest.py. If the app stores data somewhere new, add a cleanup block to conftest.py.
6. Personal apps (gitignored) go in `tests/personal/test_<app>.py` and use the `require_app` fixture to skip gracefully when the app isn't installed.

## Ongoing deepening (Option 3 practice)

After the initial test file is in place, keep growing coverage where real bugs appear:

**When you touch an app, add 1-2 deep user story tests for it** in `tests/test_user_stories.py`. A "story test" differs from the baseline `test_sys_*.py` tests:

| Baseline test (sufficient for new apps) | Story test (add on significant touch) |
|---|---|
| "Add button exists" | "Add entry → verify in list → check dashboard total updates → delete → verify all views update" |
| "Page loads without JS errors" | "Reload after adding data → data still there (persistence)" |
| "API returns 200" | "Response contains the value we just wrote" |
| "Tab switches" | "Switch tab → verify tab-specific data loaded → switch back → state preserved" |

**Triggers for adding a story test:**
- Fixed a bug in the app → add a test that would have caught it
- Added a cross-app feature (event emission, vault ripple) → add a journey test in `test_journeys.py`
- Changed the data model → add a persistence test
- Touched UI layout → add a visual baseline to `test_visual.py` + regenerate screenshot

**Story test file map:**
- `test_user_stories.py` — multi-step flows with verification at each step (primary)
- `test_journeys.py` — cross-app event chains (capture → task → project)
- `test_accessibility.py` — keyboard-only flows, ARIA, mobile viewport
- `test_visual.py` — screenshot baselines (regenerate with `python -m pytest tests/test_visual.py` after intentional UI changes)
- `test_components.py` — modal/sidebar/chat component lifecycle

## Requirements

- EmptyOS must be running on `localhost:9000` to run tests (not required for `--collect-only`)
- `pip install playwright pytest-playwright httpx` + `playwright install chromium` (one-time)
- CI (`.github/workflows/tests.yml`) runs `--collect-only` on every push to catch import/syntax errors
- CI (`.github/workflows/dogfood.yml`) boots the daemon against a throwaway vault and runs `pytest -m "dogfood and not llm"` on every push to `main` and every PR. Non-LLM only; LLM dogfood runs locally or on demand.

## Cross-browser testing (PWA work)

PWA-related tests (`tests/test_sys_pwa.py`) must run across all three Playwright engines to catch iOS/Safari-specific issues. WebKit is the load-bearing one — it's what iOS Safari runs.

Setup (one-time, in addition to the default):
- `playwright install firefox webkit`

Run the PWA suite on each engine locally:
- `python -m pytest tests/test_sys_pwa.py -v --browser chromium`
- `python -m pytest tests/test_sys_pwa.py -v --browser firefox`
- `python -m pytest tests/test_sys_pwa.py -v --browser webkit`

Tests that legitimately diverge by engine (e.g. service worker registration in WebKit private contexts) should `pytest.skip()` rather than fail.

### Manual device matrix (PWA ship blocker)

Cross-browser automation only catches engine differences, not real-device install flows. Before declaring the PWA shippable, verify on real devices:

| Device / Browser          | Install works | SW caches | Offline fallback | Capture + journal flow |
|---------------------------|---------------|-----------|------------------|-----------------------|
| iPhone Safari             |               |           |                  |                       |
| Android Chrome            |               |           |                  |                       |
| Desktop Chrome (Windows)  |               |           |                  |                       |
| Desktop Edge (Windows)    |               |           |                  |                       |

iPhone Safari + Android Chrome rows must all pass. Other devices are V1.5.
