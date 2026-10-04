"""Shared fixtures for EmptyOS E2E tests."""

import json
import os
import re
import time
import tomllib
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from helpers import BASE_URL, TEST_PREFIX, WORKLOG_TEST_DATES

try:  # absent from the public snapshot, which drops files naming held apps
    from cleanup_engineering import cleanup_engineering_apps
except ImportError:
    cleanup_engineering_apps = None


def _register_app_packages() -> None:
    """Make ``from apps.<dirname>.<mod> import ...`` resolve regardless of where
    an app physically lives in the track tree (apps/public/<group>/<id>, etc.).

    Apps moved under track folders so the bare namespace lookup ``apps.<id>``
    (which used to resolve to the flat ``apps/<id>/``) no longer works. We
    install a meta-path finder that maps ``apps.<dirname>`` → the real nested
    dir, lazily, ONLY when not already in sys.modules — so per-app test fixtures
    that self-register their package (e.g. pattern-harvester, feature-pipeline)
    still win and load their own submodules. ``apps.personal.<id>`` resolves
    naturally via the ``apps`` namespace (personal didn't move).
    """
    import sys
    import types
    import importlib.abc
    import importlib.machinery

    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        from emptyos.sdk.app_layout import iter_app_dirs
    except Exception:
        return
    apps_root = repo / "apps"
    if "apps" not in sys.modules:
        pkg = types.ModuleType("apps")
        pkg.__path__ = [str(apps_root)]
        sys.modules["apps"] = pkg
    elif hasattr(sys.modules["apps"], "__path__") and str(apps_root) not in list(sys.modules["apps"].__path__):
        sys.modules["apps"].__path__.append(str(apps_root))

    mapping: dict[str, str] = {}
    for _aid, adir in iter_app_dirs(apps_root, include_personal=False):
        for seg in {adir.name, adir.name.replace("-", "_")}:
            mapping[f"apps.{seg}"] = str(adir)

    class _NestedAppFinder(importlib.abc.MetaPathFinder):
        _eos_apps_shim = True

        def find_spec(self, fullname, path=None, target=None):
            d = mapping.get(fullname)
            if d is None:
                return None
            spec = importlib.machinery.ModuleSpec(fullname, None, is_package=True)
            spec.submodule_search_locations = [d]
            return spec

    if not any(getattr(f, "_eos_apps_shim", False) for f in sys.meta_path):
        sys.meta_path.insert(0, _NestedAppFinder())


_register_app_packages()

# ── Run-artifact capture ─────────────────────────────────────────────────────
# Every pytest session writes per-test artifacts under
#   data/apps/tests/runs/<run-id>/<safe-nodeid>/
# When the parent process (apps/tests/app.py) launches pytest, it sets
# EOS_TESTRUN_ID so the run dir aligns with the row recorded in history.
# Standalone pytest invocations generate their own run id.
_RUN_ID = os.environ.get("EOS_TESTRUN_ID") or (
    time.strftime("%Y-%m-%dT%H-%M-%S") + "_" + uuid.uuid4().hex[:6]
)
_REPO_ROOT = Path(__file__).resolve().parent.parent
_RUNS_ROOT = _REPO_ROOT / "data" / "apps" / "tests" / "runs"


def _safe_nodeid(nodeid: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", nodeid)
    return safe[:200] or "_"


def _run_dir() -> Path:
    p = _RUNS_ROOT / _RUN_ID
    p.mkdir(parents=True, exist_ok=True)
    return p


def _test_dir(nodeid: str) -> Path:
    p = _run_dir() / _safe_nodeid(nodeid)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _read_summary() -> dict:
    sp = _run_dir() / "summary.json"
    if sp.exists():
        try:
            return json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"run_id": _RUN_ID, "tests": []}


def _write_summary(s: dict) -> None:
    (_run_dir() / "summary.json").write_text(
        json.dumps(s, indent=2), encoding="utf-8"
    )


def _read_auth_token() -> str:
    """Read network.auth_token from emptyos.toml, env override wins.

    Returns empty string when auth is not configured (local-mode daemon).
    """
    env = os.environ.get("EOS_AUTH_TOKEN", "").strip()
    if env:
        return env
    cfg = Path(__file__).resolve().parent.parent / "emptyos.toml"
    if not cfg.exists():
        return ""
    try:
        with open(cfg, "rb") as f:
            data = tomllib.load(f)
        return str((data.get("network") or {}).get("auth_token") or "")
    except Exception:
        return ""


_AUTH_TOKEN = _read_auth_token()
_AUTH_HEADERS = {"Authorization": f"Bearer {_AUTH_TOKEN}"} if _AUTH_TOKEN else {}


