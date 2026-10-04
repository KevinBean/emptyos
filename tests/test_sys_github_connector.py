"""System app tests: github-connector — sync issues/PRs into projects, and
expose the same reads as [[provides.verbs]] action nodes for workflows.
"""

import pytest
from helpers import assert_dict_response, assert_ok


@pytest.mark.api
class TestGitHubConnectorAPI:
    def test_status_no_token(self, http_client):
        """No token configured on the test daemon — connected must read False,
        never raise."""
        data = assert_dict_response(http_client.get("/github-connector/api/status"))
        assert data.get("connected") is False
        assert "reason" in data

    def test_sync_issues_requires_repo(self, http_client):
        resp = http_client.post("/github-connector/api/sync-issues", json={})
        data = assert_ok(resp)
        assert "error" in data

    def test_pr_status_requires_repo(self, http_client):
        resp = http_client.post("/github-connector/api/pr-status", json={})
        data = assert_ok(resp)
        assert "error" in data

    def test_import_issue_requires_number(self, http_client):
        resp = http_client.post("/github-connector/api/import-issue", json={})
        data = assert_ok(resp)
        assert "error" in data

    def test_import_issue_requires_repo(self, http_client):
        """number is present but no repo configured/passed -> repo error, not a crash."""
        resp = http_client.post("/github-connector/api/import-issue", json={"number": 1})
        data = assert_ok(resp)
        assert "error" in data
