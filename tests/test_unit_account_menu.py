"""The nav's account menu: who sees it, what it offers, and how Sign out works.

Every owner gets the menu (local mode too: name, Settings, Usage). The two
session links appear only where they mean something: the daemon's own
POST /logout when the browser holds a daemon session, or a proxy's endpoints
when one is configured (the EnglishOS control plane owns its learners'
sessions). The browser tests run the shipped eos.js against a stub server.
"""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from emptyos.web.routes_auth import register_auth
from emptyos.web.server import _account_links, _same_site_path
from helpers import requires_browser

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "emptyos" / "web" / "static"


def _getter(values):
    return lambda key, default=None: values.get(key, default)


# --- which links the server offers ------------------------------------------

def test_local_mode_offers_no_session_links():
    assert _account_links(_getter({}), False) == {"manage_url": None, "sign_out_url": None}


def test_a_daemon_browser_session_signs_out_at_the_daemon():
    assert _account_links(_getter({}), True)["sign_out_url"] == "/logout"


def test_a_configured_proxy_owns_both_links():
    cfg = _getter({"network.account_url": "/portal", "network.sign_out_url": "/auth/logout"})
    # Even with a daemon cookie present, the proxy's endpoint wins: ending
    # the daemon's cookie would leave the real (proxy) session signed in.
    assert _account_links(cfg, True) == {"manage_url": "/portal", "sign_out_url": "/auth/logout"}


@pytest.mark.parametrize("hostile", [
    "//evil.example/", "/\\evil.example", "https://evil.example/", "javascript:alert(1)",
    "evil", "/a\nb", "/%2F%2Fevil", 7, None,
])
def test_a_configured_link_that_leaves_the_site_is_dropped(hostile):
    assert _same_site_path(hostile) is None
    cfg = _getter({"network.account_url": hostile, "network.sign_out_url": hostile})
    assert _account_links(cfg, False) == {"manage_url": None, "sign_out_url": None}


# --- the daemon's POST /logout -------------------------------------------------

def _auth_client():
    config = SimpleNamespace(auth_token="t" * 32, login_password="pw",
                             get=lambda key, default=None: default)
    kernel = SimpleNamespace(config=config, apps=SimpleNamespace(manifests={}),
                             services=SimpleNamespace(get_optional=lambda name: None))
    server = FastAPI()
    register_auth(server, kernel)
    return TestClient(server, follow_redirects=False)


def test_post_logout_ends_the_session_and_goes_to_login():
    client = _auth_client()
    client.cookies.set("eos_session", "pw")
    res = client.post("/logout")
    assert res.status_code == 303 and res.headers["location"] == "/login"
    assert "eos_session=" in res.headers["set-cookie"] and "Max-Age=0" in res.headers["set-cookie"]


def test_a_get_cannot_sign_the_owner_out():
    # SameSite=Lax still sends the cookie on a top-level cross-site GET, so a
    # GET /logout would let any site sign the owner out with a plain link.
    client = _auth_client()
    client.cookies.set("eos_session", "pw")
    res = client.get("/logout")
    assert res.status_code == 405
    assert "set-cookie" not in res.headers


# --- the menu itself, in a real browser ---------------------------------------

PAGE = """<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="stylesheet" href="/static/theme.css"></head>
<body><main id="content" style="display:block;padding:120px 20px 400px">page
<a id="later" href="#later">a link in the page</a></main>
<script src="/static/eos.js"></script>
<script>EOS.nav('notes');</script></body></html>"""


@pytest.fixture
def stub(request):
    engine = getattr(request, "param", "chromium")
    uvicorn = pytest.importorskip("uvicorn")
    sync_api = pytest.importorskip("playwright.sync_api")
    state = {"health": {"status": "ok"}, "apps": [], "name": "", "sign_outs": 0,
             "logout_status": 200, "app_names": {}}
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/api/health")
    async def health():
        return state["health"]

    @app.get("/api/apps")
    async def apps():
        return [{"id": a, "name": state["app_names"].get(a, a)} for a in state["apps"]]

    @app.get("/settings/api/get")
    async def setting(key: str):
        return {"value": state["name"] if key == "user.name" else None}

    @app.post("/logout")
    async def logout(request: Request):
        # As the daemon answers: 303 to the login page, which fetch follows.
        state["sign_outs"] += 1
        if state["logout_status"] != 200:
            return JSONResponse({"error": "boom"}, status_code=state["logout_status"])
        return RedirectResponse("/login", status_code=303)

    @app.get("/login")
    async def login():
        return HTMLResponse("<h1>login</h1>")

    @app.get("/{path:path}")
    async def page(path: str):
        return HTMLResponse(PAGE if path.startswith("notes") else "<h1 id=home>home</h1>")

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.02)
    with sync_api.sync_playwright() as pw:
        try:
            browser = getattr(pw, engine).launch()
        except Exception as exc:  # that engine's binary is not installed
            server.should_exit = True
            pytest.skip(f"playwright {engine} unavailable: {exc}")
        page = browser.new_page()
        try:
            yield f"http://127.0.0.1:{port}", page, state
        finally:
            browser.close()
            server.should_exit = True
            thread.join(timeout=5)


