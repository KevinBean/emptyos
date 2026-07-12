"""System app tests: Gesture — 10 use cases.

Camera + MediaPipe run in-browser only, so UI tests cover the page shell
(loads, panels render, action form is editable) — not the actual gesture
recognition. The API ingest path is exercised with synthetic payloads.
"""

import pytest

from helpers import assert_ok, assert_dict_response, TEST_PREFIX
from page_helpers import assert_no_js_errors, wait_briefly


@pytest.mark.api
class TestGestureAPI:
    def test_ping_lists_classes(self, http_client):
        data = assert_dict_response(http_client.get("/gesture/api/ping"))
        assert data.get("ok") is True
        assert isinstance(data.get("classes"), list) and len(data["classes"]) == 7

    def test_detect_unknown_rejected(self, http_client):
        resp = http_client.post("/gesture/api/detected", json={"gesture": "NotAClass"})
        data = assert_dict_response(resp)
        assert data.get("ok") is False

    def test_detect_records_and_returns_action(self, http_client):
        resp = http_client.post(
            "/gesture/api/detected",
            json={"gesture": "Open_Palm", "confidence": 0.92},
        )
        data = assert_dict_response(resp)
        assert data.get("ok") is True
        assert data["entry"]["gesture"] == "Open_Palm"
        assert 0.0 <= data["entry"]["confidence"] <= 1.0
        assert isinstance(data.get("action"), str)

    def test_detect_clamps_confidence(self, http_client):
        resp = http_client.post(
            "/gesture/api/detected",
            json={"gesture": "Thumb_Up", "confidence": 5.0},
        )
        data = assert_dict_response(resp)
        assert data.get("ok") is True
        assert data["entry"]["confidence"] == 1.0

    def test_history_returns_recent(self, http_client):
        http_client.post("/gesture/api/detected", json={"gesture": "Victory", "confidence": 0.8})
        data = assert_dict_response(http_client.get("/gesture/api/history?limit=5"))
        assert isinstance(data.get("history"), list)
        assert data["history"], "history should have at least one entry"
        assert data["history"][0]["gesture"] == "Victory"  # newest first

    def test_history_clear(self, http_client):
        http_client.post("/gesture/api/detected", json={"gesture": "ILoveYou", "confidence": 0.9})
        assert_ok(http_client.post("/gesture/api/history/clear", json={}))
        data = assert_dict_response(http_client.get("/gesture/api/history"))
        assert data.get("history") == []

    def test_actions_get_set_roundtrip(self, http_client):
        original = http_client.get("/gesture/api/actions").json().get("actions", {})
        try:
            label = f"{TEST_PREFIX}my-action"
            http_client.post(
                "/gesture/api/actions",
                json={"actions": {"Open_Palm": label}},
            )
            after = http_client.get("/gesture/api/actions").json()
            assert after["actions"]["Open_Palm"] == label
        finally:
            if original:
                http_client.post("/gesture/api/actions", json={"actions": original})

    def test_actions_ignores_unknown_class(self, http_client):
        resp = http_client.post(
            "/gesture/api/actions",
            json={"actions": {"NotARealClass": "nope"}},
        )
        data = assert_dict_response(resp)
        assert data.get("ok") is True
        assert "NotARealClass" not in data.get("actions", {})


@pytest.mark.interactive
class TestGestureUI:
    def test_ui_page_loads(self, app_page, page_errors):
        page = app_page("gesture")
        wait_briefly(page, 600)
        assert page.locator("#video").count() == 1
        assert page.locator("#start-btn").count() == 1
        assert_no_js_errors(page_errors)

    def test_ui_action_row_editable(self, app_page, page_errors):
        page = app_page("gesture")
        wait_briefly(page, 800)
        row = page.locator('input[data-class="Open_Palm"]').first
        assert row.count() == 1
        row.fill(f"{TEST_PREFIX}edited")
        assert row.input_value() == f"{TEST_PREFIX}edited"
        assert_no_js_errors(page_errors)
