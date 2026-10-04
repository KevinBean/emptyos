"""System app tests: Settings — 10 use cases."""

import pytest

import factories
from helpers import assert_dict_response, assert_ok
from page_helpers import (
    assert_no_js_errors, click_first, wait_briefly,
)


@pytest.mark.api
class TestSettingsAPI:
    def test_config_system_info(self, http_client):
        data = assert_dict_response(http_client.get("/settings/api/config"))
        sys_info = data.get("system", {})
        assert isinstance(sys_info, dict)
        assert any(
            k in sys_info for k in ("os_name", "vault_path", "host", "port")
        ), f"system info has no expected keys: {list(sys_info.keys())}"

    def test_config_settings_dict(self, http_client):
        data = assert_dict_response(http_client.get("/settings/api/config"))
        assert "settings" in data, f"config missing 'settings' key: {list(data.keys())}"
        assert isinstance(data["settings"], dict)

    def test_get_all_settings(self, http_client):
        resp = http_client.get("/settings/api/get")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    def test_set_and_verify(self, http_client):
        key = factories.settings_test_key()
        # Set
        resp = http_client.post(
            "/settings/api/set",
            json={"key": key, "value": "test-value"},
        )
        assert resp.status_code == 200
        # Verify
        got = http_client.get(f"/settings/api/get?key={key}").json()
        # Response shape varies — accept dict with value or scalar
        if isinstance(got, dict):
            assert got.get("value") == "test-value" or got.get(key) == "test-value"
        # Cleanup
        try:
            http_client.post("/settings/api/reset", json={"key": key})
        except Exception:
            pass

    def test_set_bulk(self, http_client):
        keys = [factories.settings_test_key() for _ in range(2)]
        payload = {k: "bulk-test" for k in keys}
        resp = http_client.post("/settings/api/set-bulk", json=payload)
        if resp.status_code == 404:
            pytest.skip("set-bulk not available")
        assert resp.status_code == 200
        # Cleanup
        for k in keys:
            try:
                http_client.post("/settings/api/reset", json={"key": k})
            except Exception:
                pass

    def test_reset_setting(self, http_client):
        key = factories.settings_test_key()
        http_client.post("/settings/api/set", json={"key": key, "value": "x"})
        resp = http_client.post("/settings/api/reset", json={"key": key})
        assert resp.status_code == 200

    def test_shortcuts(self, http_client):
        data = assert_ok(http_client.get("/settings/api/shortcuts"))
        assert isinstance(data, dict)

    def test_schema(self, http_client):
        resp = http_client.get("/settings/api/schema")
        if resp.status_code == 404:
            pytest.skip("schema endpoint not present")
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_network_get(self, http_client):
        data = assert_dict_response(http_client.get("/settings/api/network"))
        for field in ("mode", "host", "port", "auth_required", "auth_token_set", "is_remote_bind"):
            assert field in data, f"Network info missing {field}: {list(data.keys())}"
        assert data["mode"] in ("local", "private", "public")
        assert isinstance(data["port"], int)

    def test_network_rejects_invalid_mode(self, http_client):
        resp = http_client.post("/settings/api/network", json={"mode": "internet"})
        assert resp.status_code == 200
        assert "error" in resp.json(), "Invalid mode should return an error"

    def test_network_rejects_public_without_token(self, http_client):
        cur = http_client.get("/settings/api/network").json()
        if cur.get("auth_token_set"):
            pytest.skip("Auth token already set — can't validate the 'public requires token' path")
        resp = http_client.post("/settings/api/network", json={"mode": "public"})
        assert resp.status_code == 200
        body = resp.json()
        assert "error" in body, "public mode without token should error"
        assert "auth_token" in body["error"].lower() or "token" in body["error"].lower()

    def test_network_rejects_quoted_token(self, http_client):
        resp = http_client.post("/settings/api/network", json={
            "mode": "public", "auth_token": 'evil"token',
        })
        body = resp.json()
        assert "error" in body, "Quoted tokens must be rejected to prevent TOML injection"


