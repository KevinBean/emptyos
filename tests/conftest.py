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

from helpers import BASE_URL, TEST_PREFIX

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


def pytest_collection_modifyitems(config, items):
    """Run non-Playwright items first so pytest-asyncio tests don't collide with
    Playwright's session-scoped running loop. Within each bucket, preserve the
    original collection order."""
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


@pytest.fixture(scope="session", autouse=True)
def server_health():
    """Skip entire suite if EmptyOS is not running."""
    try:
        resp = httpx.get(f"{BASE_URL}/api/health", timeout=5, headers=_AUTH_HEADERS)
        assert resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException, AssertionError):
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


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    """Inject Authorization header into every Playwright request when auth_token is set.

    pytest-playwright passes this dict to browser.new_context(). Adding
    extra_http_headers means every page.goto() / fetch carries the bearer token,
    matching the daemon's private-mode auth gate.
    """
    if _AUTH_TOKEN:
        headers = dict(browser_context_args.get("extra_http_headers") or {})
        headers["Authorization"] = f"Bearer {_AUTH_TOKEN}"
        return {**browser_context_args, "extra_http_headers": headers}
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
def cleanup_after_all():
    """Clean up test data after all tests complete.

    Iterates each app's list endpoint, finds entries containing TEST_PREFIX,
    and deletes them via the appropriate endpoint.
    """
    yield
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

    # --- Cables (vault-backed reticulation projects) ---
    try:
        resp = client.get("/cables/api/projects")
        if resp.status_code == 200:
            data = resp.json()
            projects = data.get("projects", []) if isinstance(data, dict) else []
            for p in projects:
                name = str(p.get("name", ""))
                pid = p.get("id")
                if pid and TEST_PREFIX in name:
                    _safe_request(client, "DELETE",
                                  f"/cables/api/projects/{pid}")
    except Exception:
        pass

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

    # --- Geo-CAD (vault-backed georeferenced layers) ---
    try:
        resp = client.get("/geo-cad/api/layers")
        if resp.status_code == 200:
            layers = (resp.json() or {}).get("layers", [])
            for layer in layers:
                if TEST_PREFIX in str(layer.get("title", "")):
                    lid = layer.get("id")
                    if lid:
                        _safe_request(client, "DELETE",
                                      f"/geo-cad/api/layers/{lid}")
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
            for sub in ("30_Resources/Lyrics", "30_Resources/Music/Lyrics",
                        "30_Resources/Music/Songs"):
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

    client.close()
