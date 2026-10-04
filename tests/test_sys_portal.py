"""System app tests: Portal — folders + pins + UI smoke."""
import pytest

from helpers import TEST_PREFIX, assert_dict_response, assert_ok
from page_helpers import assert_no_js_errors


def _make_folder_name(suffix: str = "") -> str:
    return f"{TEST_PREFIX}room-{suffix}" if suffix else f"{TEST_PREFIX}room"


def _cleanup_test_folders(client) -> None:
    """Delete any folder whose name starts with TEST_PREFIX. Called as a
    fail-safe; individual tests should also delete what they create."""
    try:
        resp = client.get("/portal/api/folders")
        if resp.status_code != 200:
            return
        for f in resp.json().get("folders", []):
            name = str(f.get("name", ""))
            if TEST_PREFIX in name:
                fid = f.get("id")
                if fid:
                    client.delete(f"/portal/api/folders/{fid}")
    except Exception:
        pass


def _cleanup_test_pins(client) -> None:
    """Remove any pinned thread id that starts with TEST_PREFIX."""
    try:
        resp = client.get("/portal/api/pins")
        if resp.status_code != 200:
            return
        for tid in resp.json().get("threads", []):
            if str(tid).startswith(TEST_PREFIX):
                client.delete(f"/portal/api/pins/{tid}")
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _portal_test_cleanup(http_client):
    """Run cleanup before and after each test in this module."""
    _cleanup_test_folders(http_client)
    _cleanup_test_pins(http_client)
    yield
    _cleanup_test_folders(http_client)
    _cleanup_test_pins(http_client)


@pytest.mark.api
class TestPortalFoldersAPI:
    def test_folders_list_endpoint(self, http_client):
        data = assert_dict_response(http_client.get("/portal/api/folders"))
        assert "folders" in data
        assert isinstance(data["folders"], list)

    def test_create_folder(self, http_client):
        payload = {"name": _make_folder_name("create"), "default_mode": "think"}
        f = assert_ok(http_client.post("/portal/api/folders", json=payload))
        assert f.get("id", "").startswith("fld-")
        assert f.get("name") == payload["name"]
        assert f.get("default_mode") == "think"
        assert f.get("thread_ids") == []
        assert f.get("system_prompt") == ""
        assert f.get("model") == ""

    def test_create_folder_requires_name(self, http_client):
        resp = http_client.post("/portal/api/folders", json={"name": ""})
        data = resp.json()
        assert "error" in data

    def test_create_folder_normalizes_invalid_mode(self, http_client):
        payload = {"name": _make_folder_name("mode"), "default_mode": "BOGUS"}
        f = assert_ok(http_client.post("/portal/api/folders", json=payload))
        assert f.get("default_mode") == "think"  # invalid → think fallback

    def test_update_folder_fields(self, http_client):
        f = http_client.post("/portal/api/folders", json={"name": _make_folder_name("upd")}).json()
        fid = f["id"]
        patch = {
            "name": _make_folder_name("upd-renamed"),
            "default_mode": "code",
            "model": "claude-cli",
            "system_prompt": "be careful",
        }
        updated = assert_ok(http_client.patch(f"/portal/api/folders/{fid}", json=patch))
        assert updated["name"] == patch["name"]
        assert updated["default_mode"] == "code"
        assert updated["model"] == "claude-cli"
        assert updated["system_prompt"] == "be careful"

    def test_update_folder_clears_model_with_empty_string(self, http_client):
        # Empty string clears (per the api_update_folder docstring); only
        # `None` is treated as "field absent". Important: claude.ai-style
        # "no model override" semantics.
        f = http_client.post("/portal/api/folders", json={"name": _make_folder_name("clr")}).json()
        fid = f["id"]
        http_client.patch(f"/portal/api/folders/{fid}", json={"model": "ollama"})
        cleared = assert_ok(http_client.patch(f"/portal/api/folders/{fid}", json={"model": ""}))
        assert cleared["model"] == ""

    def test_delete_folder_releases_threads(self, http_client):
        f = http_client.post("/portal/api/folders", json={"name": _make_folder_name("del")}).json()
        fid = f["id"]
        http_client.post(
            f"/portal/api/folders/{fid}/threads",
            json={"thread_id": f"{TEST_PREFIX}t1"},
        )
        result = assert_ok(http_client.delete(f"/portal/api/folders/{fid}"))
        assert result["deleted"] == fid
        assert f"{TEST_PREFIX}t1" in result["freed_threads"]
        # Folder no longer in list.
        listing = http_client.get("/portal/api/folders").json()
        assert all(x.get("id") != fid for x in listing.get("folders", []))

    def test_attach_thread_appears_in_folder(self, http_client):
        f = http_client.post("/portal/api/folders", json={"name": _make_folder_name("att")}).json()
        fid = f["id"]
        tid = f"{TEST_PREFIX}thread-a"
        updated = assert_ok(http_client.post(
            f"/portal/api/folders/{fid}/threads", json={"thread_id": tid},
        ))
        assert tid in updated["thread_ids"]

    def test_attach_moves_thread_from_prior_folder(self, http_client):
        # A thread can only belong to one folder. Attaching to folder B
        # must pull it out of folder A.
        a = http_client.post("/portal/api/folders", json={"name": _make_folder_name("a")}).json()
        b = http_client.post("/portal/api/folders", json={"name": _make_folder_name("b")}).json()
        tid = f"{TEST_PREFIX}thread-move"
        http_client.post(f"/portal/api/folders/{a['id']}/threads", json={"thread_id": tid})
        http_client.post(f"/portal/api/folders/{b['id']}/threads", json={"thread_id": tid})
        a_after = next(x for x in http_client.get("/portal/api/folders").json()["folders"] if x["id"] == a["id"])
        b_after = next(x for x in http_client.get("/portal/api/folders").json()["folders"] if x["id"] == b["id"])
        assert tid not in a_after["thread_ids"]
        assert tid in b_after["thread_ids"]

    def test_detach_thread(self, http_client):
        f = http_client.post("/portal/api/folders", json={"name": _make_folder_name("det")}).json()
        fid = f["id"]
        tid = f"{TEST_PREFIX}thread-det"
        http_client.post(f"/portal/api/folders/{fid}/threads", json={"thread_id": tid})
        result = assert_ok(http_client.delete(f"/portal/api/folders/{fid}/threads/{tid}"))
        assert tid not in result["thread_ids"]

    def test_folder_of_thread_reverse_lookup(self, http_client):
        f = http_client.post("/portal/api/folders", json={"name": _make_folder_name("rev")}).json()
        fid = f["id"]
        tid = f"{TEST_PREFIX}thread-rev"
        http_client.post(f"/portal/api/folders/{fid}/threads", json={"thread_id": tid})
        data = assert_dict_response(http_client.get(f"/portal/api/threads/{tid}/folder"))
        assert data["thread_id"] == tid
        assert data["folder_id"] == fid
        # Unknown thread → folder_id is null.
        unknown = assert_dict_response(http_client.get(f"/portal/api/threads/{TEST_PREFIX}nope/folder"))
        assert unknown["folder_id"] is None