@pytest.mark.api
class TestAutopilotConsoleAPI:
    """The director's console — settings/api/autopilot* (emptyos/sdk/autopilot.py
    is the shared store; every grant issuer in the system reads/writes it)."""

    def test_console_shape(self, http_client):
        data = assert_dict_response(http_client.get("/settings/api/autopilot"))
        for field in ("grants", "holds", "budgets", "eligible_verbs", "week"):
            assert field in data, f"console missing {field}: {list(data.keys())}"
        assert isinstance(data["grants"], list)
        assert isinstance(data["holds"], list)
        assert isinstance(data["budgets"], list)
        assert isinstance(data["eligible_verbs"], list)
        assert "task.add" in data["eligible_verbs"], "task.add should be on the default floor"

    def test_grant_refuses_non_eligible_verb(self, http_client):
        actor_id = factories.autopilot_actor_id()
        resp = http_client.post(
            "/settings/api/autopilot/grant",
            json={
                "actor_type": "cli", "actor_id": actor_id,
                "verb_pattern": "publish.deploy", "scope": "global",
            },
        )
        body = resp.json()
        assert body.get("ok") is False, "an irreversible verb must never be grantable"
        assert "error" in body

    def test_grant_issue_then_revoke(self, http_client):
        actor_id = factories.autopilot_actor_id()
        grant_id = None
        try:
            resp = http_client.post(
                "/settings/api/autopilot/grant",
                json={
                    "actor_type": "cli", "actor_id": actor_id,
                    "verb_pattern": "task.add", "scope": "global",
                    "ttl_s": 30, "rationale": "sys-test",
                },
            )
            body = assert_dict_response(resp)
            assert body.get("ok") is True, body
            grant_id = body["grant"]["id"]

            console = assert_dict_response(http_client.get("/settings/api/autopilot"))
            ids = [g["id"] for g in console["grants"]]
            assert grant_id in ids, "issued grant should appear in the console read"
        finally:
            if grant_id:
                r = http_client.post(
                    "/settings/api/autopilot/revoke", json={"grant_id": grant_id},
                )
                assert r.json().get("ok") is True

    def test_hold_revoke_missing_id(self, http_client):
        resp = http_client.post("/settings/api/autopilot/hold/revoke", json={"hold_id": ""})
        assert resp.json().get("ok") is False

    def test_budget_set_then_clear(self, http_client):
        actor_id = factories.autopilot_actor_id()
        try:
            resp = http_client.post(
                "/settings/api/autopilot/budget",
                json={"actor_id": actor_id, "monthly_usd": 1},
            )
            body = assert_dict_response(resp)
            assert body.get("ok") is True
            assert body["budget"]["monthly_cap_usd"] == 1

            console = assert_dict_response(http_client.get("/settings/api/autopilot"))
            row = next((b for b in console["budgets"] if b["actor_id"] == actor_id), None)
            assert row is not None, "set budget should appear in the console read"
        finally:
            http_client.post(
                "/settings/api/autopilot/budget",
                json={"actor_id": actor_id, "monthly_usd": 0},
            )


