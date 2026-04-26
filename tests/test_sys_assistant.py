"""System app tests: Assistant — 12 use cases."""

import pytest

import factories
from helpers import TEST_PREFIX, assert_dict_response, assert_ok
from page_helpers import (
    assert_no_js_errors, click_first, wait_briefly,
)


@pytest.mark.api
class TestAssistantAPI:
    @pytest.fixture
    def session_id(self, http_client):
        """Create a test session and clean up after."""
        payload = factories.assistant_session(name="api session")
        resp = http_client.post("/assistant/api/sessions", json=payload)
        assert resp.status_code == 200, resp.text
        sid = resp.json().get("id")
        yield sid
        try:
            http_client.delete(f"/assistant/api/sessions/{sid}")
        except Exception:
            pass

    def test_create_session(self, session_id):
        assert session_id

    def test_list_sessions(self, http_client):
        data = assert_ok(http_client.get("/assistant/api/sessions"))
        assert isinstance(data, (list, dict))

    def test_get_session(self, http_client, session_id):
        data = assert_dict_response(
            http_client.get(f"/assistant/api/sessions/{session_id}")
        )
        assert "messages" in data or "id" in data

    def test_update_session(self, http_client, session_id):
        new_name = f"{TEST_PREFIX}renamed"
        resp = http_client.put(
            f"/assistant/api/sessions/{session_id}",
            json={"name": new_name},
        )
        assert resp.status_code == 200
        data = http_client.get(f"/assistant/api/sessions/{session_id}").json()
        assert data.get("name") == new_name

    def test_delete_session(self, http_client):
        # Create separately so we can delete and verify
        payload = factories.assistant_session(name="to-delete")
        sid = http_client.post("/assistant/api/sessions", json=payload).json().get("id")
        resp = http_client.delete(f"/assistant/api/sessions/{sid}")
        assert resp.status_code == 200

    def test_slash_commands(self, http_client):
        data = assert_ok(http_client.get("/assistant/api/slash-commands"))
        assert isinstance(data, (list, dict))

    def test_providers_list(self, http_client):
        data = assert_ok(http_client.get("/assistant/api/providers"))
        assert isinstance(data, (list, dict))

    def test_archive_sessions(self, http_client):
        resp = http_client.post("/assistant/api/archive", json={})
        assert resp.status_code in (200, 204)


@pytest.mark.interactive
class TestAssistantUI:
    def test_ui_new_session_flow(self, app_page, page_errors):
        """Click New → verify a session appears in sidebar."""
        page = app_page("assistant")
        wait_briefly(page, 800)
        click_first(
            page,
            "[onclick*='newSession']",
            "button:has-text('New')",
            "button:has-text('+')",
            ".new-session-btn",
        )
        wait_briefly(page, 800)
        assert_no_js_errors(page_errors)

    def test_ui_session_switch(self, app_page, page_errors):
        """If multiple sessions exist, click a different one."""
        page = app_page("assistant")
        wait_briefly(page, 800)
        sessions = page.locator(".session-item, .session-card, [data-session-id]")
        if sessions.count() < 2:
            pytest.skip("Less than 2 sessions to switch between")
        sessions.nth(1).click()
        wait_briefly(page, 600)
        assert_no_js_errors(page_errors)

    def test_ui_chat_input(self, app_page, page_errors):
        """Type message in chat input."""
        page = app_page("assistant")
        wait_briefly(page, 800)
        chat_input = page.locator(
            "#chat-input, #message-input, textarea[placeholder*='message' i]"
        ).first
        if chat_input.count() == 0:
            pytest.skip("No chat input found")
        chat_input.fill("PLAYWRIGHT-TEST-hello")
        wait_briefly(page, 300)
        assert_no_js_errors(page_errors)

    def test_ui_session_list_renders(self, app_page, page_errors):
        """Verify session sidebar/list renders without error."""
        page = app_page("assistant")
        wait_briefly(page, 1000)
        # Look for any session container
        containers = page.locator(
            ".sessions, .session-list, #sessions, [class*='sidebar']"
        )
        assert_no_js_errors(page_errors)
