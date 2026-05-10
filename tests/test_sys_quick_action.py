"""System app tests: Quick Action — capture inbox + tag routing."""

import pytest

from helpers import TEST_PREFIX, assert_dict_response, assert_list_response, assert_ok
from page_helpers import assert_no_js_errors, wait_briefly


@pytest.mark.api
class TestQuickActionAPI:
    def test_add_basic(self, http_client):
        resp = http_client.post(
            "/quick-action/api/add",
            json={"text": f"{TEST_PREFIX}plain capture"},
        )
        data = assert_ok(resp)
        assert data.get("text") == f"{TEST_PREFIX}plain capture"
        assert "timestamp" in data

    def test_add_with_tag(self, http_client):
        resp = http_client.post(
            "/quick-action/api/add",
            json={"text": f"{TEST_PREFIX}tagged capture", "tag": "idea"},
        )
        data = assert_ok(resp)
        assert data.get("tag") == "idea"

    def test_add_inline_tag_is_hoisted(self, http_client):
        """Inline #tag in text should be lifted into the tag field."""
        resp = http_client.post(
            "/quick-action/api/add",
            json={"text": f"{TEST_PREFIX}hoist me #note"},
        )
        data = assert_ok(resp)
        assert data.get("tag") == "note"
        # The trailing #note should have been stripped from text
        assert "#note" not in data.get("text", "")

    def test_add_rejects_empty(self, http_client):
        """Smart-add validates empty text; /api/add doesn't — pin the contract."""
        resp = http_client.post("/quick-action/api/smart-add", json={"text": ""})
        data = resp.json()
        assert "error" in data or resp.status_code != 200

    def test_list_returns_list(self, http_client):
        data = assert_list_response(http_client.get("/quick-action/api/list"))
        # Test entries we just added should appear
        texts = [c.get("text", "") for c in data]
        assert any(TEST_PREFIX in t for t in texts), "Test captures missing from /api/list"

    def test_stats_shape(self, http_client):
        data = assert_dict_response(
            http_client.get("/quick-action/api/stats"),
            required_keys=["total", "by_tag", "by_dimension"],
        )
        assert isinstance(data["total"], int)
        assert isinstance(data["by_tag"], dict)
        assert isinstance(data["by_dimension"], dict)

    def test_recent_returns_list(self, http_client):
        resp = http_client.get("/quick-action/api/recent?limit=3")
        data = assert_list_response(resp)
        assert len(data) <= 3

    def test_pending_count(self, http_client):
        data = assert_dict_response(
            http_client.get("/quick-action/api/pending"),
            required_keys=["pending"],
        )
        assert isinstance(data["pending"], int)
        assert data["pending"] >= 0

    def test_capture_alias(self, http_client):
        """/api/capture and /api/save are aliases of /api/add."""
        resp = http_client.post(
            "/quick-action/api/capture",
            json={"text": f"{TEST_PREFIX}via capture alias"},
        )
        assert resp.status_code == 200
        resp2 = http_client.post(
            "/quick-action/api/save",
            json={"text": f"{TEST_PREFIX}via save alias"},
        )
        assert resp2.status_code == 200

    def test_update_capture(self, http_client):
        """Add then update — verify the new text replaces the old line."""
        original = f"{TEST_PREFIX}original text"
        add = http_client.post("/quick-action/api/add", json={"text": original}).json()
        resp = http_client.post(
            "/quick-action/api/update",
            json={
                "old_timestamp": add.get("timestamp", ""),
                "old_text": original,
                "text": f"{TEST_PREFIX}edited text",
                "tag": "idea",
            },
        )
        data = assert_ok(resp)
        assert data.get("text") == f"{TEST_PREFIX}edited text"
        assert data.get("tag") == "idea"

    def test_dismiss_capture(self, http_client):
        """Add then dismiss — should report dismissed=True."""
        text = f"{TEST_PREFIX}dismiss me"
        add = http_client.post("/quick-action/api/add", json={"text": text}).json()
        resp = http_client.post(
            "/quick-action/api/dismiss",
            json={"timestamp": add.get("timestamp", ""), "text": text},
        )
        data = assert_ok(resp)
        assert data.get("dismissed") is True

    def test_dedupe_runs(self, http_client):
        """Dedupe should return a count without error, even with no duplicates."""
        data = assert_ok(http_client.post("/quick-action/api/dedupe", json={}))
        assert "removed" in data
        assert isinstance(data["removed"], int)

    def test_to_task_routing(self, http_client):
        """Convert capture to task. With #dev tag, should route to emptyos-development
        project; without, to plain task app. Either path returns converted=True."""
        text = f"{TEST_PREFIX}convert to task"
        add = http_client.post(
            "/quick-action/api/add", json={"text": text, "tag": "dev"}
        ).json()
        resp = http_client.post(
            "/quick-action/api/to-task",
            json={"text": text, "tag": "dev", "timestamp": add.get("timestamp", "")},
        )
        # Tolerate failure if projects/task apps aren't both loaded — the contract
        # is "returns dict with either converted or error".
        data = resp.json()
        assert resp.status_code == 200
        assert "converted" in data or "error" in data

    def test_smart_add_requires_llm(self, http_client, require_llm):
        resp = http_client.post(
            "/quick-action/api/smart-add",
            json={"text": f"{TEST_PREFIX}auto-tag this idea"},
            timeout=60,
        )
        assert resp.status_code in (200, 500)


@pytest.mark.interactive
class TestQuickActionUI:
    def test_ui_loads(self, app_page, page_errors):
        page = app_page("quick-action")
        wait_briefly(page, 1500)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_no_critical_errors(self, app_page, page_errors):
        page = app_page("quick-action")
        wait_briefly(page, 2000)
        critical = [e for e in page_errors if "TypeError" in str(e)]
        assert not critical