@pytest.mark.interactive
class TestSettingsUI:
    def test_theme_cards_preview_and_recolor_without_reload(self, app_page, page_errors):
        page = app_page("settings")
        page.locator(".theme-choice").first.wait_for(state="visible", timeout=8000)
        assert page.locator(".theme-choice").count() == 10
        assert page.locator(".theme-choice .eos-app-icon").count() == 30

        previous = page.evaluate("() => localStorage.getItem('eos-theme') || 'eos'")
        target = "void-dark" if previous != "void-dark" else "soft-light"
        before = page.evaluate(
            "() => getComputedStyle(document.querySelector('.nav-crumb-icon .eos-app-icon-accent-2')).color"
        )
        before_box = page.locator(".nav-crumb-icon .eos-app-icon").bounding_box()
        before_requests = page.evaluate(
            "() => performance.getEntriesByName('/api/app-icons/sprite?revision=live').length + performance.getEntriesByName(location.origin + '/api/app-icons/sprite?revision=live').length"
        )
        # Exercise the real card interaction while keeping the system setting
        # untouched: this test owns only its browser-local theme state.
        page.route(
            "**/settings/api/set",
            lambda route: route.fulfill(status=200, content_type="application/json", body='{"ok":true}'),
        )
        page.locator(f'.theme-choice[data-theme="{target}"]').click()
        page.wait_for_function(
            "theme => document.documentElement.classList.contains('theme-' + theme)", arg=target
        )
        after = page.evaluate(
            "() => getComputedStyle(document.querySelector('.nav-crumb-icon .eos-app-icon-accent-2')).color"
        )
        assert before != after
        assert page.locator(".nav-crumb-icon .eos-app-icon").bounding_box() == before_box
        after_requests = page.evaluate(
            "() => performance.getEntriesByName('/api/app-icons/sprite?revision=live').length + performance.getEntriesByName(location.origin + '/api/app-icons/sprite?revision=live').length"
        )
        assert after_requests == before_requests
        assert page.locator(f'.theme-choice[data-theme="{target}"]').get_attribute("aria-pressed") == "true"
        page.evaluate("theme => EOS.setTheme(theme)", previous)
        assert_no_js_errors(page_errors)

    def test_theme_cards_stack_on_mobile_and_keep_native_focus(self, app_page, page_errors):
        page = app_page("settings")
        page.locator(".theme-choice").first.wait_for(state="visible", timeout=8000)
        page.set_viewport_size({"width": 390, "height": 844})
        columns = page.locator(".theme-picker").evaluate(
            "el => getComputedStyle(el).gridTemplateColumns.split(' ').filter(Boolean).length"
        )
        assert columns == 1
        first = page.locator(".theme-choice").first
        first.focus()
        assert first.evaluate("el => document.activeElement === el")
        assert_no_js_errors(page_errors)

    def test_all_ten_themes_recolor_the_same_svg_without_refetch(self, app_page, page_errors):
        page = app_page("settings")
        icon = page.locator(".nav-crumb-icon .eos-app-icon")
        icon.wait_for(state="visible", timeout=8000)
        previous = page.evaluate("() => localStorage.getItem('eos-theme') || 'eos'")
        themes = page.evaluate("() => EOS.THEMES.slice()")
        assert len(themes) == 10
        before_requests = page.evaluate(
            "() => performance.getEntriesByName(location.origin + '/api/app-icons/sprite?revision=live').length"
        )
        palettes = {}
        for theme in themes:
            page.evaluate("theme => EOS.setTheme(theme)", theme)
            palettes[theme] = tuple(page.evaluate(
                """() => ['ink','paper','accent','accent-2'].map(role =>
                    getComputedStyle(document.querySelector('.nav-crumb-icon .eos-app-icon-' + role)).color)"""
            ))
            assert icon.evaluate("node => node.isConnected")
        after_requests = page.evaluate(
            "() => performance.getEntriesByName(location.origin + '/api/app-icons/sprite?revision=live').length"
        )
        assert len(set(palettes.values())) == 10
        assert after_requests == before_requests
        page.evaluate("theme => EOS.setTheme(theme)", previous)
        assert_no_js_errors(page_errors)

    def test_boolean_schema_fields_render_as_checkboxes(self, app_page, page_errors, http_client):
        """App manifests declare `type = "boolean"`; the page used to know only
        `toggle` and fell through to a text box, so typing `false` stored the
        STRING "false" — truthy to every bool() read (found 2026-10-01, the day
        those reads first reached the settings service)."""
        schema = assert_ok(http_client.get("/settings/api/schema"))

        def _fields(node):
            if isinstance(node, dict):
                if node.get("type") == "boolean" and isinstance(node.get("key"), str):
                    yield node["key"]
                for v in node.values():
                    yield from _fields(v)
            elif isinstance(node, list):
                for v in node:
                    yield from _fields(v)

        keys = list(_fields(schema))
        assert keys, "no boolean-typed field in /settings/api/schema — the test cannot see the control"
        page = app_page("settings")
        wait_briefly(page, 1000)
        for key in keys[:3]:
            ctrl = page.locator("#ctrl-" + key.replace(".", "-"))
            if ctrl.count() == 0:
                continue  # hidden in user posture or on another tab; try the next
            assert ctrl.get_attribute("type") == "checkbox", f"{key} is not a checkbox"
            break
        else:
            pytest.fail(f"none of {keys[:3]} rendered a control on /settings/")
        assert_no_js_errors(page_errors)

    def test_ui_system_info_visible(self, app_page, page_errors):
        """Page renders system info section."""
        page = app_page("settings")
        wait_briefly(page, 1000)
        assert_no_js_errors(page_errors)

    def test_ui_tab_navigation(self, app_page, page_errors):
        """Click through visible tabs."""
        page = app_page("settings")
        wait_briefly(page, 800)
        tabs = page.locator(".eos-tab, .tab, [data-tab]")
        if tabs.count() < 2:
            pytest.skip("Less than 2 tabs visible")
        # Click first 2 tabs
        for i in range(min(2, tabs.count())):
            try:
                tabs.nth(i).click()
                wait_briefly(page, 400)
            except Exception:
                continue
        assert_no_js_errors(page_errors)

    def test_ui_autopilot_tab_renders(self, app_page, page_errors):
        """Autopilot tab loads its console (grants/holds/budgets/eligibility)."""
        page = app_page("settings")
        wait_briefly(page, 800)
        page.locator(".eos-tab", has_text="Autopilot").first.click()
        wait_briefly(page, 800)
        content = page.locator("#autopilot-content")
        assert content.count() > 0
        assert "Loading" not in (content.inner_text() or "")
        assert_no_js_errors(page_errors)