def _uses_playwright(item) -> bool:
    """Item depends on pytest-playwright's `page` fixture (or any fixture that
    transitively pulls in `playwright`).

    pytest-playwright's `playwright` fixture is session-scoped and calls
    `sync_playwright().start()` on first use, which installs a ProactorEventLoop
    that stays in "running" state for the remainder of the session. That blocks
    pytest-asyncio's Runner from starting, so any `@pytest.mark.asyncio` test
    that runs AFTER a Playwright test fails with "Runner.run() cannot be called
    from a running event loop".

    Reorder hook below pushes Playwright items to the end so async tests run
    first, in a clean environment.
    """
    # Fast-path: parametrized browser tests expose [chromium]/[firefox]/[webkit]
    name = getattr(item, "name", "") or ""
    if "[chromium]" in name or "[firefox]" in name or "[webkit]" in name:
        return True
    # Fallback: inspect fixture closure for Playwright fixtures
    fixtures = getattr(item, "fixturenames", ()) or ()
    return any(fn in fixtures for fn in ("page", "browser", "context", "playwright", "app_page", "page_errors"))


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    """Two passes (tryfirst so the marker pass lands before -m deselection):

    1. Any test that requests the ``require_llm`` fixture makes a real LLM call,
       so it must carry the ``llm`` marker — otherwise a "fast" ``-m api`` run
       pulls in slow claude-cli/Opus calls and a single one wedges the whole run
       (CLAUDE.md § Testing: "LLM-hitting steps use @pytest.mark.llm"). Apply it
       automatically so the convention can't drift across the ~11 such files.
    2. Run non-Playwright items first so pytest-asyncio tests don't collide with
       Playwright's session-scoped running loop. Within each bucket, preserve the
       original collection order.
    """
    for item in items:
        if "require_llm" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.llm)

    playwright_items = [i for i in items if _uses_playwright(i)]
    other_items = [i for i in items if not _uses_playwright(i)]
    items[:] = other_items + playwright_items


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture(scope="session", autouse=True)
def _testrun_session():
    """Initialize per-run summary at session start, finalize at end."""
    s = {
        "run_id": _RUN_ID,
        "started": datetime.now(timezone.utc).isoformat(),
        "finished": None,
        "tests": [],
        "totals": {"passed": 0, "failed": 0, "skipped": 0, "error": 0},
    }
    _write_summary(s)
    yield
    s = _read_summary()
    s["finished"] = datetime.now(timezone.utc).isoformat()
    counts = {"passed": 0, "failed": 0, "skipped": 0, "error": 0}
    for t in s.get("tests", []):
        st = t.get("status", "")
        if st in counts:
            counts[st] += 1
    s["totals"] = counts
    _write_summary(s)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Capture per-test outcome into <run-id>/<test>/result.json.

    Only the 'call' phase is recorded (skip setup/teardown noise unless they
    fail, in which case rep.failed will be true and we still record).
    """
    out = yield
    rep = out.get_result()
    # Record on call always; on setup/teardown only if it failed (otherwise
    # we'd append a "passed" row for every test's setup phase too).
    if rep.when != "call" and not rep.failed:
        return
    td = _test_dir(item.nodeid)
    if rep.passed:
        status = "passed"
    elif rep.skipped:
        status = "skipped"
    else:
        status = "failed"
    result = {
        "nodeid": item.nodeid,
        "phase": rep.when,
        "status": status,
        "duration_ms": int(getattr(rep, "duration", 0) * 1000),
        "error": str(rep.longrepr) if rep.failed else None,
    }
    try:
        (td / "result.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
    except Exception:
        pass
    s = _read_summary()
    # De-dupe by nodeid + phase so re-reported teardown rows replace prior.
    tests = [
        t for t in s.get("tests", [])
        if not (t.get("nodeid") == item.nodeid and t.get("phase") == rep.when)
    ]
    tests.append(result)
    s["tests"] = tests
    _write_summary(s)


_DAEMON_FREE_TEST_PREFIXES = ("test_sdk_", "test_unit_")


@pytest.fixture(scope="session")
def server_health():
    """Daemon health probe — session-cached. Returns True if daemon is up.

    Daemon-free test files may override this fixture to bypass the probe,
    but the override MUST return truthy — `_require_daemon_for_http_tests`
    reads truthiness, so `return None` silently skips the entire file
    (this killed 66 tests across two files until 2026-07-12)."""
    try:
        resp = httpx.get(f"{BASE_URL}/api/health", timeout=5, headers=_AUTH_HEADERS)
        return resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException):
        return False


@pytest.fixture(autouse=True)
def _require_daemon_for_http_tests(request, server_health):
    # Pure-Python tests (SDK + unit) don't touch the daemon — let them run
    # offline so `/eos-simplify` can verify them without a restart. HTTP /
    # UI tests still skip when the daemon isn't up.
    module_name = request.node.module.__name__.rsplit(".", 1)[-1]
    if module_name.startswith(_DAEMON_FREE_TEST_PREFIXES):
        return
    if not server_health:
        pytest.skip("EmptyOS not running on localhost:9000")


@pytest.fixture(scope="session")
def app_list():
    """Fetch all apps from the running server."""
    resp = httpx.get(f"{BASE_URL}/api/apps", timeout=10, headers=_AUTH_HEADERS)
    return resp.json()


@pytest.fixture(scope="session")
def llm_available():
    """Check if think capability has an online provider."""
    try:
        resp = httpx.get(f"{BASE_URL}/api/capabilities", timeout=5, headers=_AUTH_HEADERS)
        caps = resp.json()
        if isinstance(caps, dict):
            for cap in caps.values() if isinstance(caps, dict) else []:
                if isinstance(cap, dict):
                    providers = cap.get("providers", [])
                    if any(p.get("status") == "online" for p in providers if isinstance(p, dict)):
                        return True
        return True  # assume available if response is valid
    except Exception:
        return False


@pytest.fixture
def require_llm(llm_available):
    """Skip test if LLM is not available."""
    if not llm_available:
        pytest.skip("LLM (think capability) not available")


def _auth_is_configured() -> bool:
    """True when the daemon under test actually enforces auth.

    `routes_auth.py` installs the whole middleware only under
    ``if _auth_token or _login_password``, so a daemon with neither credential
    set has NO auth gate and answers every request 200. CI's emptyos.toml sets
    `mode = "local"` and no credential, which is that case.

    Read from the same emptyos.toml the daemon booted from — test process and
    daemon are the same box in CI and on the dev machines.
    """
    import os
    import tomllib

    if os.environ.get("EOS_TEST_AUTH_TOKEN"):
        return True
    try:
        with open(_REPO_ROOT / "emptyos.toml", "rb") as f:
            net = (tomllib.load(f).get("network") or {})
    except Exception:
        return False
    return bool(net.get("auth_token") or net.get("password"))


@pytest.fixture
def require_ripgrep(http_client):
    """Skip a test needing grep CONTENT mode when ripgrep is absent.

    `GrepSearchProvider` documents the limit itself: "Falls back to `grep -rl`
    when ripgrep is unavailable (files mode only - `grep` doesn't give the same
    structured content output)". So content-mode results carry no
    `line_number` without `rg`, and ubuntu-latest has no `rg`.
    """
    try:
        info = http_client.get("/repo/api/info").json()
    except Exception:
        pytest.skip("repo app unavailable - cannot determine ripgrep support")
    if not info.get("rg_available"):
        pytest.skip("ripgrep not installed - grep content mode is files-only")


@pytest.fixture
def require_auth_enforced():
    """Skip a test that asserts auth REJECTION when this daemon enforces none.

    Deliberately keyed on configuration, not on the endpoint's own response —
    "skip if the request was not rejected" would be circular and would mask a
    real auth regression. With a credential configured, the test still runs and
    still fails if the gate is broken.
    """
    if not _auth_is_configured():
        pytest.skip("daemon enforces no auth (no network.auth_token/password configured)")


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    """Authenticate Playwright contexts via the eos_session cookie when
    auth_token is set.

    The cookie (POST /login sets the same value) covers page loads, fetch()
    AND WebSocket handshakes against the daemon host — and, being a cookie,
    it is scoped to that host. Do NOT re-add a global Authorization header
    via extra_http_headers: Playwright attaches those to EVERY request, which
    (a) leaks the bearer token to third-party hosts and (b) forces a CORS
    preflight on cross-origin assets (Google Fonts rejects the `authorization`
    request header, failing export-bundle tests with console errors).
    """
    if _AUTH_TOKEN:
        from urllib.parse import urlparse

        host = urlparse(BASE_URL).hostname or "localhost"
        storage = dict(browser_context_args.get("storage_state") or {})
        cookies = list(storage.get("cookies") or [])
        cookies.append({
            "name": "eos_session", "value": _AUTH_TOKEN,
            "domain": host, "path": "/",
        })
        storage["cookies"] = cookies
        storage.setdefault("origins", [])
        return {
            **browser_context_args,
            "storage_state": storage,
        }
    return browser_context_args


@pytest.fixture
def page(page, request):
    """Wrap pytest-playwright's `page` to capture trace/console/screenshot.

    Each test gets <run-id>/<safe-nodeid>/ populated with:
      - trace.zip          (Playwright trace — DOM snapshots + actions + sources)
      - console.log        (browser console messages, JS errors)
      - screenshot-final.png  (visible viewport at teardown)

    Failures still raise normally; capture is best-effort.
    """
    td = _test_dir(request.node.nodeid)
    # Tracing — wrapped in try/except so a stale context (someone closed it
    # mid-test) doesn't poison the teardown.
    tracing_started = False
    try:
        page.context.tracing.start(snapshots=True, sources=True, screenshots=True)
        tracing_started = True
    except Exception:
        pass

    console_path = td / "console.log"
    cf = open(console_path, "w", encoding="utf-8")

    def _on_console(msg):
        try:
            cf.write(f"[{msg.type}] {msg.text}\n")
        except Exception:
            pass

    def _on_pageerror(err):
        try:
            cf.write(f"[pageerror] {err}\n")
        except Exception:
            pass

    page.on("console", _on_console)
    page.on("pageerror", _on_pageerror)

    try:
        yield page
    finally:
        try:
            cf.flush()
            cf.close()
        except Exception:
            pass
        if tracing_started:
            try:
                page.context.tracing.stop(path=str(td / "trace.zip"))
            except Exception:
                pass
        try:
            page.screenshot(path=str(td / "screenshot-final.png"), full_page=False)
        except Exception:
            pass


@pytest.fixture
def page_errors(page):
    """Collect JS errors during a test. Assert empty after test."""
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    return errors


@pytest.fixture(scope="session")
def http_client():
    """Shared httpx client for API tests."""
    client = httpx.Client(base_url=BASE_URL, timeout=15, headers=_AUTH_HEADERS)
    yield client
    client.close()


@pytest.fixture
def app_page(page, base_url):
    """Factory fixture: app_page("task") navigates + waits for networkidle.

    Returns a function so tests can call app_page("task") inline.
    """
    def _go(app_id, wait_idle=True):
        url = f"{base_url}/{app_id}/"
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        if wait_idle:
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                page.wait_for_timeout(800)
        return page
    return _go


def _safe_request(client, method, url, **kwargs):
    """Make an HTTP request, swallow all errors. Used by cleanup."""
    try:
        return client.request(method, url, **kwargs)
    except Exception:
        return None


@pytest.fixture(scope="session", autouse=True)
def cleanup_after_all(server_health):
    """Clean up test data after all tests complete.

    Iterates each app's list endpoint, finds entries containing TEST_PREFIX,
    and deletes them via the appropriate endpoint. ``server_health`` is taken so
    the vault-level leak backstop below only runs when a live daemon could have
    written to the vault this session — a pure offline ``test_unit_`` run never
    touches the real vault.
    """
    yield
    if not server_health:
        # No daemon this session — nothing was written through it, so there is
        # nothing to sweep. Without this the teardown fired every request
        # against a dead port anyway, which the docstring above already said it
        # shouldn't: `server_health` was taken as a parameter but never read.
        return
    client = httpx.Client(base_url=BASE_URL, timeout=10, headers=_AUTH_HEADERS)

    # --- Boards ---
    try:
        resp = client.get("/boards/api/boards")
        if resp.status_code == 200:
            data = resp.json()
            boards = data.get("boards", []) if isinstance(data, dict) else []
            for b in boards:
                bid = b.get("id", "")
                name = b.get("name", "")
                if TEST_PREFIX in bid or TEST_PREFIX in name:
                    _safe_request(client, "DELETE", f"/boards/api/boards/{bid}")
                    continue
                # Also clean up any test-prefixed saved views on real boards.
                vresp = _safe_request(client, "GET", f"/boards/api/boards/{bid}/views")
                if vresp and vresp.status_code == 200:
                    for v in (vresp.json() or {}).get("views", []):
                        if TEST_PREFIX in str(v.get("name", "")):
                            _safe_request(client, "DELETE",
                                          f"/boards/api/boards/{bid}/views/{v.get('id','')}")
    except Exception:
        pass

    # --- Nature Math Simulation (vault models + uploaded references) ---
    try:
        resp = client.get("/nature-math-simulation/api/models")
        if resp.status_code == 200:
            for m in (resp.json() or {}).get("models", []):
                if TEST_PREFIX.lower() in str(m.get("id", "")).lower():
                    _safe_request(client, "DELETE", f"/nature-math-simulation/api/models/{m['id']}")
        resp = client.get("/nature-math-simulation/api/references")
        if resp.status_code == 200:
            for r in (resp.json() or {}).get("references", []):
                if TEST_PREFIX.lower() in str(r.get("name", "")).lower():   # uploads are stored lower-case
                    _safe_request(client, "DELETE", f"/nature-math-simulation/api/references/{r['name']}")
        resp = client.get("/nature-math-simulation/api/renders")
        if resp.status_code == 200:
            for r in (resp.json() or {}).get("renders", []):
                if TEST_PREFIX.lower() in str(r.get("name", "")).lower():
                    _safe_request(client, "DELETE", f"/nature-math-simulation/api/renders/{r['name']}")
    except Exception:
        pass

    # --- Expense ---
    try:
        resp = client.get("/expense/api/list")
        if resp.status_code == 200:
            entries = resp.json()
            if isinstance(entries, list):
                for e in entries:
                    if TEST_PREFIX in str(e.get("description", "")):
                        _safe_request(client, "POST", "/expense/api/delete", json={"entry": e})
    except Exception:
        pass

    # --- Library ---
    try:
        resp = client.get("/library/api/papers")
        if resp.status_code == 200:
            papers = resp.json()
            if isinstance(papers, list):
                for p in papers:
                    ck = p.get("citekey") or str(p.get("file", "")).removesuffix(".md")
                    if TEST_PREFIX in str(p.get("title", "")) or TEST_PREFIX in ck:
                        _safe_request(client, "DELETE", f"/library/api/papers/{ck}")
    except Exception:
        pass

    # --- Items ---
    try:
        resp = client.get("/items/api/items")
        if resp.status_code == 200:
            data = resp.json()
            items = data if isinstance(data, list) else data.get("items", [])
            for item in items:
                if TEST_PREFIX in str(item.get("name", "")):
                    _safe_request(client, "DELETE", f"/items/api/items/{item['id']}")
    except Exception:
        pass

    # --- Writing-editor articles ---
    try:
        resp = client.get("/writing-editor/api/articles")
        if resp.status_code == 200:
            for a in (resp.json() or {}).get("articles", []):
                if TEST_PREFIX in str(a.get("title", "")) or TEST_PREFIX in str(a.get("file", "")):
                    _safe_request(client, "DELETE", f"/writing-editor/api/articles/{a['file']}")
    except Exception:
        pass

    # --- Sheet (calc sheets) ---
    try:
        resp = client.get("/sheet/api/sheets")
        if resp.status_code == 200:
            for s in (resp.json() or {}).get("sheets", []):
                if TEST_PREFIX in str(s.get("title", "")) or TEST_PREFIX in str(s.get("id", "")):
                    _safe_request(client, "DELETE", f"/sheet/api/sheets/{s['id']}")
    except Exception:
        pass

    # --- Jianpu songs ---
    try:
        resp = client.get("/jianpu/api/songs")
        if resp.status_code == 200:
            for s in (resp.json() or {}).get("songs", []):
                if TEST_PREFIX in str(s.get("title", "")) or TEST_PREFIX in str(s.get("file", "")):
                    _safe_request(client, "DELETE", f"/jianpu/api/songs/{s['file']}")
    except Exception:
        pass

    # --- Haitao (orders + packages) ---
    try:
        resp = client.get("/haitao/api/orders")
        if resp.status_code == 200:
            data = resp.json() or {}
            for o in data.get("orders", []):
                if TEST_PREFIX in str(o.get("items_summary", "")) or TEST_PREFIX in str(o.get("store", "")) or TEST_PREFIX in str(o.get("id", "")):
                    _safe_request(client, "DELETE", f"/haitao/api/orders/{o['id']}")
        resp = client.get("/haitao/api/packages")
        if resp.status_code == 200:
            data = resp.json() or {}
            for p in data.get("packages", []):
                if TEST_PREFIX in str(p.get("notes", "")) or TEST_PREFIX in str(p.get("id", "")):
                    _safe_request(client, "DELETE", f"/haitao/api/packages/{p['id']}")
    except Exception:
        pass

    # --- Habits (under healing) ---
    try:
        resp = client.get("/healing/api/habits")
        if resp.status_code == 200:
            data = resp.json()
            habits = data if isinstance(data, list) else data.get("habits", [])
            for h in habits:
                if TEST_PREFIX in str(h.get("name", "")):
                    _safe_request(client, "DELETE", f"/healing/api/habits/{h['id']}")
    except Exception:
        pass

    # --- BESS Analyser connection projects (vault-backed; flag-gated) ---
    try:
        resp = client.get("/bess-analyser/api/projects")
        if resp.status_code == 200:
            data = resp.json() or {}
            # list returns {disabled: true} when the flag is off — nothing to clean
            for p in data.get("projects", []):
                pid = str(p.get("id", ""))
                if TEST_PREFIX in pid or TEST_PREFIX in str(p.get("name", "")):
                    _safe_request(client, "DELETE",
                                  f"/bess-analyser/api/projects/{pid}")
    except Exception:
        pass

    # --- Capture ---
    try:
        resp = client.get("/quick-action/api/list")
        if resp.status_code == 200:
            data = resp.json()
            entries = data if isinstance(data, list) else data.get("captures", [])
            for c in entries:
                text = str(c.get("text", ""))
                ts = c.get("timestamp") or c.get("ts")
                if TEST_PREFIX in text:
                    _safe_request(
                        client, "POST", "/quick-action/api/dismiss",
                        json={"timestamp": ts, "text": text},
                    )
    except Exception:
        pass

    # --- Bookmarks ---
    try:
        resp = client.get("/bookmarks/api/bookmarks")
        if resp.status_code == 200:
            data = resp.json()
            bookmarks = data if isinstance(data, list) else data.get("bookmarks", [])
            for b in bookmarks:
                if TEST_PREFIX in str(b.get("title", "")):
                    bid = b.get("id")
                    if bid is not None:
                        _safe_request(client, "DELETE", f"/bookmarks/api/bookmarks/{bid}")
    except Exception:
        pass

    # --- BookMe (test bookings + the throwaway event type) ---
    try:
        resp = client.get("/bookme/api/bookings")
        if resp.status_code == 200:
            for b in resp.json().get("bookings", []):
                if TEST_PREFIX in str(b.get("name", "")):
                    bid = b.get("id")
                    if bid:
                        _safe_request(client, "DELETE", f"/bookme/api/bookings/{bid}")
        cfg_resp = client.get("/bookme/api/config")
        if cfg_resp.status_code == 200:
            cfg = cfg_resp.json()
            kept = [et for et in cfg.get("event_types", []) if TEST_PREFIX not in str(et.get("id", ""))]
            if len(kept) != len(cfg.get("event_types", [])):
                cfg["event_types"] = kept
                _safe_request(client, "POST", "/bookme/api/config", json=cfg)
    except Exception:
        pass

    # --- Guidelines (absorbed into kb 2026-07-10) ---
    try:
        resp = client.get("/kb/api/guidelines")
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("items", []) if isinstance(data, dict) else []
            for g in items:
                gid = g.get("id", "")
                title = g.get("title", "")
                cat = g.get("category", "")
                if TEST_PREFIX in gid or TEST_PREFIX in title or TEST_PREFIX in cat:
                    _safe_request(client, "DELETE", f"/kb/api/guidelines/{gid}")
    except Exception:
        pass

    # --- KB blocks / docs ---
    # Created by `test_block_lifecycle` / `test_doc_lifecycle` with titles
    # prefixed by TEST_PREFIX. The slug is the lower-kebab-case of the title,
    # so the prefix check is case-insensitive.
    try:
        resp = client.get("/kb/api/blocks")
        if resp.status_code == 200:
            for b in resp.json().get("blocks", []):
                if TEST_PREFIX.lower() in str(b.get("slug", "")).lower():
                    _safe_request(client, "DELETE", f"/kb/api/blocks/{b['slug']}")
    except Exception:
        pass
    try:
        resp = client.get("/kb/api/docs")
        if resp.status_code == 200:
            for d in resp.json().get("docs", []):
                if TEST_PREFIX.lower() in str(d.get("slug", "")).lower():
                    _safe_request(client, "DELETE", f"/kb/api/docs/{d['slug']}")
    except Exception:
        pass

    # --- Recipes ---
    try:
        resp = client.get("/recipes/api/recipes")
        if resp.status_code == 200:
            data = resp.json()
            recipes = data if isinstance(data, list) else data.get("recipes", [])
            for r in recipes:
                if TEST_PREFIX in str(r.get("name", "")):
                    rid = r.get("id")
                    if rid is not None:
                        _safe_request(client, "DELETE", f"/recipes/api/recipes/{rid}")
    except Exception:
        pass

    # --- Assistant sessions ---
    try:
        resp = client.get("/assistant/api/sessions")
        if resp.status_code == 200:
            data = resp.json()
            sessions = data if isinstance(data, list) else data.get("sessions", [])
            for s in sessions:
                if TEST_PREFIX in str(s.get("name", "")):
                    sid = s.get("id")
                    if sid is not None:
                        _safe_request(client, "DELETE", f"/assistant/api/sessions/{sid}")
    except Exception:
        pass

    # --- Shadowing passages ---
    try:
        resp = client.get("/shadowing/api/passages")
        if resp.status_code == 200:
            data = resp.json()
            passages = data.get("passages", []) if isinstance(data, dict) else []
            for p in passages:
                title = str(p.get("title", ""))
                slug = str(p.get("slug", ""))
                if TEST_PREFIX in title or TEST_PREFIX.lower() in slug.lower():
                    _safe_request(client, "DELETE", f"/shadowing/api/passages/{slug}")
    except Exception:
        pass

    # --- Agent sessions ---
    try:
        resp = client.get("/agent/api/sessions")
        if resp.status_code == 200:
            data = resp.json()
            sessions = data if isinstance(data, list) else data.get("sessions", [])
            for s in sessions:
                if TEST_PREFIX in str(s.get("name", "")):
                    sid = s.get("id")
                    if sid is not None:
                        _safe_request(client, "DELETE", f"/agent/api/sessions/{sid}")
    except Exception:
        pass

    # --- Rooms agents (scroll personas + org persona mirrors land here) ---
    # Scroll's POST /api/personas creates a rooms agent; without this sweep the
    # leftover agent makes the next run's persona-create fail with
    # "already exists" (no per-app delete endpoint exists on scroll).
    try:
        resp = client.get("/rooms/api/agents")
        if resp.status_code == 200:
            agents = resp.json()
            for a in agents if isinstance(agents, list) else []:
                aid = str(a.get("id", ""))
                if TEST_PREFIX in str(a.get("name", "")) or TEST_PREFIX.lower() in aid:
                    _safe_request(client, "DELETE", f"/rooms/api/agents/{aid}")
    except Exception:
        pass

    # --- Scroll relationships (delta-based store accrues across runs) ---
    # POST /scroll/api/relationships/{a}/{b} applies DELTAS clamped to [-1,1];
    # a leftover test record saturates and breaks the next run's round-trip
    # assertion. There is no delete endpoint, so reset by posting the negated
    # axis values (x + (-x) == 0.0 exactly in IEEE floats).
    try:
        resp = client.get("/scroll/api/relationships")
        if resp.status_code == 200:
            rels = resp.json()
            for rel in rels if isinstance(rels, list) else []:
                a, b = str(rel.get("a", "")), str(rel.get("b", ""))
                if TEST_PREFIX not in a and TEST_PREFIX not in b:
                    continue
                deltas = {
                    k: -v for k, v in rel.items()
                    if isinstance(v, (int, float)) and not isinstance(v, bool)
                    and k not in ("updated_at",) and v != 0.0
                }
                if deltas:
                    _safe_request(client, "POST", f"/scroll/api/relationships/{a}/{b}", json=deltas)
    except Exception:
        pass

    # --- Workout sessions ---
    try:
        resp = client.get("/workout/api/sessions")
        if resp.status_code == 200:
            data = resp.json()
            sessions = data if isinstance(data, list) else data.get("sessions", [])
            for s in sessions:
                if TEST_PREFIX in str(s.get("name", "")):
                    sid = s.get("id")
                    if sid is not None:
                        _safe_request(client, "DELETE", f"/workout/api/sessions/{sid}")
    except Exception:
        pass

    # --- Settings test keys ---
    try:
        resp = client.get("/settings/api/get")
        if resp.status_code == 200:
            data = resp.json()
            settings = data if isinstance(data, dict) else {}
            for key in list(settings.keys()):
                if TEST_PREFIX in key:
                    _safe_request(
                        client, "POST", "/settings/api/reset",
                        json={"key": key},
                    )
    except Exception:
        pass

    # --- Plan scenarios ---
    try:
        resp = client.get("/plan-scenarios/api/plans")
        if resp.status_code == 200:
            data = resp.json()
            plans = data.get("plans", []) if isinstance(data, dict) else []
            for p in plans:
                blob = str(p.get("title", "")) + " " + str(p.get("brief", ""))
                pid = p.get("plan_id")
                if pid and TEST_PREFIX in blob:
                    _safe_request(client, "DELETE", f"/plan-scenarios/api/plan/{pid}")
    except Exception:
        pass

    # --- Reports test docs ---
    try:
        resp = client.get("/reports/api/reports")
        if resp.status_code == 200:
            data = resp.json()
            reports = data if isinstance(data, list) else data.get("reports", [])
            for r in reports:
                title = str(r.get("title", ""))
                rid = r.get("id")
                if rid and TEST_PREFIX in title:
                    _safe_request(client, "DELETE", f"/reports/api/reports/{rid}")
    except Exception:
        pass

    # --- Projects test tasks (best effort) ---
    try:
        resp = client.get("/projects/api/all-tasks")
        if resp.status_code == 200:
            data = resp.json()
            tasks = data if isinstance(data, list) else data.get("tasks", [])
            for t in tasks:
                if TEST_PREFIX in str(t.get("text", "")):
                    project = t.get("project") or t.get("project_id")
                    if project:
                        _safe_request(
                            client, "POST",
                            f"/projects/api/projects/{project}/tasks/toggle",
                            json={"text": t.get("text")},
                        )
    except Exception:
        pass

    # --- Canvas (boards written as .md files) ---
    try:
        resp = client.get("/canvas/api/boards")
        if resp.status_code == 200:
            for b in (resp.json() or {}).get("boards", []):
                bid = str(b.get("board_id", ""))
                if TEST_PREFIX in bid:
                    _safe_request(client, "POST", "/canvas/api/board/delete",
                                  json={"board_id": bid})
    except Exception:
        pass

    # --- Places (vault-backed) ---
    try:
        resp = client.get("/places/api/places")
        if resp.status_code == 200:
            data = resp.json()
            places = data if isinstance(data, list) else []
            for p in places:
                name = str(p.get("name", ""))
                if TEST_PREFIX in name or "pw-dogfood-" in str(p.get("file", "")):
                    fname = p.get("file") or p.get("filename")
                    if fname:
                        _safe_request(client, "DELETE", f"/places/api/places/{fname}")
    except Exception:
        pass

    # --- Earthing (vault-backed substation projects) ---
    try:
        resp = client.get("/earthing/api/projects")
        if resp.status_code == 200:
            data = resp.json()
            projects = data.get("projects", []) if isinstance(data, dict) else []
            for p in projects:
                name = str(p.get("name", ""))
                pid = p.get("id")
                if pid and TEST_PREFIX in name:
                    _safe_request(client, "DELETE",
                                  f"/earthing/api/projects/{pid}")
    except Exception:
        pass

    # --- Forge (vault-backed native projects) ---
    # Ids are slugged-lowercase so name might be uppercase TEST_PREFIX or
    # the id itself carries lowercased prefix. Match both shapes.
    try:
        resp = client.get("/forge/api/projects")
        if resp.status_code == 200:
            data = resp.json()
            projects = data.get("projects", []) if isinstance(data, dict) else []
            lower_prefix = TEST_PREFIX.lower()
            for p in projects:
                pid = str(p.get("id", ""))
                name = str(p.get("name", ""))
                if TEST_PREFIX in name or lower_prefix in pid:
                    # Soft delete only — leaves the external repo dir on
                    # disk if scaffold ever wrote anything (tests target a
                    # throwaway HOME, so disk leak is bounded).
                    _safe_request(client, "DELETE",
                                  f"/forge/api/projects/{pid}")
    except Exception:
        pass

    # --- Held engineering apps: tests/cleanup_engineering.py ---
    # Kept out of this file so conftest ships in the public snapshot (see that
    # module's docstring). Absent there, so the call is skipped.
    if cleanup_engineering_apps is not None:
        cleanup_engineering_apps(client, _safe_request, TEST_PREFIX)

    # --- Interference (vault-backed EMF studies) ---
    try:
        resp = client.get("/interference/api/studies")
        if resp.status_code == 200:
            studies = (resp.json() or {}).get("studies", [])
            for s in studies:
                if TEST_PREFIX in str(s.get("name", "")):
                    sid = s.get("id")
                    if sid:
                        _safe_request(client, "DELETE",
                                      f"/interference/api/studies/{sid}")
    except Exception:
        pass

    # --- Lightning (vault-backed rolling-sphere studies) ---
    try:
        resp = client.get("/lightning/api/studies")
        if resp.status_code == 200:
            studies = (resp.json() or {}).get("studies", [])
            for s in studies:
                if TEST_PREFIX in str(s.get("name", "")):
                    sid = s.get("id")
                    if sid:
                        _safe_request(client, "DELETE",
                                      f"/lightning/api/studies/{sid}")
    except Exception:
        pass

    # --- Jobs (vault-backed applications) ---
    try:
        resp = client.get("/jobs/api/applications")
        if resp.status_code == 200:
            data = resp.json()
            apps_list = data.get("applications") if isinstance(data, dict) else data
            for a in apps_list or []:
                if TEST_PREFIX in str(a.get("company", "")):
                    aid = a.get("id")
                    if aid:
                        _safe_request(client, "DELETE",
                                      "/jobs/api/applications/delete",
                                      json={"id": aid})
    except Exception:
        pass

    # --- Jobs outreach (LinkedIn/email contact log) ---
    try:
        resp = client.get("/jobs/api/outreach")
        if resp.status_code == 200:
            items = (resp.json() or {}).get("items", [])
            for o in items:
                blob = str(o.get("person", "")) + " " + str(o.get("company", ""))
                if TEST_PREFIX in blob:
                    oid = o.get("id")
                    if oid:
                        _safe_request(client, "DELETE", f"/jobs/api/outreach/{oid}")
    except Exception:
        pass

    # --- Media highlights (vault-backed via SQLite index) ---
    try:
        resp = client.get("/media/api/highlights")
        if resp.status_code == 200:
            data = resp.json()
            hls = data if isinstance(data, list) else data.get("highlights", [])
            for h in hls:
                blob = str(h.get("text", "")) + " " + str(h.get("source", ""))
                if TEST_PREFIX in blob:
                    hid = h.get("id") or h.get("highlight_id")
                    if hid:
                        _safe_request(client, "DELETE",
                                      f"/media/api/highlights/{hid}")
    except Exception:
        pass

    # --- Publish drafts (vault files) ---
    try:
        resp = client.get("/publish/api/sources?include_drafts=1")
        if resp.status_code == 200:
            data = resp.json()
            items = data if isinstance(data, list) else data.get("sources", [])
            for s in items:
                title = str(s.get("title", ""))
                path = str(s.get("path", ""))
                if TEST_PREFIX in title or TEST_PREFIX in path:
                    file_path = s.get("path")
                    if file_path:
                        try:
                            from pathlib import Path
                            p = Path(file_path)
                            if p.exists() and p.is_file():
                                p.unlink()
                        except Exception:
                            pass
    except Exception:
        pass

    # --- Music studio lyrics (vault files with dogfood markers) ---
    # No direct API delete path; best-effort filesystem sweep.
    try:
        import tomllib
        from pathlib import Path
        with open("emptyos.toml", "rb") as f:
            cfg = tomllib.load(f)
        vault = Path(cfg.get("notes", {}).get("path", ""))
        if vault.exists():
            for sub in ("30_Resources/Lyrics", "70_Media/Music/Lyrics",
                        "70_Media/Music/Songs"):
                base = vault / sub
                if base.exists():
                    for f in base.rglob("*.md"):
                        try:
                            if TEST_PREFIX in f.read_text(encoding="utf-8", errors="ignore"):
                                f.unlink()
                        except Exception:
                            continue
    except Exception:
        pass

    # --- Improv (vault session notes + local session JSON) ---
    try:
        from pathlib import Path
        import tomllib
        # Local data sweep: any sessions whose persona carries TEST_PREFIX.
        repo_data = Path("data/apps/improv/sessions")
        if repo_data.exists():
            for f in repo_data.glob("*.json"):
                try:
                    import json as _json
                    s = _json.loads(f.read_text(encoding="utf-8"))
                    if TEST_PREFIX in str(s.get("persona", "")):
                        f.unlink()
                except Exception:
                    continue
        # Vault sweep: any saved improv-session note that mentions the test
        # persona prefix anywhere in body / frontmatter.
        with open("emptyos.toml", "rb") as f:
            cfg = tomllib.load(f)
        vault = Path(cfg.get("notes", {}).get("path", ""))
        sess_dir = vault / "30_Resources/EmptyOS/improv/sessions"
        if sess_dir.exists():
            for f in sess_dir.glob("*.md"):
                try:
                    if TEST_PREFIX in f.read_text(encoding="utf-8", errors="ignore"):
                        f.unlink()
                except Exception:
                    continue
    except Exception:
        pass

    # --- Fiction stories (whole directory) ---
    try:
        import shutil
        import tomllib
        from pathlib import Path
        with open("emptyos.toml", "rb") as f:
            cfg = tomllib.load(f)
        vault = Path(cfg.get("notes", {}).get("path", ""))
        if vault.exists():
            for base in ("10_Projects", "30_Resources/Fiction", "30_Resources/Stories"):
                root = vault / base
                if not root.exists():
                    continue
                for d in root.iterdir():
                    if d.is_dir() and ("pw-dogfood-" in d.name
                                       or TEST_PREFIX.lower() in d.name.lower()):
                        shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass

    # --- Worklog sentinel days (whole test-created day notes) ---
    # Worklog tests write to WORKLOG_TEST_DATES, never date.today(), because
    # /api/plan and /api/update REPLACE their section — a test on today's note
    # would destroy the user's real Plan, and the leak backstop below cannot
    # repair prose. Because the sentinel day is entirely test-created, the whole
    # file is deletable; no line-stripping (which orphans ### project headings
    # and leaves permanent manual-review debt) is needed.
    try:
        import tomllib
        from pathlib import Path
        with open("emptyos.toml", "rb") as f:
            cfg = tomllib.load(f)
        vault = Path(cfg.get("notes", {}).get("path", ""))
        if vault.exists():
            years = set()
            for day_s in WORKLOG_TEST_DATES:
                note = vault / "60_Worklogs" / day_s[:4] / f"{day_s}.md"
                years.add(note.parent)
                try:
                    note.unlink(missing_ok=True)
                except Exception:
                    continue
            for year_dir in years:
                try:
                    if year_dir.is_dir() and not any(year_dir.iterdir()):
                        year_dir.rmdir()   # no empty husk left behind
                except Exception:
                    continue
    except Exception:
        pass

    # --- Designer outputs (whole artifact dirs) ---
    try:
        import shutil
        import tomllib
        from pathlib import Path
        with open("emptyos.toml", "rb") as f:
            cfg = tomllib.load(f)
        vault = Path(cfg.get("notes", {}).get("path", ""))
        root = vault / "30_Resources/EmptyOS/designer/outputs"
        if root.exists():
            for d in root.iterdir():
                if not d.is_dir():
                    continue
                rec = d / "record.md"
                try:
                    if rec.exists() and TEST_PREFIX in rec.read_text(encoding="utf-8", errors="ignore"):
                        shutil.rmtree(d, ignore_errors=True)
                except Exception:
                    continue
    except Exception:
        pass

    # Speaking Practice stores one JSON file per attempt and has no delete
    # endpoint by design. Test-created attempts carry client_tag=TEST_PREFIX;
    # remove only those records and their same-id recording files.
    try:
        root = Path(__file__).resolve().parent.parent / "data" / "apps" / "speaking-practice"
        attempts = root / "attempts"
        audio = root / "audio"
        if attempts.is_dir():
            for record in attempts.glob("*.json"):
                try:
                    row = json.loads(record.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if TEST_PREFIX not in str(row.get("client_tag", "")):
                    continue
                attempt_id = str(row.get("id") or "")
                record.unlink(missing_ok=True)
                if attempt_id and audio.is_dir():
                    for clip in audio.glob(f"attempt-{attempt_id}.*"):
                        clip.unlink(missing_ok=True)
    except Exception:
        pass

    client.close()

    if os.environ.get("EOS_SKIP_LEAK_GUARD"):
        return
    if not server_health:
        # No live daemon this session → no writes happened → don't touch the
        # real vault or data dir (keeps offline test_unit_/test_sdk_ runs
        # side-effect-free).
        return

    # --- SRS-store leak backstop --------------------------------------------
    # The grade tests write TEST_PREFIX entries into data/apps/{learn,
    # dictionary}/srs.json, and neither app has a delete endpoint for the API
    # sweeps above to use — adding product API for a test's benefit is the
    # wrong trade, so the leak was documented as accepted noise and duly
    # accumulated (two were still sitting there on 2026-08-06, pinned at the
    # old scheduler's 50-year ceiling). These stores are flat {id: entry}
    # dicts, so dropping a prefixed key is unambiguous — the same "unambiguous
    # artifacts only" bar the vault backstop below holds itself to.
    try:
        for store in sorted((Path(__file__).resolve().parent.parent
                             / "data" / "apps").glob("*/srs.json")):
            entries = json.loads(store.read_text(encoding="utf-8"))
            if not isinstance(entries, dict):
                continue
            kept = {k: v for k, v in entries.items() if TEST_PREFIX not in str(k)}
            if len(kept) != len(entries):
                store.write_text(json.dumps(kept, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
                print(f"\n[leak-guard] dropped {len(entries) - len(kept)} test "
                      f"SRS entr(ies) from {store.parent.name}/srs.json")
    except Exception:
        pass

    # --- Vault-level leak backstop ------------------------------------------
    # The per-app API sweeps above are best-effort and only cover apps with a
    # cleanup block. Anything they miss (cad outputs, journal/capture entries
    # appended to real dailies, future apps) accumulated TEST_PREFIX fixtures in
    # the *real* vault — 6,954 were purged by hand on 2026-05-30. This backstop
    # makes that unrecurrable: after every session it sweeps the vault the
    # daemon writes to and removes the unambiguous test artifacts (whole
    # test-created files + single test list-lines), leaving prose-pollution for
    # manual review. Content is read only for notes modified since the last
    # sweep that ended fully clean (prefixed names are matched everywhere), with
    # a full read when there is no marker or the last full one is over a week
    # old. A full read of a 33k-note / 1 GB vault is 3s warm but 86s+ on a cold
    # disk (measured 2026-09-28). The marker advances only when
    # sweep_was_clean(), so a killed run, a late write, an unreadable note or a
    # failed delete is still read next time. Skip with EOS_SKIP_LEAK_GUARD=1.
    # Strict (CI) fails the session on any leftover. See
    # scripts/check_vault_test_leak.py + .claude/rules/vault-operator.md.
    try:
        import importlib.util
        repo = Path(__file__).resolve().parent.parent
        with open(repo / "emptyos.toml", "rb") as f:
            vault = Path((tomllib.load(f).get("notes") or {}).get("path") or "")
        if not vault.exists():
            return
        spec = importlib.util.spec_from_file_location(
            "check_vault_test_leak", repo / "scripts" / "check_vault_test_leak.py")
        cvtl = importlib.util.module_from_spec(spec)
        import sys as _sys
        _sys.modules["check_vault_test_leak"] = cvtl
        spec.loader.exec_module(cvtl)

        marker = repo / "data" / "leak-guard" / "last-clean-sweep.json"
        sweep_started = time.time()
        since = cvtl.sweep_cutoff(marker, vault)
        leaks = cvtl.scan(vault, TEST_PREFIX, since=since)
        summary = cvtl.purge(leaks, owned=True, lines=True)
        purged = summary["files_deleted"] + summary["dirs_deleted"] + summary["lines_stripped"]
        if purged:
            print(f"\n[leak-guard] swept {summary['files_deleted']} file(s), "
                  f"{summary['dirs_deleted']} artifact-dir(s), "
                  f"{summary['lines_stripped']} test list-line(s) from {vault}")
        if summary.get("notes_skipped"):
            print(f"[leak-guard] {summary['notes_skipped']} note(s) left untouched: changed "
                  "since the scan (another purge or a writer), unreadable, or locked; "
                  "retry: python scripts/check_vault_test_leak.py --purge")
        if cvtl.sweep_was_clean(leaks, summary):
            cvtl.record_clean_sweep(marker, vault, sweep_started, full=since is None)
        if leaks.review:
            files = sorted({str(p.relative_to(vault)).replace(chr(92), '/')
                            for p, _, _ in leaks.review})
            print(f"[leak-guard] {len(leaks.review)} test line(s) need MANUAL review "
                  f"(prose pollution, not auto-stripped) in: {', '.join(files)}")
            print("[leak-guard] inspect with: python scripts/check_vault_test_leak.py")
            if os.environ.get("EOS_LEAK_GATE_STRICT"):
                raise AssertionError(
                    f"vault test-fixture leak: {len(leaks.review)} line(s) need manual review")
    except AssertionError:
        raise
    except Exception:
        # Never let the backstop break a test session.
        pass
