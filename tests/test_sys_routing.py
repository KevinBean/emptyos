"""System app tests: Routing — 13 use cases.

Local-behavior tests for validation, status gate, caching. One live test hits
the real OSRM demo and is gated behind `@pytest.mark.slow`.
"""

import pytest

from helpers import assert_ok
from page_helpers import assert_no_js_errors, wait_briefly


def _post(http_client, body, **kw):
    return http_client.post("/routing/api/route", json=body, **kw)


@pytest.mark.api
class TestRoutingAPI:

    def test_status_shape(self, http_client):
        data = assert_ok(http_client.get("/routing/api/status"))
        assert "enabled" in data
        assert "mode" in data
        assert "using_demo" in data
        assert isinstance(data["enabled"], bool)
        assert data["mode"] in ("local", "private", "public")

    def test_status_enabled_on_non_public_mode(self, http_client):
        data = assert_ok(http_client.get("/routing/api/status"))
        if data["mode"] != "public":
            assert data["enabled"] is True

    def test_cache_stats_shape(self, http_client):
        data = assert_ok(http_client.get("/routing/api/cache-stats"))
        assert "routes" in data
        assert isinstance(data["routes"], int)

    def test_empty_points(self, http_client):
        r = assert_ok(_post(http_client, {"points": [], "profile": "driving"}))
        assert r.get("error")

    def test_single_point(self, http_client):
        r = assert_ok(_post(http_client, {
            "points": [[-33.86, 151.21]], "profile": "driving",
        }))
        assert r.get("error")

    def test_invalid_json(self, http_client):
        resp = http_client.post(
            "/routing/api/route",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        data = resp.json()
        assert data.get("error")

    def test_missing_points_key(self, http_client):
        r = assert_ok(_post(http_client, {"profile": "driving"}))
        assert r.get("error")

    def test_bogus_point_shapes_coerce_out(self, http_client):
        # Garbage mixed with one valid point → <2 valid → error.
        r = assert_ok(_post(http_client, {
            "points": ["not-a-point", None, [-33.86, 151.21], {"lat": "bad"}],
            "profile": "driving",
        }))
        assert r.get("error")

    def test_dict_points_accepted(self, http_client):
        # Dict-shaped points should coerce — this will try the live service,
        # so we accept either success shape OR a clear service/network error
        # (we don't want the suite flaky when Nominatim/OSRM demo is down).
        r = assert_ok(_post(http_client, {
            "points": [{"lat": -33.86, "lng": 151.21}, {"lat": -33.89, "lon": 151.27}],
            "profile": "driving",
        }, timeout=30))
        assert "geometry" in r or "error" in r

    def test_unknown_profile_falls_back_to_driving(self, http_client):
        # Validation downgrades unknown profiles; should not reject outright.
        r = assert_ok(_post(http_client, {
            "points": [[-33.86, 151.21], [-33.87, 151.22]],
            "profile": "rollerblading",
        }, timeout=30))
        # Either routed (profile downgraded silently) or a network error — never
        # a profile-validation error from our own code.
        if "error" in r:
            assert "profile" not in r["error"].lower()
        else:
            assert r.get("profile") == "driving"


@pytest.mark.api
@pytest.mark.slow
class TestRoutingLive:
    """Hits the real OSRM demo — skipped unless explicitly opted in."""

    def test_live_two_stop_driving(self, http_client):
        r = assert_ok(_post(http_client, {
            "points": [[-33.8688, 151.2093], [-33.8906, 151.2724]],  # CBD → Bondi
            "profile": "driving",
        }, timeout=30))
        assert "geometry" in r, f"expected geometry, got {r}"
        assert len(r["geometry"]) > 10
        assert r["distance_m"] > 1000  # at least 1 km
        assert r["duration_s"] > 60   # at least 1 min

    def test_live_caching(self, http_client):
        body = {"points": [[-33.8688, 151.2093], [-33.8906, 151.2724]],
                "profile": "driving"}
        a = assert_ok(_post(http_client, body, timeout=30))
        b = assert_ok(_post(http_client, body, timeout=5))  # cached
        if "geometry" in a:
            assert a == b

    def test_live_three_stop(self, http_client):
        r = assert_ok(_post(http_client, {
            "points": [[-33.79, 151.08], [-33.86, 151.21], [-33.89, 151.27]],
            "profile": "driving",
        }, timeout=30))
        if "geometry" in r:
            # Legs = stops - 1
            assert len(r.get("legs", [])) == 2


@pytest.mark.interactive
class TestRoutingUI:
    def test_ui_loads(self, app_page, page_errors):
        page = app_page("routing")
        wait_briefly(page, 1500)
        assert page.locator("h1").first.text_content().strip() == "Routing"
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_panels_render(self, app_page, page_errors):
        page = app_page("routing")
        wait_briefly(page, 1500)
        assert page.locator("#stops").count() == 1
        assert page.locator("#profile").count() == 1
        assert page.locator("#route-btn").count() == 1
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_result_panel_initially_hidden(self, app_page, page_errors):
        page = app_page("routing")
        wait_briefly(page, 1500)
        # Result panel hidden until a route is planned
        result = page.locator("#result-panel")
        assert result.count() == 1
        assert "none" in (result.get_attribute("style") or "").replace(" ", "")
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])
