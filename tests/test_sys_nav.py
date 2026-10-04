"""System tests: persistent nav shell (breadcrumb, global search overlay, assistant entry).

Covers the always-mounted ``.nav`` chrome (eos.js ``_renderNav`` / ``EOS.nav``)
enriched into a fuller shell — the location breadcrumb, the ``/``-triggered
``EOS_UI.searchBar`` overlay, and the assistant button. See
``.claude/rules/shared-frontend.md`` and ``emptyos/web/static/eos.js``.

Requires the daemon on localhost:9000. The ``icon`` API assertions need the
``server.py`` change live (restart / sandbox restart); the rest are static
JS/CSS that serve from disk on refresh.
"""

import pytest

# Default quick-links rendered in the nav — an app in this set is named by its
# own highlighted link, so it's a poor breadcrumb test subject.
DEFAULT_NAV = {"task", "journal", "projects", "focus", "search", "hub"}
# Apps that intentionally suppress the nav (full-screen islands / chat shells).
NAV_SUPPRESSED = {"voice-assistant", "assistant", "agent", "meditation"}


def _pick_deep_app(app_list):
    """First installed, loaded app that is NOT a default-nav quick link, so the
    breadcrumb is the only thing naming the current location."""
    for a in app_list:
        if not isinstance(a, dict):
            continue
        if a.get("id") in DEFAULT_NAV or a.get("id") in NAV_SUPPRESSED:
            continue
        if a.get("state") != "loaded" or not a.get("web_prefix"):
            continue
        return a
    return None


@pytest.mark.api
class TestNavApi:
    def test_apps_expose_icon_fields(self, http_client):
        """/api/apps surfaces adaptive ids plus the legacy emoji fallback."""
        apps = http_client.get("/api/apps").json()
        assert isinstance(apps, list) and apps
        for a in apps:
            assert "icon" in a, f"/api/apps missing 'icon' for {a.get('id')}"
            assert "icon_id" in a, f"/api/apps missing 'icon_id' for {a.get('id')}"

    def test_default_nav_apps_have_seeded_icons(self, http_client):
        apps = {a["id"]: a for a in http_client.get("/api/apps").json() if a.get("id")}
        seeded = [i for i in DEFAULT_NAV if apps.get(i, {}).get("icon")]
        assert seeded, "no default-nav app exposes a seeded icon (daemon restart needed?)"

    def test_first_family_ids_propagate(self, http_client):
        apps = {a["id"]: a for a in http_client.get("/api/apps").json() if a.get("id")}
        for app_id in ("hub", "settings", "task", "search", "kb", "worklog"):
            if app_id in apps:
                assert apps[app_id]["icon_id"] == app_id