@pytest.mark.api
class TestPortalPinsAPI:
    def test_pins_list_endpoint(self, http_client):
        data = assert_dict_response(http_client.get("/portal/api/pins"))
        assert "threads" in data
        assert isinstance(data["threads"], list)

    def test_pin_thread_inserts_at_top(self, http_client):
        # Newest pin floats to position 0.
        first = f"{TEST_PREFIX}pin-1"
        second = f"{TEST_PREFIX}pin-2"
        http_client.post(f"/portal/api/pins/{first}")
        result = assert_dict_response(http_client.post(f"/portal/api/pins/{second}"))
        assert result["threads"][0] == second
        assert first in result["threads"]

    def test_pin_idempotent(self, http_client):
        tid = f"{TEST_PREFIX}pin-idem"
        http_client.post(f"/portal/api/pins/{tid}")
        # Pinning again should not duplicate.
        result = assert_dict_response(http_client.post(f"/portal/api/pins/{tid}"))
        assert result["threads"].count(tid) == 1

    def test_unpin_thread(self, http_client):
        tid = f"{TEST_PREFIX}pin-unp"
        http_client.post(f"/portal/api/pins/{tid}")
        result = assert_dict_response(http_client.delete(f"/portal/api/pins/{tid}"))
        assert tid not in result["threads"]

    def test_unpin_unknown_is_idempotent(self, http_client):
        # Unpinning a thread that isn't pinned should not error.
        resp = http_client.delete(f"/portal/api/pins/{TEST_PREFIX}never-pinned")
        assert resp.status_code == 200


@pytest.mark.api
class TestPortalModeBackendsAPI:
    """Read-only smokes over the endpoints portal's unified door rides.

    No LLM sends — these pin the response *shapes* the portal frontend
    depends on (sidebar lists, home-strip chips), so a backend shape change
    breaks here instead of silently blanking the door.
    """

    def test_rooms_global_pending_is_list(self, http_client):
        resp = http_client.get("/rooms/api/pending")
        assert_ok(resp)
        assert isinstance(resp.json(), list)

    def test_agent_sessions_shape(self, http_client):
        resp = http_client.get("/agent/api/sessions")
        assert_ok(resp)
        rows = resp.json()
        assert isinstance(rows, list)
        if rows:
            row = rows[0]
            for key in ("id", "name", "message_count"):
                assert key in row, f"agent session row missing {key!r}"

    def test_assistant_sessions_shape(self, http_client):
        resp = http_client.get("/assistant/api/sessions")
        assert_ok(resp)
        rows = resp.json()
        assert isinstance(rows, list)
        if rows:
            row = rows[0]
            for key in ("id", "name"):
                assert key in row, f"assistant session row missing {key!r}"

    def test_billing_today_has_cost(self, http_client):
        resp = http_client.get("/billing/api/today")
        assert_ok(resp)
        body = resp.json()
        assert "cost" in body and isinstance(body["cost"], (int, float))


