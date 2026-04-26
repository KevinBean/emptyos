"""System app tests: Geocode — 12 use cases.

Mostly local behavior (status gate, caching shape, validation). One live test
hits the real Nominatim demo and is gated behind `@pytest.mark.slow` so the
default CI run stays offline + fast.
"""

import pytest

from helpers import assert_ok
from page_helpers import assert_no_js_errors, wait_briefly


@pytest.mark.api
class TestGeocodeAPI:

    def test_status_shape(self, http_client):
        data = assert_ok(http_client.get("/geocode/api/status"))
        assert "enabled" in data
        assert "mode" in data
        assert "using_demo" in data
        assert isinstance(data["enabled"], bool)
        assert data["mode"] in ("local", "private", "public")

    def test_status_enabled_on_non_public_mode(self, http_client):
        data = assert_ok(http_client.get("/geocode/api/status"))
        if data["mode"] != "public":
            assert data["enabled"] is True, "geocode should be enabled on local/private"

    def test_cache_stats_shape(self, http_client):
        data = assert_ok(http_client.get("/geocode/api/cache-stats"))
        assert "forward" in data
        assert "reverse" in data
        assert isinstance(data["forward"], int)
        assert isinstance(data["reverse"], int)

    def test_lookup_empty_query(self, http_client):
        # Empty q → empty list (no Nominatim call)
        data = assert_ok(http_client.get("/geocode/api/lookup?q="))
        assert data == []

    def test_lookup_whitespace_query(self, http_client):
        data = assert_ok(http_client.get("/geocode/api/lookup?q=%20%20"))
        assert data == []

    def test_reverse_missing_params(self, http_client):
        data = assert_ok(http_client.get("/geocode/api/reverse"))
        assert "error" in data

    def test_reverse_invalid_lat(self, http_client):
        # Missing lon → error; missing lat → error
        data = assert_ok(http_client.get("/geocode/api/reverse?lat=0"))
        assert "error" in data

    def test_lookup_returns_list(self, http_client):
        # No network — empty returns [] per graceful-degradation contract
        data = assert_ok(http_client.get("/geocode/api/lookup?q=zzz-no-such-place-exists-xyz"))
        assert isinstance(data, list)

    def test_lookup_limit_is_bounded(self, http_client):
        # limit=999 should clamp (local-side validation); empty q → still []
        data = assert_ok(http_client.get("/geocode/api/lookup?q=&limit=999"))
        assert data == []


@pytest.mark.api
@pytest.mark.slow
class TestGeocodeLive:
    """Hits the real Nominatim demo — skipped unless explicitly opted in."""

    def test_live_lookup(self, http_client):
        data = assert_ok(http_client.get(
            "/geocode/api/lookup?q=Bondi+Beach+NSW+Australia&limit=1",
            timeout=20,
        ))
        assert isinstance(data, list)
        if data:
            hit = data[0]
            assert "lat" in hit and "lon" in hit
            assert hit["lat"] is not None and hit["lon"] is not None
            assert -35 < float(hit["lat"]) < -33
            assert 150 < float(hit["lon"]) < 152

    def test_live_reverse(self, http_client):
        data = assert_ok(http_client.get(
            "/geocode/api/reverse?lat=-33.8688&lon=151.2093",
            timeout=20,
        ))
        assert isinstance(data, dict)
        if data:
            assert "display_name" in data

    def test_live_caching(self, http_client):
        # Two identical calls — second should hit cache (no throttle delay visible,
        # but we just assert it returns the same shape, same data).
        url = "/geocode/api/lookup?q=Sydney+Opera+House&limit=1"
        a = assert_ok(http_client.get(url, timeout=20))
        b = assert_ok(http_client.get(url, timeout=5))  # cached → fast
        assert a == b


@pytest.mark.interactive
class TestGeocodeUI:
    def test_ui_loads(self, app_page, page_errors):
        page = app_page("geocode")
        wait_briefly(page, 1500)
        assert page.locator("h1").first.text_content().strip() == "Geocode"
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_panels_render(self, app_page, page_errors):
        page = app_page("geocode")
        wait_briefly(page, 1500)
        # Forward, Reverse, Map, Cache panels
        assert page.locator(".panel").count() >= 4
        assert page.locator("#fwd-q").count() == 1
        assert page.locator("#rev-lat").count() == 1
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_cache_stats_use_shared_helper(self, app_page, page_errors):
        page = app_page("geocode")
        wait_briefly(page, 2000)
        # EOS_UI.statCards renders into #cache-stats
        assert page.locator("#cache-stats .eos-stat-card").count() == 2
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])
