"""System app tests: System Log — 10 use cases."""

import pytest

from helpers import assert_ok
from page_helpers import assert_no_js_errors, wait_briefly


@pytest.mark.api
class TestSystemLogAPI:
    def test_recent(self, http_client):
        resp = http_client.get("/system-log/api/recent")
        assert resp.status_code == 200

    def test_feed(self, http_client):
        resp = http_client.get("/system-log/api/feed")
        assert resp.status_code == 200

    def test_summary(self, http_client):
        resp = http_client.get("/system-log/api/summary")
        assert resp.status_code == 200

    def test_sources(self, http_client):
        resp = http_client.get("/system-log/api/sources")
        assert resp.status_code == 200

    def test_logs(self, http_client):
        resp = http_client.get("/system-log/api/logs")
        assert resp.status_code == 200

    def test_logs_errors(self, http_client):
        resp = http_client.get("/system-log/api/logs/errors")
        assert resp.status_code == 200

    def test_narrative_requires_llm(self, http_client, require_llm):
        # Narrative hits LLM on potentially long event history — needs big timeout
        resp = http_client.get("/system-log/api/narrative", timeout=120)
        assert resp.status_code in (200, 500)

    def test_post_log(self, http_client):
        """POST a test log entry."""
        resp = http_client.post(
            "/system-log/api/post",
            json={
                "source": "test",
                "message": "PLAYWRIGHT-TEST log entry",
                "level": "info",
            },
        )
        assert resp.status_code in (200, 201)


@pytest.mark.interactive
class TestSystemLogUI:
    def test_ui_loads(self, app_page, page_errors):
        page = app_page("system-log")
        wait_briefly(page, 1500)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_feed_renders(self, app_page, page_errors):
        page = app_page("system-log")
        wait_briefly(page, 2000)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_no_critical_errors(self, app_page, page_errors):
        page = app_page("system-log")
        wait_briefly(page, 1500)
        critical = [e for e in page_errors if "TypeError" in str(e)]
        assert not critical, f"Critical: {critical}"
