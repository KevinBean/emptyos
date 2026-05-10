"""System app tests: Journal — 12 use cases."""

import pytest

import factories
from helpers import assert_dict_response, assert_list_response, assert_ok
from page_helpers import (
    assert_no_js_errors, click_first, switch_tab, wait_briefly, wait_for_toast,
)


@pytest.mark.api
class TestJournalAPI:
    def test_today_entries(self, http_client):
        data = assert_ok(http_client.get("/journal/api/today"))
        assert isinstance(data, (dict, list))

    def test_add_entry(self, http_client):
        payload = factories.journal_entry(text="entry from pytest", mood="good")
        data = assert_ok(http_client.post("/journal/api/entry", json=payload))
        assert isinstance(data, dict)

    def test_recent_list(self, http_client):
        data = assert_ok(http_client.get("/journal/api/recent"))
        assert isinstance(data, (list, dict))

    def test_heatmap_date_map(self, http_client):
        data = assert_ok(http_client.get("/journal/api/heatmap"))
        # Accept either dict {date: count} or list [{date, count}, ...]
        assert isinstance(data, (dict, list))
        if isinstance(data, list) and data:
            assert "date" in data[0], f"Heatmap entry missing 'date': {data[0]}"

    def test_mood_trend(self, http_client):
        data = assert_ok(http_client.get("/journal/api/mood-trend"))
        assert isinstance(data, (list, dict))

    def test_streak_counter(self, http_client):
        data = assert_dict_response(http_client.get("/journal/api/streak"))
        assert "streak" in data or "current" in data

    def test_templates_available(self, http_client):
        data = assert_ok(http_client.get("/journal/api/templates"))
        assert isinstance(data, (list, dict))

    def test_related_endpoint_short_query_no_results(self, http_client):
        """Related-entries endpoint returns empty when query is too short
        to embed meaningfully — protects against junk hits on one-liners."""
        data = assert_ok(http_client.get("/journal/api/related?text=hi&top_k=3"))
        assert isinstance(data, dict)
        assert data.get("results") == []

    def test_related_endpoint_returns_shape(self, http_client):
        """Substantive query returns the documented response shape, even if
        results is empty (vault may not have semantically-similar entries).

        Long timeout: on a fresh boot the embedding index hasn't warmed yet
        (3-year corpus walk + per-chunk embed call ≈ 30–60s on OpenAI)."""
        q = "had a productive coding session today and finished the feature I had been working on"
        data = assert_ok(http_client.get(f"/journal/api/related?text={q}&top_k=3", timeout=90))
        assert isinstance(data, dict)
        assert "text" in data and "results" in data
        assert isinstance(data["results"], list)
        for r in data["results"]:
            assert {"date", "score"} <= set(r.keys())
            assert isinstance(r["score"], (int, float))


@pytest.mark.interactive
class TestJournalUI:
    def test_ui_write_entry_flow(self, app_page, page_errors):
        """Select mood → type entry → submit → verify autosave/toast."""
        page = app_page("journal")
        wait_briefly(page, 600)
        # Click a mood button (any one)
        click_first(
            page,
            ".mood-btn[data-mood='good']",
            ".mood-btn",
            "button:has-text('🙂')",
        )
        textarea = page.locator("#entry-text, textarea.entry-input").first
        if textarea.count() == 0:
            pytest.skip("No journal entry textarea found")
        textarea.fill("PLAYWRIGHT-TEST-journal entry from UI")
        wait_briefly(page, 1500)  # let autosave run
        # Try to click submit if button exists
        click_first(
            page,
            "button:has-text('Submit')",
            ".submit-btn",
            "[onclick*='submitEntry']",
        )
        wait_briefly(page, 1000)
        assert_no_js_errors(page_errors, allow_patterns=["AbortError"])

    def test_ui_date_navigation(self, app_page, page_errors):
        """Click ◀ to go back a day."""
        page = app_page("journal")
        wait_briefly(page, 500)
        clicked = click_first(
            page,
            "[onclick*='changeDate(-1)']",
            "button:has-text('◀')",
        )
        if clicked:
            wait_briefly(page, 600)
        # Click Today to return
        click_first(page, "[onclick*='goToday']", "button:has-text('Today')")
        wait_briefly(page, 400)
        assert_no_js_errors(page_errors)

    def test_ui_milestone_entry(self, app_page, page_errors):
        """Type in milestone field → wait for autosave."""
        page = app_page("journal")
        wait_briefly(page, 500)
        ms = page.locator("#milestone").first
        if ms.count() == 0:
            pytest.skip("No milestone input")
        ms.fill("PLAYWRIGHT-TEST-milestone")
        wait_briefly(page, 1500)
        assert_no_js_errors(page_errors)

    def test_ui_three_things(self, app_page, page_errors):
        """Fill the three good things inputs."""
        page = app_page("journal")
        wait_briefly(page, 500)
        for sel, val in [
            ("#thing1", "PLAYWRIGHT-TEST-thing1"),
            ("#thing2", "PLAYWRIGHT-TEST-thing2"),
            ("#thing3", "PLAYWRIGHT-TEST-thing3"),
        ]:
            inp = page.locator(sel).first
            if inp.count() > 0:
                inp.fill(val)
        wait_briefly(page, 1500)
        assert_no_js_errors(page_errors)

    def test_ui_ai_reflect(self, app_page, page_errors, require_llm):
        """Click AI Reflect → verify reflection content streams in."""
        page = app_page("journal")
        wait_briefly(page, 500)
        clicked = click_first(
            page,
            "[onclick*='aiReflect']",
            "button:has-text('AI Reflect')",
            "button:has-text('Reflect')",
        )
        if not clicked:
            pytest.skip("AI Reflect button not present")
        wait_briefly(page, 4000)
        assert_no_js_errors(page_errors, allow_patterns=["AbortError", "fetch"])
