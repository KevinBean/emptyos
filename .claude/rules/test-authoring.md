---
paths:
  - "tests/**"
---

# Test Authoring — writing new tests

Split out of `testing.md` (2026-09-25). `testing.md` says when to run tests;
this file says how to write them.

## Adding tests for a new app

When a new app is created under `apps/`, follow the pattern from existing `test_sys_*.py` files:

1. Create `tests/test_sys_<app>.py` with an `@pytest.mark.api class Test<App>API` and an `@pytest.mark.interactive class Test<App>UI`.
2. Aim for 10+ use cases total — mix of API CRUD tests and real UI workflows (click button → fill form → verify toast/list updates).
3. Use helpers: `assert_ok`, `assert_dict_response`, `assert_list_response` from `helpers.py`.
4. Use UI helpers: `switch_tab`, `click_first`, `wait_for_toast`, `assert_no_js_errors` from `page_helpers.py`.
5. Register all created test data using `TEST_PREFIX` — it will be cleaned up by `cleanup_after_all` in conftest.py. If the app stores data somewhere new, add a cleanup block to conftest.py.
6. Personal apps (gitignored) go in `tests/personal/test_<app>.py` and use the `require_app` fixture to skip gracefully when the app isn't installed.

### `assert_ok` checks the status, not the body — check a seed call's body too

`assert_ok` asserts HTTP 200 and nothing else, and EmptyOS routes answer most
failures in-band: `{"error": "..."}` **with a 200**. So a seed call wrapped in
`assert_ok` (or not asserted at all) can fail silently, and the test then fails
later, on the thing it was actually about, with a misleading message.

Measured 2026-09-26 on `test_sys_earthing.py::test_soil_import_takes_only_wenner_rows`:
the soil `PUT /soundings` seed answered `{"error": "soil engine not available"}`
with a 200 because CI lacks numpy/scipy. Nothing checked it, so earthing saw an
empty project, and its own error surfaced as `KeyError: 'imported'` — which reads
as an earthing bug and was not one.

Any call that **sets up** state the test depends on gets its body checked:

```python
seeded = assert_ok(http_client.put(url, json=payload))
assert "error" not in seeded, seeded
```

The assertion then fails at the seed and prints the real cause.

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

## Browser JS — `node --test`, not a browser

Page JS carries real logic (escaping, ability comparison, status-variant
mapping, the state store, loop control), and none of it was reachable by a
test. A Playwright harness could not fix that: CI never runs
`playwright install` and excludes `interactive` tests outright, so a
browser-driven suite would gate nothing.

`tests/js/*.test.mjs` runs under Node's built-in runner — **no npm packages, no
`package.json`, no browser**. `tests/js/shim.mjs` evaluates a shipped
browser-global `.js` file into a tiny sandbox and hands back its globals —
`loadStatic` takes any repo-relative path, so a shared `emptyos/web/static/`
bundle and an app's own `pages/` sibling both load;
`tests/test_unit_js_suite.py` is the pytest bridge, so the suite runs from
`pytest` locally and from the existing CI guards step. It skips (never fails)
where Node is absent.

Rules that keep it honest:

- **Pure functions only.** The shim is not a jsdom and must not grow into one.
  It implements exactly one real DOM behaviour — `textContent` → `innerHTML`
  escaping, because `EOS_UI.esc` is built on it and *without* it every escaping
  assertion passes for the wrong reason (a `warningBanner` test "proved" markup
  was escaped when the message had simply vanished). Anything needing layout,
  events, or rendering belongs in the `/eos-ui-walk` browser pass.
- **Assert the loaded surface first.** A failed load yields an empty object and
  every later assertion passes against `undefined`.
- **Expected values are measured, not recalled.** `esc` leaves quotes alone;
  `statusVariant` answers `neutral` (not `status-neutral`) for an unknown
  status. Three first-draft assertions here were wrong from memory.
- **A personal app's page JS skips when absent** (`apps/personal/` is
  gitignored) — same discipline as the Python side below.
