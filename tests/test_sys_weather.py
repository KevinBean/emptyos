"""System app tests: Weather — 10 use cases."""

import pytest

from helpers import assert_ok
from page_helpers import assert_no_js_errors, wait_briefly


@pytest.mark.api
class TestWeatherAPI:
    def test_current(self, http_client):
        """`current` always returns a dict — payload may be OWM-rich, Open-Meteo
        summary, wttr.in one-liner, or `{"error": ...}` when nothing is
        configured. All three branches return 200."""
        data = assert_ok(http_client.get("/weather/api/current"))
        assert isinstance(data, dict)

    def test_current_has_source_or_error(self, http_client):
        """Successful responses identify their source; failures carry an error."""
        data = assert_ok(http_client.get("/weather/api/current"))
        assert "error" in data or "source" in data or data.get("summary") or data.get("description")

    def test_forecast_is_list(self, http_client):
        data = assert_ok(http_client.get("/weather/api/forecast"))
        assert isinstance(data, list)

    def test_history_is_list(self, http_client):
        data = assert_ok(http_client.get("/weather/api/history"))
        assert isinstance(data, list)

    def test_history_respects_days_param(self, http_client):
        data = assert_ok(http_client.get("/weather/api/history?days=3"))
        assert isinstance(data, list)
        assert len(data) <= 3

    def test_config_status_shape(self, http_client):
        data = assert_ok(http_client.get("/weather/api/config-status"))
        assert isinstance(data, dict)
        for key in ("configured", "has_api_key", "has_location"):
            assert key in data, f"missing key: {key}"

    def test_refresh_endpoint(self, http_client):
        """POST /refresh returns either data or an `error` dict; never 404/500."""
        resp = http_client.post("/weather/api/refresh")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)


@pytest.mark.interactive
class TestWeatherUI:
    def test_ui_page_loads(self, app_page, page_errors):
        page = app_page("weather")
        wait_briefly(page, 1500)
        assert_no_js_errors(page_errors, allow_patterns=["fetch", "API"])

    def test_ui_renders_hero_or_setup(self, app_page, page_errors):
        """Either the hero tile renders or the setup-box appears — never a
        blank #content."""
        page = app_page("weather")
        wait_briefly(page, 2000)
        content = page.locator("#content").inner_html()
        assert content.strip(), "weather #content is empty"
        assert ("weather-hero" in content) or ("setup-box" in content), (
            f"neither hero nor setup-box rendered: {content[:200]}"
        )

    def test_ui_refresh_button_present(self, app_page, page_errors):
        page = app_page("weather")
        wait_briefly(page, 1200)
        assert page.locator(".refresh-btn").count() == 1
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])