def _items(page):
    return page.eval_on_selector_all(
        "#eos-account-menu > :not(.eos-account-sep)",
        "els => els.map(e => e.textContent.trim())")


@requires_browser()
def test_an_owner_with_a_session_gets_the_full_menu_and_can_sign_out(stub):
    origin, page, state = stub
    state.update(name="Kevin", apps=["notes", "billing"], health={
        "status": "ok", "account": {"manage_url": "/portal", "sign_out_url": "/logout"}})
    page.goto(origin + "/notes/")
    page.wait_for_selector(".nav-account:has-text('K')", timeout=10000)
    page.click(".nav-account")
    assert page.get_attribute(".nav-account", "aria-expanded") == "true"
    assert _items(page) == ["Kevin", "Settings", "Usage", "Manage account", "Sign out"]
    assert page.get_attribute("#eos-account-menu a:has-text('Manage account')", "href") == "/portal"
    page.click("#eos-account-menu .eos-account-signout")
    page.wait_for_selector("#home", timeout=10000)
    assert state["sign_outs"] == 1 and page.url == origin + "/"


@requires_browser()
def test_a_failed_sign_out_says_so_and_stays(stub):
    # Going to "/" anyway would show a still-signed-in page as if it worked.
    origin, page, state = stub
    state.update(logout_status=500, health={
        "status": "ok", "account": {"manage_url": None, "sign_out_url": "/logout"}})
    page.goto(origin + "/notes/")
    page.wait_for_selector(".nav-account", timeout=10000)
    page.click(".nav-account")
    page.click("#eos-account-menu .eos-account-signout")
    page.wait_for_selector(".eos-account-signout:has-text('failed')", timeout=10000)
    assert state["sign_outs"] == 1 and page.url == origin + "/notes/"


@requires_browser()
def test_local_mode_gets_the_menu_without_session_links(stub):
    origin, page, state = stub
    state.update(health={"status": "ok", "account": {"manage_url": None, "sign_out_url": None}})
    page.goto(origin + "/notes/")
    page.wait_for_selector(".nav-account", timeout=10000)
    page.click(".nav-account")
    assert _items(page) == ["Add your name", "Settings"]


@requires_browser()
def test_an_anonymous_visitor_gets_no_menu(stub):
    origin, page, state = stub
    page.goto(origin + "/notes/")
    page.wait_for_selector("nav.nav .nav-more", timeout=10000)
    page.wait_for_load_state("networkidle")
    assert page.query_selector(".nav-account") is None


@requires_browser()
def test_escape_and_an_outside_click_close_the_menu(stub):
    origin, page, state = stub
    state.update(name="Kevin", health={"status": "ok", "account": {"manage_url": None, "sign_out_url": None}})
    page.goto(origin + "/notes/")
    page.wait_for_selector(".nav-account:has-text('K')", timeout=10000)
    page.wait_for_load_state("networkidle")
    page.click(".nav-account")
    page.keyboard.press("Escape")
    assert page.query_selector("#eos-account-menu") is None
    assert page.evaluate("document.activeElement.className") == "nav-account"
    page.click(".nav-account")
    page.click("#content")
    assert page.query_selector("#eos-account-menu") is None
    # A press on the name row leaves focus on the page, so no focusout will
    # fire for the next click: closing it is the document click handler's.
    page.click(".nav-account")
    page.click("#eos-account-menu .eos-account-name")
    page.click("#content", position={"x": 5, "y": 300})
    assert page.query_selector("#eos-account-menu") is None


LOCAL = {"status": "ok", "account": {"manage_url": None, "sign_out_url": None}}


@requires_browser()
def test_a_hostile_name_is_text_not_markup(stub):
    origin, page, state = stub
    state.update(name='<img src=x onerror="window.pwned=1">"x', health=LOCAL)
    page.goto(origin + "/notes/")
    page.wait_for_selector(".nav-account:has-text('<')", timeout=10000)
    page.click(".nav-account")
    assert page.inner_text("#eos-account-menu .eos-account-name") == state["name"]
    assert page.query_selector("#eos-account-menu img, nav img") is None
    assert page.get_attribute(".nav-account", "title") == state["name"] + " — account"
    assert page.evaluate("window.pwned") is None


@requires_browser()
def test_an_emoji_name_gives_a_whole_glyph(stub):
    origin, page, state = stub
    state.update(name="\U0001F9D1 Sam", health=LOCAL)
    page.goto(origin + "/notes/")
    page.wait_for_selector(".nav-account:has-text('\U0001F9D1')", timeout=10000)
    assert page.inner_text(".nav-account") == "\U0001F9D1"


