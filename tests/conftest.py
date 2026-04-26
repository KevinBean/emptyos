"""Shared fixtures for EmptyOS E2E tests."""

import pytest
import httpx

from helpers import TEST_PREFIX, BASE_URL


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
def server_health():
    """Skip entire suite if EmptyOS is not running."""
    try:
        resp = httpx.get(f"{BASE_URL}/api/health", timeout=5)
        assert resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException, AssertionError):
        pytest.skip("EmptyOS not running on localhost:9000")


@pytest.fixture(scope="session")
def app_list():
    """Fetch all apps from the running server."""
    resp = httpx.get(f"{BASE_URL}/api/apps", timeout=10)
    return resp.json()


@pytest.fixture(scope="session")
def llm_available():
    """Check if think capability has an online provider."""
    try:
        resp = httpx.get(f"{BASE_URL}/api/capabilities", timeout=5)
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


@pytest.fixture
def page_errors(page):
    """Collect JS errors during a test. Assert empty after test."""
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    return errors


@pytest.fixture(scope="session")
def http_client():
    """Shared httpx client for API tests."""
    client = httpx.Client(base_url=BASE_URL, timeout=15)
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
    client = httpx.Client(base_url=BASE_URL, timeout=10)

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