@pytest.mark.interactive
class TestNavShell:
    def test_nav_mounted_on_app_page(self, app_page):
        page = app_page("task")
        assert page.locator("body > nav.nav").count() == 1

    def test_home_crumb_links_to_hub(self, app_page):
        page = app_page("task")
        home = page.locator("nav.nav a.nav-home")
        assert home.count() == 1
        assert home.get_attribute("href") == "/"

    def test_breadcrumb_names_current_app(self, app_page, app_list):
        target = _pick_deep_app(app_list)
        if not target:
            pytest.skip("no non-default loaded app available to test breadcrumb")
        page = app_page(target["id"])
        page.locator(".nav-crumb-name").wait_for(state="visible", timeout=6000)
        # Crumb upgrades from the id fallback to the resolved app name on the
        # async /api/apps pass — wait for the real name.
        page.wait_for_function(
            "(name) => { var el = document.querySelector('.nav-crumb-name');"
            " return !!el && el.textContent.trim() === name; }",
            arg=target["name"],
            timeout=6000,
        )
        assert page.locator(".nav-crumb-name").inner_text().strip() == target["name"]

    def test_breadcrumb_uses_registered_svg_icon(self, app_page):
        page = app_page("settings")
        page.locator(".nav-crumb-icon .eos-app-icon").wait_for(state="visible", timeout=6000)
        assert page.locator(".nav-crumb-icon use").count() == 4
        box = page.locator(".nav-crumb-icon").bounding_box()
        assert box and round(box["width"]) == 16 and round(box["height"]) == 16

    def test_drawer_uses_icons_without_replacing_labels(self, app_page):
        page = app_page("task")
        page.locator(".nav-more").click()
        page.locator("#app-drawer.open").wait_for(state="visible", timeout=4000)
        page.locator(".app-drawer-item .adi-icon").first.wait_for(state="visible", timeout=6000)
        assert page.locator(".app-drawer-item .adi-name").count() > 0
        assert page.locator(".app-drawer-item .eos-app-icon").count() > 0
        box = page.locator(".app-drawer-item .adi-icon").first.bounding_box()
        assert box and round(box["width"]) == 32 and round(box["height"]) == 32

    def test_unregistered_icon_uses_legacy_fallback_without_svg(self, app_page):
        page = app_page("task")
        result = page.evaluate(
            """() => {
                var host = document.createElement('div');
                host.innerHTML = EOS.appIcon('../not-registered', '🧪');
                return {svg: host.querySelectorAll('svg').length, text: host.textContent};
            }"""
        )
        assert result == {"svg": 0, "text": "🧪"}

    def test_iconless_app_gets_a_letter_monogram_not_a_bare_box(self, app_page):
        """An app with no icon and no emoji draws its initial, not a lone box.

        187 of 230 apps have no icon yet, so this is the common case rather than
        the edge one; in the drawer's dense two-column list a bare box reads as
        an unchecked checkbox, which is what the monogram replaces.
        """
        page = app_page("task")
        result = page.evaluate(
            """() => {
                var host = document.createElement('div');
                host.innerHTML = EOS.appIcon('../not-registered', '', 'Command Runner');
                var t = host.querySelector('.eos-app-icon-monogram-text');
                return {
                    svg: host.querySelectorAll('svg.eos-app-icon-monogram').length,
                    letter: t ? t.textContent : null,
                    bareBox: host.textContent.indexOf('▢') >= 0,
                };
            }"""
        )
        assert result == {"svg": 1, "letter": "C", "bareBox": False}

    def test_icon_fallback_order_is_real_icon_then_emoji_then_monogram(self, app_page):
        """A monogram must never shadow a registered icon or a manifest emoji."""
        page = app_page("task")
        result = page.evaluate(
            """(emoji) => {
                var probe = function(id, e, label) {
                    var host = document.createElement('div');
                    host.innerHTML = EOS.appIcon(id, e, label);
                    return {
                        real: host.querySelectorAll('use').length > 0,
                        mono: host.querySelectorAll('.eos-app-icon-monogram').length > 0,
                        text: host.textContent.trim(),
                    };
                };
                return {
                    registered: probe('task', '', 'Task Manager'),
                    emoji: probe('../nope', emoji, 'Test App'),
                    neither: probe('../nope', '', 'Zebra'),
                };
            }""",
            '🧪',
        )
        assert result["registered"]["real"] and not result["registered"]["mono"]
        assert result["emoji"]["text"] == '🧪' and not result["emoji"]["mono"]
        assert result["neither"]["mono"] and result["neither"]["text"] == "Z"

    def test_slash_opens_search_overlay_and_focuses(self, app_page):
        page = app_page("task")
        # The `/` handler ignores keystrokes while an input is focused — blur
        # whatever the app may have autofocused first.
        page.evaluate("() => { var a = document.activeElement; if (a && a.blur) a.blur(); }")
        page.keyboard.press("/")
        page.wait_for_selector("#eos-search-overlay.open", timeout=4000)
        page.wait_for_selector("#eos-search-overlay .eos-search-input", timeout=4000)
        focused = page.evaluate(
            "() => { var a = document.activeElement;"
            " return !!(a && a.classList && a.classList.contains('eos-search-input')); }"
        )
        assert focused, "search input not focused after pressing '/'"
        page.keyboard.press("Escape")
        page.wait_for_function(
            "() => !document.querySelector('#eos-search-overlay.open')", timeout=2000
        )
        assert page.locator("#eos-search-overlay.open").count() == 0

    def test_search_button_opens_overlay(self, app_page):
        page = app_page("task")
        assert page.locator("nav.nav .nav-search-btn").count() == 1
        page.locator("nav.nav .nav-search-btn").click()
        page.wait_for_selector("#eos-search-overlay.open", timeout=4000)
        assert page.locator("#eos-search-overlay.open").count() == 1

    def test_assistant_button_matches_presence(self, app_page, app_list):
        ids = {a.get("id") for a in app_list if isinstance(a, dict)}
        expected = ("voice-assistant" in ids) or ("assistant" in ids)
        page = app_page("task")
        # The button appears on the async re-render once app presence is known.
        page.wait_for_timeout(1500)
        present = page.locator("nav.nav .nav-assistant").count() > 0
        assert present == expected, (
            f"assistant button present={present} but expected={expected} "
            f"(voice-assistant/assistant installed)"
        )