@pytest.mark.interactive
class TestPortalUI:
    def test_page_loads_with_verb_chips(self, app_page, page_errors):
        page = app_page("portal")
        # All five capability chips render — three real verbs + Think (default) + Adaptive (stub).
        for verb in ("think", "capture", "find", "learn", "adaptive"):
            page.locator(f'.portal-chip[data-verb="{verb}"]').wait_for(state="visible", timeout=4000)

    def test_sidebar_footer_links_and_one_scroller(self, app_page, page_errors):
        page = app_page("portal")
        foot = page.locator(".portal-side-foot")
        foot.wait_for(state="visible", timeout=8000)
        # "Project" also matches chat-first's rename to "Project tracker".
        for label in ("Project", "Settings", "All apps"):
            assert foot.locator(".portal-side-link", has_text=label).count() == 1, label
        # The footer stays on screen: the list scrolls, not the whole sidebar.
        assert page.evaluate(
            "getComputedStyle(document.querySelector('#portal-shell .eos-chat-sidebar')).overflowY") == "hidden"
        assert page.evaluate("getComputedStyle(document.getElementById('portal-side-scroll')).overflowY") == "auto"
        box = foot.bounding_box()
        assert box and box["y"] + box["height"] <= page.viewport_size["height"] + 1

    def test_backend_mode_switch_renders(self, app_page, page_errors):
        page = app_page("portal")
        # The unified-door mode switch carries the core conversation backends.
        for bk in ("rooms", "assistant", "agent"):
            page.locator(f'.portal-bk[data-backend="{bk}"]').wait_for(state="visible", timeout=4000)

    def test_backend_switch_persists_across_reload(self, app_page, page_errors):
        page = app_page("portal")
        asst = page.locator('.portal-bk[data-backend="assistant"]')
        asst.wait_for(state="visible", timeout=4000)
        asst.click()
        page.wait_for_timeout(200)
        assert "active" in (asst.get_attribute("class") or ""), "Assistant should be active after click"
        page.reload()
        asst = page.locator('.portal-bk[data-backend="assistant"]')
        asst.wait_for(state="visible", timeout=4000)
        page.wait_for_timeout(400)  # _syncBackendUI restores from localStorage on load
        assert "active" in (asst.get_attribute("class") or ""), "Assistant should persist across reload"
        # Restore the default so this test doesn't leak state to others.
        page.locator('.portal-bk[data-backend="rooms"]').click()
        page.wait_for_timeout(150)

    def test_home_strip_container_present(self, app_page, page_errors):
        page = app_page("portal")
        # The strip is state-dependent (hidden when nothing to show) — assert the
        # container exists and the page loads error-free, not its visibility.
        assert page.locator("#portal-home-strip").count() == 1
        assert page.locator("#portal-continue").count() == 1
        assert page.locator("#portal-status-chips").count() == 1

    def test_code_mode_opens_workspace_iframe(self, app_page, page_errors):
        page = app_page("portal")
        page.wait_for_timeout(600)  # let _syncBackendAvailability run after the app catalog loads
        code_btn = page.locator('.portal-bk[data-backend="code"]')
        if code_btn.count() == 0 or not code_btn.is_visible():
            pytest.skip("code app not installed on this deployment")
        code_btn.click()
        page.locator(".portal-iframe-pane.open").wait_for(state="visible", timeout=5000)
        src = page.locator("#portal-iframe-frame").get_attribute("src") or ""
        assert "/code/" in src and "embed=1" in src, f"expected embedded code workspace, got {src!r}"


# ── Portal as the landing surface (2026-10-03) ──────────────────────────────
# "/" lands on portal when it is loaded (server.py home_target), and portal's
# hero carries a home board — the hub's companion lanes + panel contributions,
# painted by the shared /static/eos-hub-renderers.js from /hub/api/*.

# Same allowlist test_sys_hub.py carries: WS auth returns 403 under the test
# header and Google Fonts warns on CORS — neither is a page bug.
_HOME_UI_ALLOW = [
    r"Failed to load resource",
    r"WebSocket connection.*failed",
    r"font.*CORS policy",
]


