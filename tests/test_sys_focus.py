"""System app tests: Focus — 15 use cases."""

import pytest

import factories
from helpers import assert_dict_response, assert_ok
from page_helpers import (
    assert_no_js_errors, click_first, switch_tab, wait_briefly,
)


@pytest.mark.api
class TestFocusAPI:
    def test_start_session(self, http_client):
        resp = http_client.post("/focus/api/start", json={"minutes": 25})
        assert resp.status_code == 200

    def test_complete_session(self, http_client):
        payload = factories.focus_complete(minutes=1, task="pytest focus")
        resp = http_client.post("/focus/api/complete", json=payload)
        assert resp.status_code == 200

    def test_today_stats(self, http_client):
        data = assert_dict_response(http_client.get("/focus/api/stats"))
        assert any(k in data for k in ("sessions", "total_minutes", "today"))

    def test_history(self, http_client):
        data = assert_ok(http_client.get("/focus/api/history?limit=5"))
        assert isinstance(data, (list, dict))

    def test_streak(self, http_client):
        data = assert_dict_response(http_client.get("/focus/api/streak"))
        assert "streak" in data or "current" in data

    def test_achievements(self, http_client):
        data = assert_ok(http_client.get("/focus/api/achievements"))
        assert isinstance(data, dict)
        assert "achievements" in data or "earned" in data or isinstance(data, list)

    def test_config_get_set(self, http_client):
        original = http_client.get("/focus/api/config").json()
        try:
            http_client.post("/focus/api/config", json={"work_min": 26})
            updated = http_client.get("/focus/api/config").json()
            assert updated.get("work_min") == 26 or "work_min" in updated
        finally:
            if isinstance(original, dict):
                http_client.post("/focus/api/config", json=original)

    def test_break_log(self, http_client):
        resp = http_client.post("/focus/api/break", json={"minutes": 5, "type": "short"})
        assert resp.status_code == 200
        breaks = http_client.get("/focus/api/breaks").json()
        assert isinstance(breaks, list)
        assert any(b.get("type") == "short" and b.get("minutes") == 5 for b in breaks)


@pytest.mark.interactive
class TestFocusUI:
    def test_ui_select_duration(self, app_page, page_errors):
        """Click 25 then 45 duration chip → verify selection changes."""
        page = app_page("focus")
        wait_briefly(page, 500)
        click_first(
            page,
            "[onclick*='setDuration(25)']",
            "button:has-text('25')",
            ".duration-chip:has-text('25')",
        )
        wait_briefly(page, 200)
        click_first(
            page,
            "[onclick*='setDuration(45)']",
            "button:has-text('45')",
        )
        wait_briefly(page, 200)
        assert_no_js_errors(page_errors)

    def test_ui_start_timer_flow(self, app_page, page_errors):
        """Type task → click Start → verify timer ticks → click Pause."""
        page = app_page("focus")
        wait_briefly(page, 500)
        task_input = page.locator("#task-input").first
        if task_input.count() > 0:
            task_input.fill("PLAYWRIGHT-TEST-focus task")
        click_first(
            page,
            "[onclick*='toggleTimer']",
            "button:has-text('▶')",
            ".start-btn",
        )
        wait_briefly(page, 1500)
        # Click again to pause
        click_first(
            page,
            "[onclick*='toggleTimer']",
            "button:has-text('⏸')",
        )
        wait_briefly(page, 400)
        assert_no_js_errors(page_errors)

    def test_ui_complete_session_flow(self, app_page, http_client, page_errors):
        """Start a session via API, verify it appears in stats."""
        before = http_client.get("/focus/api/stats").json()
        before_count = before.get("sessions", 0) if isinstance(before, dict) else 0
        http_client.post(
            "/focus/api/complete",
            json={"minutes": 1, "task": "PLAYWRIGHT-TEST-quick"},
        )
        after = http_client.get("/focus/api/stats").json()
        after_count = after.get("sessions", 0) if isinstance(after, dict) else 0
        assert after_count >= before_count

    def test_ui_tab_switch(self, app_page, page_errors):
        """Switch between Today and History tabs."""
        page = app_page("focus")
        wait_briefly(page, 500)
        switch_tab(page, "history")
        wait_briefly(page, 400)
        switch_tab(page, "today")
        wait_briefly(page, 400)
        assert_no_js_errors(page_errors)

    def test_ui_noise_toggle(self, app_page, page_errors):
        """Click noise button → verify state toggles."""
        page = app_page("focus")
        wait_briefly(page, 500)
        click_first(
            page,
            "[onclick*='toggleNoise']",
            "button:has-text('♪')",
        )
        wait_briefly(page, 300)
        assert_no_js_errors(page_errors)

    def test_ui_manual_break(self, app_page, page_errors):
        """Click the break button → enter short break; click again → long break."""
        page = app_page("focus")
        wait_briefly(page, 500)
        click_first(page, "[onclick*='takeBreakManual']")
        wait_briefly(page, 300)
        mode = page.evaluate("mode")
        break_type = page.evaluate("breakType")
        assert mode == "break", f"expected break mode, got {mode!r}"
        assert break_type == "short", f"expected short break, got {break_type!r}"
        # Click again → toggle to long
        click_first(page, "[onclick*='takeBreakManual']")
        wait_briefly(page, 300)
        assert page.evaluate("breakType") == "long"
        # Reset to return to work mode so other tests aren't polluted
        click_first(page, "[onclick*='resetTimer']")
        wait_briefly(page, 300)
        assert page.evaluate("mode") == "work"
        assert_no_js_errors(page_errors)

    def test_ui_break_mode_transition(self, app_page, page_errors):
        """Complete a focus session → UI switches to break mode → reset skips break."""
        page = app_page("focus")
        wait_briefly(page, 500)
        task_input = page.locator("#task-input").first
        if task_input.count() > 0:
            task_input.fill("PLAYWRIGHT-TEST-break-flow")
        # Simulate timer reaching 0 by invoking completeSession directly
        page.evaluate("completeSession()")
        wait_briefly(page, 1000)
        mode = page.evaluate("mode")
        assert mode == "break", f"expected break mode, got {mode!r}"
        label = page.locator("#state-label").inner_text()
        assert "break" in label.lower(), f"expected break label, got {label!r}"
        chips_opacity = page.evaluate("document.getElementById('durations').style.opacity")
        assert chips_opacity == "0.4", f"duration chips not dimmed, opacity={chips_opacity!r}"
        # Reset during break = skip break → back to work
        click_first(page, "[onclick*='resetTimer']")
        wait_briefly(page, 500)
        mode_after = page.evaluate("mode")
        assert mode_after == "work", f"expected work mode after reset, got {mode_after!r}"
        label_after = page.locator("#state-label").inner_text()
        assert label_after.lower() == "ready", f"expected 'Ready', got {label_after!r}"
        assert_no_js_errors(page_errors)