@requires_browser()
def test_an_open_menu_survives_a_nav_re_render(stub):
    # /api/apps, /api/health and the name each re-render the nav when they land.
    origin, page, state = stub
    state.update(health=LOCAL)
    page.goto(origin + "/notes/")
    page.wait_for_selector(".nav-account", timeout=10000)
    page.wait_for_load_state("networkidle")
    page.click(".nav-account")
    page.evaluate("EOS.nav('notes')")
    assert page.query_selector("#eos-account-menu") is not None
    assert page.get_attribute(".nav-account", "aria-expanded") == "true"
    # The keyboard user's place is kept, not dropped to <body>.
    assert page.evaluate("document.activeElement.textContent.trim()") == "Add your name"


@requires_browser()
def test_avatar_focus_survives_a_nav_re_render(stub):
    origin, page, state = stub
    state.update(name="Kevin", health=LOCAL)
    page.goto(origin + "/notes/")
    page.wait_for_selector(".nav-account:has-text('K')", timeout=10000)
    page.wait_for_load_state("networkidle")
    page.focus(".nav-account")
    page.evaluate("EOS.nav('notes')")
    assert page.evaluate("document.activeElement.className") == "nav-account"


@requires_browser()
def test_tab_order_runs_avatar_menu_and_out_closes_it(stub):
    # Chromium's Tab order. Safari leaves links out of Tab by default (Option+
    # Tab reaches them), as on any site, so this is not run under WebKit.
    origin, page, state = stub
    state.update(name="Kevin", apps=["notes", "billing"], health=LOCAL)
    page.goto(origin + "/notes/")
    page.wait_for_selector(".nav-account:has-text('K')", timeout=10000)
    page.wait_for_load_state("networkidle")
    page.click(".nav-account")
    focused = "document.activeElement.textContent.trim()"
    assert page.evaluate(focused) == "Settings"
    page.keyboard.press("Shift+Tab")
    assert page.evaluate("document.activeElement.className") == "nav-account"
    page.keyboard.press("Tab")
    page.keyboard.press("Tab")
    assert page.evaluate(focused) == "Usage"
    page.keyboard.press("Tab")  # past the last item, out of the menu
    assert page.query_selector("#eos-account-menu") is None


@requires_browser()
@pytest.mark.parametrize("stub", ["chromium", "webkit"], indirect=True)
def test_the_avatar_closes_its_own_menu(stub):
    # Safari does not focus a clicked button, so focus leaves the menu for
    # nowhere on the second press; closing on that made the click reopen it.
    origin, page, state = stub
    state.update(name="Kevin", health=LOCAL)
    page.goto(origin + "/notes/")
    page.wait_for_selector(".nav-account:has-text('K')", timeout=10000)
    page.wait_for_load_state("networkidle")
    page.click(".nav-account")
    assert page.query_selector("#eos-account-menu") is not None
    page.click(".nav-account")
    assert page.query_selector("#eos-account-menu") is None


@requires_browser()
def test_a_tap_on_the_name_row_keeps_the_menu_open_on_a_phone(stub):
    # On touch, focus moves after touchend, so any timing trick loses the race.
    origin, first_page, state = stub
    state.update(name="Kevin", health=LOCAL)
    ctx = first_page.context.browser.new_context(has_touch=True, is_mobile=True,
                                        viewport={"width": 375, "height": 700})
    page = ctx.new_page()
    try:
        page.goto(origin + "/notes/")
        page.wait_for_selector(".nav-account:has-text('K')", timeout=10000)
        page.wait_for_load_state("networkidle")
        page.tap(".nav-account")
        page.tap("#eos-account-menu .eos-account-name")
        assert page.query_selector("#eos-account-menu") is not None
    finally:
        ctx.close()


@requires_browser()
def test_a_press_on_the_name_row_keeps_the_menu_open(stub):
    origin, page, state = stub
    state.update(name="Kevin", health=LOCAL)
    page.goto(origin + "/notes/")
    page.wait_for_selector(".nav-account:has-text('K')", timeout=10000)
    page.wait_for_load_state("networkidle")
    page.click(".nav-account")
    page.click("#eos-account-menu .eos-account-name")
    assert page.query_selector("#eos-account-menu") is not None


@requires_browser()
def test_the_avatar_stays_on_screen_on_a_phone(stub):
    origin, page, state = stub
    page.set_viewport_size({"width": 375, "height": 700})
    # A phone nav keeps Home, the current app and the tools (quick links are
    # hidden). Worst case: a long app name in the crumb, plus the assistant.
    state.update(apps=["notes", "assistant"], health=LOCAL,
                 app_names={"notes": "A rather long application name"})
    page.goto(origin + "/notes/")
    page.wait_for_selector(".nav-account", timeout=10000)
    page.wait_for_load_state("networkidle")
    nav = page.evaluate("(() => { const n = document.querySelector('nav.nav');"
                        " return [n.scrollWidth, n.clientWidth]; })()")
    # Far past the nav's own padding, or a non-sticky avatar would still fit.
    assert nav[0] - nav[1] > 40, ("the nav must really overflow", nav)
    box = page.evaluate("(() => { const r = document.querySelector('.nav-account')"
                        ".getBoundingClientRect(); return [r.left, r.right, r.width, innerWidth]; })()")
    assert box[1] <= box[3] and box[0] >= 0, box
    assert box[2] >= 44, "phone tap target"