@pytest.mark.api
class TestPortalLanding:
    def test_root_redirects_to_the_home_the_server_reports(self, http_client):
        health = http_client.get("/api/health").json()
        assert health.get("home"), "GET /api/health should name where / lands"
        resp = http_client.get("/", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers.get("location") == health["home"]

    def test_portal_is_home_unless_the_deployment_overrides_it(self, http_client, app_list):
        ids = {a.get("id") if isinstance(a, dict) else a for a in (app_list or [])}
        if "portal" not in ids:
            pytest.skip("portal not installed on this deployment")
        home = http_client.get("/api/health").json().get("home")
        if home not in ("/portal/", "/hub/"):
            pytest.skip(f"deployment sets its own landing route: {home!r}")
        assert home == "/portal/", (
            "portal is loaded, so / should land on it — unless this deployment "
            "pins [os] home = \"/hub/\" (or a stored os.home), which reads the same here"
        )


class TestPortalHomeBoard:
    def test_board_renders_the_hub_lanes(self, app_page, page_errors):
        page = app_page("portal")
        # The greeting fills from /hub/api/digest — the stable marker that the
        # board came up (same marker test_sys_hub uses for the hub).
        page.wait_for_function(
            "(() => { var g = document.getElementById('portal-greeting');"
            " return !!g && g.textContent.trim().length > 0; })()",
            timeout=30000,   # the digest is cold for the first calls after a daemon restart
        )
        page.locator("#portal-board").wait_for(state="visible", timeout=5000)
        # The Next move card paints with its reasoning + a routable action.
        page.wait_for_selector("#portal-next .hub-next-card", state="attached", timeout=10000)
        assert "Suggested because:" in page.locator("#portal-next .hub-next-why").inner_text()
        href = page.locator("#portal-next a.hub-next-go").first.get_attribute("href") or ""
        assert href.startswith("/"), f"Next move action not routable: {href!r}"
        # The greeting is the eyebrow, not a second copy inside the card.
        assert page.locator("#portal-board .hub-greeting:visible").count() == 0
        assert_no_js_errors(page_errors, allow_patterns=_HOME_UI_ALLOW)

    def test_explore_panels_render_through_the_shared_renderers(self, app_page, page_errors, http_client):
        # Skip ONLY when the aggregator really has nothing for Explore; when it
        # does, a board that drops the section is a failure, not an absence.
        # The oracle is deliberately coarse (cognitive band, not a lane source,
        # not a quick-add form) — enough to know Explore must have a head.
        blocks = http_client.get("/hub/api/panels", timeout=60).json().get("blocks") or []
        explore_bound = [
            b for b in blocks
            if (b.get("priority") or 100) < 150
            and b.get("renderer") not in ("hero-weather", "quick-add", "outcome-box")
            and b.get("id") != "hub-welcome"
            and ((b.get("items") or [{}])[0] or {}).get("source") not in ("task", "calendar")
        ]
        if not explore_bound:
            pytest.skip("the aggregator has no cognitive-band panel for Explore here")
        page = app_page("portal")
        page.wait_for_selector("#portal-explore .hub-explore-head", state="attached", timeout=30000)
        assert page.locator("#portal-explore .hub-explore-head").count() == 1
        # No renderer fell through to the "Unknown renderer" / "Render error" card.
        assert page.locator("#portal-explore .panel.err").count() == 0
        assert_no_js_errors(page_errors, allow_patterns=_HOME_UI_ALLOW)

    def test_composer_does_not_jump_when_the_board_arrives(self, page, base_url):
        # The hero is top-aligned before any lane answers. Re-aligning when the
        # board appeared moved the composer ~120px under a user aiming at it.
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(base_url + "/portal/", wait_until="domcontentloaded", timeout=15000)
        # Measured inside the hero: chrome eos.js injects above the shell later
        # (the nav, an "AI is offline" banner) moves the whole pane, not the
        # composer within it, and is not what this pins.
        offset = ("(() => document.getElementById('hero-input').getBoundingClientRect().top"
                  " - document.getElementById('portal-hero').getBoundingClientRect().top)()")
        first = page.evaluate(offset)
        page.locator("#portal-board").wait_for(state="visible", timeout=20000)
        page.wait_for_timeout(300)
        after = page.evaluate(offset)
        # The greeting eyebrow filling in may nudge it; a re-centring would not fit.
        assert abs(after - first) < 40, (first, after)

    def test_quick_add_refresh_reaches_the_board(self, app_page):
        # A hub quick-add form refreshes its host via window.refreshAll after a
        # save (eos-hub-renderers.js _refreshAfterAdd); on portal that must be
        # the board's loader, or an expense logged from the landing page leaves
        # the dashboard tiles stale.
        page = app_page("portal")
        page.locator("#portal-board").wait_for(state="visible", timeout=20000)
        seen = []
        page.on("request", lambda r: seen.append(r.url) if "/hub/api/panels" in r.url else None)
        page.evaluate("window.refreshAll()")
        page.wait_for_timeout(1500)
        assert seen, "window.refreshAll did not re-fetch the board's panels"

    def test_nav_marks_portal_as_home_when_it_is_the_landing(self, app_page, http_client):
        # eos.js reads /api/health `home` and marks that app's Home link current.
        if http_client.get("/api/health").json().get("home") != "/portal/":
            pytest.skip("portal is not the landing route on this deployment")
        page = app_page("portal")
        page.wait_for_function(
            "(() => { var a = document.querySelector('a.nav-home');"
            " return !!a && a.classList.contains('current'); })()",
            timeout=8000,
        )
        # ...and ONLY portal: the hub is an ordinary app now, so it is not Home
        # and it gets a breadcrumb naming it (a page must always say where it is).
        page = app_page("hub")
        page.wait_for_function(
            "(() => !!document.querySelector('a.nav-home') && !!document.querySelector('.nav-crumb'))()",
            timeout=8000,
        )
        assert "current" not in (page.locator("a.nav-home").get_attribute("class") or "")

    def test_quick_entry_mode_never_loads_the_board(self, page, base_url):
        # Count the board's requests rather than inspecting what it painted: a
        # board that loaded but stayed hidden (the CSS hides it in quick mode
        # either way) or answered slowly would pass a DOM check. The fetches
        # start synchronously in PortalHome.init, so by load they have been sent.
        hub_calls = []
        board_api = ("/hub/api/", "/app-analytics/api/")   # every source the board reads
        page.on("request", lambda r: hub_calls.append(r.url) if any(a in r.url for a in board_api) else None)
        page.goto(base_url + "/portal/?quick=1", wait_until="load", timeout=15000)
        page.wait_for_timeout(500)
        assert hub_calls == [], f"quick-entry mode fetched the home board: {hub_calls}"
        board = page.locator("#portal-board")
        assert board.count() == 1
        assert not board.is_visible()
        assert page.evaluate(
            "getComputedStyle(document.querySelector('.portal-greeting-row')).display"
        ) == "none"


# ── Opening an app from the hero composer (portal-launch.js, 2026-10-03) ─────
# The hub's search bar opened apps on Enter; portal's composer starts chats.
# One exact app name + Enter opens the app; a partial name only suggests.

def _app_ids(app_list):
    return {a.get("id") if isinstance(a, dict) else a for a in (app_list or [])}


class TestPortalLaunch:
    # 20s hint waits: the hint needs /api/apps, which on a full install queues
    # behind the home board's slower requests on the same host during page load.
    def _type(self, page, text):
        box = page.locator("#hero-input")
        box.wait_for(state="visible", timeout=8000)
        # The hint needs the app catalog; without it every "no hint" assertion
        # would pass for the wrong reason.
        page.wait_for_function("Object.keys(APP_CATALOG).length > 0", timeout=20000)
        box.fill(text)
        return box

    # Conversation-creating POSTs: recorded and refused, so a test that sends
    # can prove what it sent without creating a thread on the daemon.
    _STARTS = ("/rooms/api/agents", "/agent/api/sessions", "/assistant/api/sessions")

    def _guard_sends(self, page):
        sent = []
        def handler(route):
            req = route.request
            if req.method == "POST" and any(p in req.url for p in self._STARTS):
                sent.append(req.url)
                route.abort()
            else:
                route.continue_()
        page.route("**/api/**", handler)
        return sent

    def test_exact_app_name_opens_it_on_enter(self, app_page, app_list, page_errors):
        if "task" not in _app_ids(app_list):
            pytest.skip("task app not installed")
        page = app_page("portal")
        box = self._type(page, "task")
        hint = page.locator("#hero-launch .portal-launch-btn.primary")
        hint.wait_for(state="visible", timeout=20000)
        assert "opens" in hint.inner_text()
        assert "Send to chat it instead" in page.locator("#hero-launch").inner_text()
        sent = self._guard_sends(page)
        box.press("Enter")
        page.locator("#portal-iframe-pane.open").wait_for(state="visible", timeout=8000)
        assert "/task/" in (page.locator("#portal-iframe-frame").get_attribute("src") or "")
        # It opened an app and did NOT also start a conversation. A chat start
        # is async (it lands ~seconds later and moves the hash), so wait for it.
        page.wait_for_timeout(3000)
        assert sent == [], f"Enter on an app name also started a conversation: {sent}"
        assert page.evaluate("location.hash") == "#app:task"
        assert page.locator("#hero-input").input_value() == ""
        assert_no_js_errors(page_errors, allow_patterns=_HOME_UI_ALLOW)

    def test_text_typed_before_the_app_list_loads_still_gets_its_hint(self, page, base_url, app_list):
        # A fast typist beats /api/apps: the hint must appear once the list
        # arrives, not only on the next keystroke. The catalog response is held
        # back so the order is forced rather than left to chance.
        if "task" not in _app_ids(app_list):
            pytest.skip("task app not installed")
        held = []
        page.route("**/api/apps", lambda route: held.append(route))   # parked, not answered
        # The ranking fetch also redraws the hint; refuse it so only the
        # catalog path can make the hint appear.
        page.route("**/app-analytics/api/ranking**", lambda route: route.abort())
        page.goto(base_url + "/portal/", wait_until="domcontentloaded", timeout=15000)
        page.locator("#hero-input").fill("task")
        page.wait_for_timeout(300)
        assert page.evaluate("Object.keys(APP_CATALOG).length") == 0, "the catalog arrived early — the race was not forced"
        assert page.locator("#hero-launch").inner_html().strip() == ""
        assert held, "the /api/apps request was never intercepted"
        for route in held:          # release first: unroute() would settle them itself
            route.continue_()
        page.unroute("**/api/apps")
        page.locator("#hero-launch .portal-launch-btn.primary").wait_for(state="visible", timeout=10000)

    def test_a_partial_name_suggests_and_a_click_opens(self, app_page, app_list):
        if "journal" not in _app_ids(app_list):
            pytest.skip("journal app not installed")
        page = app_page("portal")
        self._type(page, "journ")
        btn = page.locator('#hero-launch .portal-launch-btn[data-app="journal"]')
        btn.wait_for(state="visible", timeout=20000)
        # A prefix must never be the Enter target — no primary "opens" hint.
        assert page.locator("#hero-launch .portal-launch-btn.primary").count() == 0
        assert "still sends a chat" in page.locator("#hero-launch").inner_text()
        btn.click()
        page.locator("#portal-iframe-pane.open").wait_for(state="visible", timeout=8000)
        assert page.evaluate("location.hash") == "#app:journal"

    def test_a_message_shaped_text_shows_no_hint(self, app_page, app_list):
        if "task" not in _app_ids(app_list):
            pytest.skip("task app not installed")
        page = app_page("portal")
        for text in ("task?", "task list please"):
            self._type(page, text)
            page.wait_for_timeout(150)
            assert page.locator("#hero-launch").inner_html().strip() == "", text

    def test_send_button_sends_an_app_name_as_a_chat(self, app_page, app_list):
        # The visible escape from the Enter rule: clicking Send never opens an app.
        if "task" not in _app_ids(app_list):
            pytest.skip("task app not installed")
        page = app_page("portal")
        self._type(page, "task")
        page.locator("#hero-launch .portal-launch-btn.primary").wait_for(state="visible", timeout=20000)
        sent = self._guard_sends(page)
        page.locator("#hero-send").click()
        page.wait_for_timeout(1500)
        assert page.evaluate("location.hash") != "#app:task"
        assert sent, "Send did not try to start a conversation"

    def test_no_open_on_the_agent_backend_or_with_a_room_pending(self, app_page, app_list):
        if "task" not in _app_ids(app_list):
            pytest.skip("task app not installed")
        page = app_page("portal")
        self._type(page, "task")
        page.locator("#hero-launch .portal-launch-btn.primary").wait_for(state="visible", timeout=20000)
        # "run", "tests", "release" are instructions to a coding agent.
        page.evaluate("ACTIVE_BACKEND = 'agent'; PortalLaunch.render()")
        assert page.locator("#hero-launch").inner_html().strip() == ""
        assert page.evaluate("PortalLaunch.intercept()") is False
        # "New thread in <room>" is a stated intent to talk.
        page.evaluate("ACTIVE_BACKEND = 'rooms'; _pendingFolderId = 'probe'; PortalLaunch.render()")
        assert page.locator("#hero-launch").inner_html().strip() == ""
        assert page.evaluate("PortalLaunch.intercept()") is False
        page.evaluate("_pendingFolderId = null; PortalLaunch.render()")
        assert page.locator("#hero-launch .portal-launch-btn.primary").count() == 1

    def test_quick_entry_never_opens_an_app(self, page, base_url, app_list):
        # The hotkey window has no app pane to open into: Enter there is a chat.
        if "task" not in _app_ids(app_list):
            pytest.skip("task app not installed")
        page.goto(base_url + "/portal/?quick=1", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_function("Object.keys(APP_CATALOG).length > 0", timeout=20000)
        page.locator("#hero-input").fill("task")
        page.wait_for_timeout(200)
        assert page.evaluate("PortalLaunch.intercept()") is False
        assert page.evaluate("location.hash") == ""

    def test_only_the_think_verb_opens_apps(self, app_page, app_list):
        if "task" not in _app_ids(app_list):
            pytest.skip("task app not installed")
        page = app_page("portal")
        self._type(page, "task")
        page.locator("#hero-launch .portal-launch-btn.primary").wait_for(state="visible", timeout=20000)
        # Switching to Capture: "task" is now text to capture, not an app.
        page.locator('.portal-chip[data-verb="capture"]').click()
        page.wait_for_timeout(150)
        assert page.locator("#hero-launch").inner_html().strip() == ""
        page.locator('.portal-chip[data-verb="think"]').click()   # leave the default behind


# ── Apps embedded in portal's pane draw no second nav (2026-10-03) ───────────
# Portal opens an app at /<id>/?embed=1. eos.js skipped its auto-mounted nav
# there, but ~158 pages call EOS.nav('<id>') themselves and got a second bar,
# plus theme.css's 46px of body padding reserved for it.

class TestPortalEmbeddedApps:
    def _open_in_pane(self, page, base_url, app_id):
        page.goto(base_url + f"/portal/#app:{app_id}", wait_until="domcontentloaded", timeout=15000)
        page.locator("#portal-iframe-pane.open").wait_for(state="visible", timeout=15000)
        page.wait_for_function(
            "(() => { var f = document.getElementById('portal-iframe-frame');"
            " return !!(f && f.contentDocument && f.contentDocument.readyState === 'complete'"
            " && f.contentWindow.EOS && f.contentWindow.EOS._currentApp); })()",
            timeout=20000,
        )
        page.wait_for_timeout(500)
        return next(f for f in page.frames if "embed=1" in f.url)

    def test_an_app_in_the_pane_has_no_nav_and_no_gap(self, page, base_url, app_list):
        if "task" not in _app_ids(app_list):
            pytest.skip("task app not installed")
        # task calls EOS.nav('task') itself — the path the auto-mount guard missed.
        frame = self._open_in_pane(page, base_url, "task")
        assert frame.evaluate("document.querySelectorAll('body > nav.nav').length") == 0
        # Exactly the safe-area inset, which is 0 in a desktop iframe.
        pad = frame.evaluate("parseFloat(getComputedStyle(document.body).paddingTop)")
        assert pad == 0, f"embedded page still reserves {pad}px for a nav it does not have"
        # The pane title is the app's display name once the catalog arrives.
        name = next(a.get("name") for a in app_list if isinstance(a, dict) and a.get("id") == "task")
        page.wait_for_function(
            "(n) => document.getElementById('portal-iframe-name').textContent === n", arg=name, timeout=15000)

    def test_a_full_height_app_fills_the_pane(self, page, base_url, app_list):
        # agent sizes its panes as 100dvh minus the nav height. Embedded there
        # is no nav, so a literal 46px left a dead band at the bottom; the panes
        # must reach the bottom of the frame.
        if "agent" not in _app_ids(app_list):
            pytest.skip("agent app not installed")
        frame = self._open_in_pane(page, base_url, "agent")
        frame.locator("#main").wait_for(state="attached", timeout=15000)
        gap = frame.evaluate(
            "window.innerHeight - document.getElementById('main').getBoundingClientRect().bottom")
        assert abs(gap) <= 1, f"agent's main pane stops {gap}px short of the frame bottom"

    def test_the_same_app_opened_directly_keeps_its_nav(self, page, base_url, app_list):
        if "task" not in _app_ids(app_list):
            pytest.skip("task app not installed")
        page.goto(base_url + "/task/", wait_until="domcontentloaded", timeout=15000)
        page.locator("body > nav.nav").wait_for(state="attached", timeout=15000)
        assert page.evaluate("parseFloat(getComputedStyle(document.body).paddingTop)") >= 40



# ── The sidebar's one Recent list (portal-recent.js, 2026-10-03) ─────────────
# Conversations from every backend in one list, newest activity first, grouped
# by day, with a kind glyph and a time per row; empty sessions hidden.

class TestPortalRecentSidebar:
    def _threads(self, http_client):
        rooms = http_client.get("/rooms/api/agents?status=active").json() or []
        asst = http_client.get("/assistant/api/sessions").json() or []
        agent = http_client.get("/agent/api/sessions").json() or []
        agent = agent if isinstance(agent, list) else agent.get("sessions", [])
        return rooms, asst, agent

    def test_rows_are_grouped_by_day_with_a_glyph_and_time(self, app_page, http_client):
        rooms, asst, agent = self._threads(http_client)
        if not (rooms or [s for s in asst + agent if s.get("message_count")]):
            pytest.skip("no conversations on this deployment")
        page = app_page("portal")
        page.locator("#portal-rooms .portal-group-head").first.wait_for(state="visible", timeout=15000)
        heads = page.locator("#portal-rooms .portal-group-head").all_inner_texts()
        order = ["TODAY", "YESTERDAY", "LAST 7 DAYS", "LAST 30 DAYS", "OLDER"]
        idx = [order.index(h.strip().upper()) for h in heads if h.strip().upper() in order]
        assert idx and idx == sorted(idx), f"day groups out of order: {heads}"
        ts = page.evaluate(
            "[...document.querySelectorAll('#portal-rooms .portal-room[data-ts]')].map(a => +a.dataset.ts)")
        assert ts and ts == sorted(ts, reverse=True), "rows are not newest-activity first"
        # ...and "activity" means what the APIs report, not something the page
        # derived: the top row is the conversation whose last_active /
        # last_message (else created) is newest, computed here independently.
        from datetime import datetime
        folders = http_client.get("/portal/api/folders").json().get("folders") or []
        chat_first = http_client.get("/portal/api/config").json().get("chat_first")
        if not folders and not chat_first:
            def act(*vals):
                for v in vals:
                    if v:
                        return datetime.fromisoformat(v).timestamp()
                return 0
            cands = [(act(r.get("last_active"), r.get("created")), r["id"]) for r in rooms]
            cands += [(act(x.get("last_message"), x.get("created")), "asst:" + x["id"])
                      for x in asst if x.get("message_count") != 0]
            cands += [(act(x.get("last_message"), x.get("created")), "agent:" + x["id"])
                      for x in agent if x.get("message_count") != 0]
            newest = max(cands)[1]
            top = page.evaluate(
                "decodeURIComponent(document.querySelector('#portal-rooms .portal-room').getAttribute('href').slice(1))")
            assert top == newest, f"top row {top!r}, newest by the APIs {newest!r}"
        row = page.locator("#portal-rooms .portal-room").first
        assert row.locator(".r-ico").count() == 1
        assert row.locator(".r-time").inner_text().strip()
        # No per-backend section headings any more.
        side = page.locator("#portal-rooms").inner_text()
        assert "Assistant chats" not in side and "Agent runs" not in side

    def test_empty_sessions_are_not_listed(self, app_page, http_client):
        _, asst, agent = self._threads(http_client)
        empty = [("asst:" + s["id"]) for s in asst if s.get("message_count") == 0] + \
                [("agent:" + s["id"]) for s in agent if s.get("message_count") == 0]
        if not empty:
            pytest.skip("no empty sessions to hide here")
        page = app_page("portal")
        page.locator("#portal-rooms .portal-room").first.wait_for(state="visible", timeout=15000)
        if page.locator(".portal-show-more").count():
            page.locator(".portal-show-more").click()
        hrefs = page.evaluate(
            "[...document.querySelectorAll('#portal-rooms .portal-room')].map(a => decodeURIComponent(a.getAttribute('href').slice(1)))")
        pinned = set(http_client.get("/portal/api/pins").json().get("threads", []))
        leaked = [t for t in empty if t in hrefs and t not in pinned]
        assert not leaked, f"empty sessions listed: {leaked[:5]}"

    def test_show_more_reveals_the_rest(self, app_page):
        page = app_page("portal")
        page.locator("#portal-rooms .portal-room").first.wait_for(state="visible", timeout=15000)
        more = page.locator(".portal-show-more")
        if more.count() == 0:
            pytest.skip("fewer conversations than the list limit")
        before = page.locator("#portal-rooms .portal-room").count()
        n = int("".join(ch for ch in more.inner_text() if ch.isdigit()))
        more.click()
        assert page.locator("#portal-rooms .portal-room").count() == before + n
        assert page.locator(".portal-show-more").count() == 0

    def test_search_lists_matching_chats_and_opens_one(self, app_page):
        # Conversations are found in the search DROPDOWN (a "Chats" section
        # above Apps), not by filtering the list underneath it — the dropdown
        # covered the filtered rows.
        page = app_page("portal")
        page.locator("#portal-rooms .portal-room").first.wait_for(state="visible", timeout=15000)
        row = page.locator("#portal-rooms .portal-room").first
        name = row.locator(".r-name").inner_text().strip()
        tid = page.evaluate("decodeURIComponent(document.querySelector('#portal-rooms .portal-room').getAttribute('href').slice(1))")
        rows_before = page.locator("#portal-rooms .portal-room").count()
        box = page.locator("#portal-search input")
        box.fill(name)
        res = page.locator("#portal-search .eos-search-results")
        res.locator(".eos-search-section", has_text="Chats").wait_for(state="visible", timeout=5000)
        # The chats item itself — by its name cell, not the "All vault
        # matches for <name>" fallback row, which also contains the text.
        item = res.locator(".eos-search-item-name").get_by_text(name, exact=True).first
        assert item.is_visible()
        # The list underneath is not filtered by typing.
        assert page.locator("#portal-rooms .portal-room").count() == rows_before
        item.click()
        page.wait_for_function("(t) => decodeURIComponent(location.hash.slice(1)) === t", arg=tid, timeout=8000)
        # Picking a chat closes the dropdown for good: the pending note search
        # must not re-open it over the now-empty box.
        page.wait_for_timeout(2500)
        assert not res.evaluate("e => e.classList.contains('open')"), "results re-opened after a pick"
        assert box.input_value() == ""

    def test_enter_picks_the_top_chat_and_the_dropdown_stays_shut(self, app_page, http_client):
        if http_client.get("/portal/api/folders").json().get("folders"):
            pytest.skip("a filed (older) thread could outrank the list's top row in search")
        page = app_page("portal")
        page.locator("#portal-rooms .portal-room").first.wait_for(state="visible", timeout=15000)
        name = page.locator("#portal-rooms .portal-room .r-name").first.inner_text().strip()
        tid = page.evaluate("decodeURIComponent(document.querySelector('#portal-rooms .portal-room').getAttribute('href').slice(1))")
        box = page.locator("#portal-search input")
        box.fill(name)
        res = page.locator("#portal-search .eos-search-results")
        res.locator(".eos-search-section", has_text="Chats").wait_for(state="visible", timeout=5000)
        first = res.locator(".eos-search-item").first.locator(".eos-search-item-name").inner_text().strip()
        if first != name:
            pytest.skip("another conversation with a matching name is newer")
        box.press("Enter")
        page.wait_for_function("(t) => decodeURIComponent(location.hash.slice(1)) === t", arg=tid, timeout=8000)
        page.wait_for_timeout(2500)
        assert not res.evaluate("e => e.classList.contains('open')"), "results re-opened after Enter"

    def test_home_continue_strip_agrees_with_the_list(self, app_page, http_client):
        # One activity model for both: the strip's first card is the list's
        # newest conversation (with no room folders, nothing is filed away).
        if http_client.get("/portal/api/folders").json().get("folders"):
            pytest.skip("threads filed in rooms are left out of the list")
        if http_client.get("/portal/api/config").json().get("chat_first"):
            pytest.skip("chat-first lists its chat sessions in its own section, not this list")
        page = app_page("portal")
        page.locator("#portal-rooms .portal-room").first.wait_for(state="visible", timeout=15000)
        card = page.locator("#portal-continue .portal-cont-card").first
        card.wait_for(state="attached", timeout=10000)
        pinned = set(http_client.get("/portal/api/pins").json().get("threads", []))
        if pinned:
            pytest.skip("pinned threads are listed separately")
        assert card.get_attribute("href") == page.locator("#portal-rooms .portal-room").first.get_attribute("href")

    def test_new_room_is_reachable_without_any_room(self, app_page, http_client):
        if http_client.get("/portal/api/folders").json().get("folders"):
            pytest.skip("rooms exist here — this pins the button when there are none")
        page = app_page("portal")
        btn = page.locator(".portal-new-room")
        btn.wait_for(state="visible", timeout=8000)
        assert btn.get_attribute("title")
        btn.click()
        # The create form opens (cancelled here: no room is made).
        modal = page.locator("#eos-modal-overlay .eos-modal")
        modal.wait_for(state="visible", timeout=5000)
        assert "New room" in modal.inner_text()
        page.keyboard.press("Escape")

    def test_mobile_drawer_opens_below_the_nav(self, page, base_url):
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(base_url + "/portal/", wait_until="domcontentloaded", timeout=15000)
        page.locator("body > nav.nav").wait_for(state="attached", timeout=15000)
        page.evaluate("toggleSidebar()")
        page.wait_for_timeout(400)
        nav_bottom = page.evaluate("document.querySelector('body > nav.nav').getBoundingClientRect().bottom")
        head_top = page.evaluate("document.querySelector('.eos-chat-sidebar-header').getBoundingClientRect().top")
        drawer_top = page.evaluate("document.querySelector('.eos-chat-sidebar').getBoundingClientRect().top")
        assert head_top >= nav_bottom - 1, f"drawer header at {head_top}px, under the nav ending at {nav_bottom}px"
        assert abs(drawer_top - nav_bottom) <= 1, f"drawer starts at {drawer_top}px, nav ends at {nav_bottom}px"


# ── --eos-nav-h must equal the nav's real height (theme.css) ─────────────────
# The token was a designed 46px while the nav rendered 52px (56px on phones)
# once the account button joined it, so every page kept 6-10px under the nav.

_NAV_VS_TOKEN = """() => {
  var nav = document.querySelector('body > nav.nav');
  if (!nav) return null;
  return {nav: nav.getBoundingClientRect().height,
          token: parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--eos-nav-h')),
          pad: parseFloat(getComputedStyle(document.body).paddingTop)};
}"""


def test_nav_height_token_follows_a_theme_switch(page, base_url, app_list):
    # A theme switch changes only the nav's border (nord: +1px). The token must
    # follow it without a reload — a content-box observer never sees it.
    if "task" not in _app_ids(app_list):
        pytest.skip("task app not installed")
    page.goto(base_url + "/task/", wait_until="domcontentloaded", timeout=15000)
    page.locator("body > nav.nav .nav-account").wait_for(state="attached", timeout=15000)
    page.evaluate("try { localStorage.setItem('eos-theme', 'eos') } catch (e) {}")
    page.evaluate("EOS.setTheme('eos')")
    page.wait_for_timeout(300)
    page.evaluate("EOS.setTheme('nord')")
    try:
        page.wait_for_function(
            "() => { var n = document.querySelector('body > nav.nav');"
            " return Math.abs(n.getBoundingClientRect().height - parseFloat(getComputedStyle("
            "document.documentElement).getPropertyValue('--eos-nav-h'))) <= 0.6; }", timeout=3000)
    finally:
        page.evaluate("EOS.setTheme('eos')")
    m = page.evaluate(_NAV_VS_TOKEN)
    assert abs(m["nav"] - m["token"]) <= 0.6, m


@pytest.mark.parametrize("width,touch,theme", [
    (390, True, "eos"), (1280, False, "eos"), (1280, True, "eos"),
    # nord's 1px border makes its nav 53px — no CSS estimate covers that; only
    # the live measurement in eos.js does, so this case is what pins it.
    (1280, False, "nord"),
])
@pytest.mark.parametrize("app_id", ["task", "journal", "portal"])
def test_nav_height_token_matches_the_rendered_nav(browser, base_url, app_list, app_id, width, touch, theme):
    # eos.js keeps --eos-nav-h equal to the nav's rendered height, which moves
    # with theme borders, touch targets (60px), the viewport and the account
    # button arriving. Body padding must equal it: more leaves a gap, less
    # puts content under the nav.
    if app_id not in _app_ids(app_list):
        pytest.skip(f"{app_id} not installed")
    from conftest import _AUTH_HEADERS
    ctx = browser.new_context(viewport={"width": width, "height": 844}, has_touch=touch,
                              extra_http_headers=_AUTH_HEADERS)
    ctx.add_init_script(f"try{{localStorage.setItem('eos-theme','{theme}')}}catch(e){{}}")
    try:
        page = ctx.new_page()
        page.goto(base_url + f"/{app_id}/", wait_until="domcontentloaded", timeout=15000)
        page.locator("body > nav.nav").wait_for(state="attached", timeout=15000)
        # The account button lands when /api/health answers and grows the nav;
        # wait for the token to have followed it rather than for a fixed time.
        page.wait_for_function(
            "() => { var n = document.querySelector('body > nav.nav'); if (!n || !n.querySelector('.nav-account')) return false;"
            " return Math.abs(n.getBoundingClientRect().height - parseFloat(getComputedStyle(document.documentElement)"
            ".getPropertyValue('--eos-nav-h'))) <= 0.6; }", timeout=15000)
        m = page.evaluate(_NAV_VS_TOKEN)
        # eos.js rounds the height, so half a pixel is the honest tolerance —
        # a whole pixel would pass nord's 53px nav against a 52px CSS guess.
        assert abs(m["nav"] - m["token"]) <= 0.6, f"{app_id}@{width}: nav {m['nav']}px, token {m['token']}px"
        assert abs(m["pad"] - m["nav"]) <= 0.6, f"{app_id}@{width}: body padding {m['pad']}px vs a {m['nav']}px nav"
    finally:
        ctx.close()
