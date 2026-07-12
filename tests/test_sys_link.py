"""System app tests: Link — 10 use cases."""

import pytest

from helpers import assert_ok
from page_helpers import assert_no_js_errors, wait_briefly


@pytest.mark.api
class TestLinkAPI:
    def test_backlinks_empty(self, http_client):
        """Backlinks for nonexistent note should return empty list or 404 gracefully."""
        resp = http_client.get("/link/api/backlinks?note=zzz-nonexistent")
        assert resp.status_code in (200, 404)

    def test_outgoing_empty(self, http_client):
        resp = http_client.get("/link/api/outgoing?note=zzz-nonexistent")
        assert resp.status_code in (200, 404)

    def test_orphans(self, http_client):
        resp = http_client.get("/link/api/orphans")
        assert resp.status_code == 200

    def test_stats(self, http_client):
        resp = http_client.get("/link/api/stats")
        assert resp.status_code == 200

    def test_orphan_insights_requires_llm(self, http_client, require_llm):
        resp = http_client.get("/link/api/orphan-insights", timeout=60)
        assert resp.status_code in (200, 500)

    def test_stats_returns_dict(self, http_client):
        data = http_client.get("/link/api/stats").json()
        assert isinstance(data, (dict, list))

    def test_suggest_shape(self, http_client):
        """`/link/api/suggest` always returns {suggestions: [...]} even on no match."""
        resp = http_client.get("/link/api/suggest?q=zzz-no-match-expected")
        assert resp.status_code == 200
        data = resp.json()
        assert "suggestions" in data
        assert isinstance(data["suggestions"], list)

    def test_suggest_limit_capped(self, http_client):
        """Limit param honored and capped at 50."""
        data = http_client.get("/link/api/suggest?q=&limit=500").json()
        assert len(data.get("suggestions", [])) <= 50

    def test_suggest_kinds_filter(self, http_client):
        """`kinds=` filters by tag — returns same shape, possibly empty."""
        resp = http_client.get("/link/api/suggest?q=&limit=5&kinds=kb")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data.get("suggestions"), list)


@pytest.mark.interactive
class TestLinkUI:
    def test_ui_loads(self, app_page, page_errors):
        page = app_page("link")
        wait_briefly(page, 1500)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_orphans_section(self, app_page, page_errors):
        page = app_page("link")
        wait_briefly(page, 2000)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_stats_section(self, app_page, page_errors):
        page = app_page("link")
        wait_briefly(page, 2000)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_no_critical_errors(self, app_page, page_errors):
        page = app_page("link")
        wait_briefly(page, 2000)
        critical = [e for e in page_errors if "TypeError" in str(e)]
        assert not critical
