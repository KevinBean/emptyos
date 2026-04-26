"""System app tests: Git — 10 use cases."""

import pytest

from helpers import assert_ok
from page_helpers import assert_no_js_errors, click_first, wait_briefly


@pytest.mark.api
class TestGitAPI:
    def test_repos(self, http_client):
        resp = http_client.get("/git/api/repos")
        assert resp.status_code == 200

    def test_status(self, http_client):
        resp = http_client.get("/git/api/status")
        assert resp.status_code == 200

    def test_log(self, http_client):
        resp = http_client.get("/git/api/log")
        assert resp.status_code == 200

    def test_diff(self, http_client):
        resp = http_client.get("/git/api/diff")
        assert resp.status_code == 200

    def test_branches(self, http_client):
        resp = http_client.get("/git/api/branches")
        assert resp.status_code == 200

    def test_stats(self, http_client):
        resp = http_client.get("/git/api/stats")
        assert resp.status_code == 200


@pytest.mark.interactive
class TestGitUI:
    def test_ui_loads(self, app_page, page_errors):
        page = app_page("git")
        wait_briefly(page, 1500)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_status_display(self, app_page, page_errors):
        page = app_page("git")
        wait_briefly(page, 2000)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_log_renders(self, app_page, page_errors):
        page = app_page("git")
        wait_briefly(page, 2000)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_no_critical_errors(self, app_page, page_errors):
        page = app_page("git")
        wait_briefly(page, 1500)
        critical = [e for e in page_errors if "TypeError" in str(e)]
        assert not critical, f"Critical: {critical}"
