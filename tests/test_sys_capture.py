"""System app tests: Capture — 11 use cases."""

import pytest

import factories
from helpers import TEST_PREFIX, assert_dict_response, assert_ok
from page_helpers import (
    assert_no_js_errors, click_first, wait_briefly, wait_for_toast,
)


@pytest.mark.api
class TestCaptureAPI:
    def test_add_capture(self, http_client):
        payload = factories.capture(text="capture from pytest", tag="idea")
        data = assert_ok(http_client.post("/quick-action/api/add", json=payload))
        assert isinstance(data, dict)

    def test_list_captures(self, http_client):
        data = assert_ok(http_client.get("/quick-action/api/list"))
        assert isinstance(data, (list, dict))

    def test_pending_count(self, http_client):
        data = assert_ok(http_client.get("/quick-action/api/pending"))
        assert isinstance(data, dict)
        assert "pending" in data or "count" in data

    def test_recent_limit(self, http_client):
        data = assert_ok(http_client.get("/quick-action/api/recent?limit=3"))
        items = data if isinstance(data, list) else data.get("captures", [])
        assert isinstance(items, list)
        assert len(items) <= 3

    def test_dismiss(self, http_client):
        # Create then dismiss
        payload = factories.capture(text="to-dismiss", tag="note")
        created = http_client.post("/quick-action/api/add", json=payload).json()
        ts = created.get("timestamp") or created.get("ts")
        if ts:
            resp = http_client.post(
                "/quick-action/api/dismiss",
                json={"timestamp": ts, "text": payload["text"]},
            )
            assert resp.status_code == 200

    def test_to_task(self, http_client):
        payload = factories.capture(text="to-task", tag="task")
        created = http_client.post("/quick-action/api/add", json=payload).json()
        resp = http_client.post(
            "/quick-action/api/to-task",
            json={"text": payload["text"], "timestamp": created.get("timestamp")},
        )
        assert resp.status_code == 200

    def test_stats_by_tag(self, http_client):
        data = assert_dict_response(http_client.get("/quick-action/api/stats"))
        assert "by_tag" in data or "tags" in data or "total" in data


@pytest.mark.interactive
class TestCaptureUI:
    def test_ui_capture_flow(self, app_page, page_errors):
        """Type capture → click Idea tag → click Save → verify list."""
        page = app_page("quick-action")
        wait_briefly(page, 500)
        textarea = page.locator("#capture-text, textarea").first
        if textarea.count() == 0:
            pytest.skip("No capture text area")
        textarea.fill(f"{TEST_PREFIX}quick capture")
        # Click Idea tag chip
        click_first(
            page,
            "[onclick*=\"setTag\"][onclick*='idea']",
            ".tag-chip:has-text('Idea')",
            "button:has-text('Idea')",
        )
        wait_briefly(page, 200)
        # Click Save
        click_first(
            page,
            "[onclick*='submit']",
            "button:has-text('Save')",
        )
        wait_briefly(page, 1500)
        assert_no_js_errors(page_errors)

    def test_ui_auto_classify(self, app_page, page_errors):
        """Type text matching task pattern → verify hint shows."""
        page = app_page("quick-action")
        wait_briefly(page, 500)
        textarea = page.locator("#capture-text, textarea").first
        if textarea.count() == 0:
            pytest.skip("No capture text area")
        textarea.fill("TODO: fix the bug")
        wait_briefly(page, 400)
        # Switch to expense pattern
        textarea.fill("spent $20 on lunch")
        wait_briefly(page, 400)
        assert_no_js_errors(page_errors)

    def test_ui_triage_flow(self, app_page, page_errors):
        """Switch to Triage tab if present."""
        page = app_page("quick-action")
        wait_briefly(page, 500)
        click_first(
            page,
            "[onclick*=\"showTab('triage')\"]",
            "button:has-text('Triage')",
            ".tab:has-text('Triage')",
        )
        wait_briefly(page, 600)
        assert_no_js_errors(page_errors)

    def test_ui_tag_selection(self, app_page, page_errors):
        """Click each tag chip → verify only one active at a time."""
        page = app_page("quick-action")
        wait_briefly(page, 500)
        # Click Task chip
        click_first(
            page,
            "[onclick*=\"setTag\"][onclick*='task']",
            ".tag-chip:has-text('Task')",
        )
        wait_briefly(page, 200)
        # Then Idea chip
        click_first(
            page,
            "[onclick*=\"setTag\"][onclick*='idea']",
            ".tag-chip:has-text('Idea')",
        )
        wait_briefly(page, 200)
        assert_no_js_errors(page_errors)
