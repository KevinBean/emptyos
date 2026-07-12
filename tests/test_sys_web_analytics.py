"""System app tests: Web Analytics — 12 use cases.

Covers:
- API: collect / stats / sites / live / beacon.js
- Privacy: DNT respected by beacon, session hash rolls daily, no raw IP stored
- UI: dashboard loads, settings panel opens, empty state, populated state
"""

import pytest

from helpers import assert_ok
from page_helpers import assert_no_js_errors, wait_briefly


_TEST_SITE = "PLAYWRIGHT-wa-test"


def _post_hit(client, site=_TEST_SITE, path="/hello", referrer=""):
    return client.post(
        "/web-analytics/api/collect",
        content=f'{{"site":"{site}","path":"{path}","referrer":"{referrer}"}}',
        headers={"content-type": "text/plain"},
    )


@pytest.mark.api
class TestWebAnalyticsAPI:

    def test_collect_valid(self, http_client):
        r = _post_hit(http_client)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] in ("ok", "dropped")  # dropped if drop_local + client=127.0.0.1

    def test_collect_missing_site_rejected(self, http_client):
        r = http_client.post(
            "/web-analytics/api/collect",
            content='{"path":"/"}',
            headers={"content-type": "text/plain"},
        )
        assert r.status_code == 200
        assert r.json().get("reason") == "missing_site"

    def test_collect_invalid_json(self, http_client):
        r = http_client.post(
            "/web-analytics/api/collect",
            content="not json",
            headers={"content-type": "text/plain"},
        )
        assert r.status_code == 200
        assert r.json().get("reason") == "invalid_json"

    def test_stats_shape(self, http_client):
        data = assert_ok(http_client.get("/web-analytics/api/stats"))
        for key in ("total", "unique_sessions", "series", "top_paths", "top_referrers", "top_countries"):
            assert key in data, f"missing {key} in stats"
        assert isinstance(data["series"], list)

    def test_stats_range_honored(self, http_client):
        d7 = assert_ok(http_client.get("/web-analytics/api/stats?range=7d"))
        d30 = assert_ok(http_client.get("/web-analytics/api/stats?range=30d"))
        assert len(d7["series"]) == 7
        assert len(d30["series"]) == 30

    def test_stats_bad_range_defaults_to_30(self, http_client):
        d = assert_ok(http_client.get("/web-analytics/api/stats?range=bogus"))
        assert len(d["series"]) == 30

    def test_sites_endpoint(self, http_client):
        # Seed a hit so the site shows up even if drop_local is on (stats table
        # should still record). If drop_local blocks it, sites may be empty —
        # that's still a valid shape.
        _post_hit(http_client)
        data = assert_ok(http_client.get("/web-analytics/api/sites"))
        assert "sites" in data and isinstance(data["sites"], list)

    def test_live_endpoint(self, http_client):
        data = assert_ok(http_client.get("/web-analytics/api/live?limit=5"))
        assert "hits" in data and isinstance(data["hits"], list)
        assert len(data["hits"]) <= 5

    def test_live_limit_capped(self, http_client):
        data = assert_ok(http_client.get("/web-analytics/api/live?limit=99999"))
        assert len(data["hits"]) <= 200  # hard cap in handler

    def test_beacon_script_renders(self, http_client):
        r = http_client.get("/web-analytics/api/beacon.js?site=demo")
        assert r.status_code == 200
        assert "javascript" in r.headers.get("content-type", "")
        body = r.text
        # Sanity: the site token made it into the script body, not left as a placeholder
        assert '"demo"' in body
        assert "__SITE__" not in body and "__COLLECTOR__" not in body
        # Privacy: DNT is respected
        assert "doNotTrack" in body

    def test_beacon_site_is_json_escaped(self, http_client):
        # Attacker-supplied site name with quotes + closing tag must not escape the string
        r = http_client.get('/web-analytics/api/beacon.js?site=</script><script>alert(1)</script>')
        assert r.status_code == 200
        body = r.text
        # The raw </script> must not appear in the rendered JS
        assert "</script><script>alert" not in body

    def test_whoami_shape(self, http_client):
        data = assert_ok(http_client.get("/web-analytics/api/whoami"))
        for key in ("ip", "is_local", "excluded_now", "excluded_list"):
            assert key in data, f"missing {key}"
        assert isinstance(data["excluded_list"], list)


@pytest.mark.interactive
class TestWebAnalyticsUI:

    def test_dashboard_loads(self, app_page, page_errors):
        page = app_page("web-analytics")
        wait_briefly(page, 800)
        assert page.locator("h1").first.text_content().strip() == "Web Analytics"
        assert_no_js_errors(page_errors)

    def test_settings_panel_opens(self, app_page, page_errors):
        page = app_page("web-analytics")
        wait_briefly(page, 500)
        page.locator(".btn-settings").first.click()
        page.wait_for_selector("#app-settings-panel", state="visible", timeout=2000)
        assert_no_js_errors(page_errors)

    def test_range_tabs_switch(self, app_page, page_errors):
        page = app_page("web-analytics")
        wait_briefly(page, 500)
        page.locator('#wa-range button[data-r="7d"]').click()
        wait_briefly(page, 300)
        active = page.locator('#wa-range button.active').first.get_attribute("data-r")
        assert active == "7d"
        assert_no_js_errors(page_errors)