- **Normalise anything the sandbox constructed before `assert/strict` sees it.**
  An array or object built *inside* the vm context carries the sandbox's
  `Array.prototype`, and `deepEqual` compares prototypes — so an
  element-for-element identical list fails, while the diff prints two lists that
  look the same. Wrap the value (`Array.from(...)`, `{...obj}`) at the assertion
  boundary. Measured 2026-09-05: four `EOS.fetchLayers()` cases failed this way
  while the code under test was provably correct, and the neighbouring case
  passed only because its array happened to be host-built — which is what makes
  it read as a real bug rather than a harness artifact.

To make page logic testable, lift the decision into a sibling browser-global
file the page `<script>`-tags and the shim loads — the *shipped* file, so there
is no parallel build to drift. References:
`apps/personal/studio/pages/icon-batch-loop.js`,
`apps/public/standard/worklog/pages/competency-chips.js`.

**A page's own script usually cannot be loaded directly**, which is what forces
the sibling: `pages/index.html`'s bulk typically ends in a boot IIFE that calls
`EOS_UI.hashRoute()` and fetches on load, so the shim either throws on an
undefined global or leaves an async rejection firing after the test ended.
Stubbing enough to get past that is how the shim turns into the bad jsdom the
rule above forbids. Move the pure helper out instead — and give it a name no
other file on that page declares, per `.claude/rules/multi-module-apps.md`
§ frontend counterpart.

## Precondition guards — skip, don't fail, when the environment can't run it

CI is a bare Ubuntu container: `pip install -e .` and nothing else. No
`apps/personal/` (gitignored), no numpy/scipy/Pillow/tomlkit/openpyxl, no
ripgrep, no Playwright browser, a throwaway vault, no LLM provider, no auth
credential. A tracked test that needs any of those **fails** there rather than
skipping — and a failing test reads as a regression, indistinguishable from
something actually broken. Measured 2026-09-12: of 160 failures on the API
suite's first-ever run, ~130 were this.

Five mechanisms, picked by what the precondition actually is:

| Precondition | Use | Shape |
|---|---|---|
| App absent (`apps/personal/`, or retired) | `requires_app("id")` | module-level `pytestmark`; checks the app dir on disk |
| Optional Python dep absent | `requires_dep("numpy", ...)` | per-test/class marker; `find_spec` in the test process |
| Playwright's Chromium **binary** absent | `requires_browser()` | per-test marker; probes `chromium.executable_path`, cached |
| A daemon-observable capability (LLM, ripgrep, auth config) | a **fixture** (`require_llm`, `require_ripgrep`, `require_auth_enforced`) | must query the daemon, so it cannot be a marker |
| Authored vault content | a per-file fixture (see `require_real_course` in `test_sys_learn.py`) | probes the content through the daemon |

Three rules that decide whether a guard is honest:

- **Name the root cause, not the symptom.** An app whose entry module imports
  numpy does not load, so its routes 404 and the test fails on `Expected 200,
  got 404`. Gate it on `requires_dep("numpy")` — the reason it is missing.
  Never write "skip if the route is 404": that also swallows an app that failed
  to load for a *different* reason, which is exactly the regression you want.
- **Never gate on the assertion itself.** "Skip if the request was not
  rejected" is circular and silently retires the test. `require_auth_enforced`
  reads the configured credential instead, so a broken auth gate still fails.
- **Per-test, not per-module, unless every test in the file needs it.** Five of
  the files gated in this pass had tests that pass or skip for other reasons; a
  module marker would have hidden working coverage.

Verify both directions, and prefer an unmocked one: run where the dep is
genuinely absent and watch the reason name it, then run where it is present and
watch the test fall through to a *different* skip reason. The differing reason
string is the discriminator.

**`xfail` is not a guard.** A test that fails everywhere is a defect, not an
environment gap: mark it `xfail(strict=True)` with a per-bug reason so the build
fails if someone fixes it without removing the marker. Reserve non-strict
`xfail` for a failure you have genuinely not diagnosed, and say so in the
reason — it is parked debt, not resolved work.

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
