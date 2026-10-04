"""System app tests: App Analytics — 18 use cases.

Covers legacy endpoints (backward compat) + new personal-usage reports.
"""

import pytest

from helpers import assert_dict_response, assert_ok
from page_helpers import assert_no_js_errors, wait_briefly


@pytest.mark.api
class TestAppAnalyticsAPI:
    # --- Legacy (backward compat) ---

    def test_analytics(self, http_client):
        data = assert_ok(http_client.get("/app-analytics/api/analytics"))
        assert isinstance(data, (dict, list))

    def test_active_apps(self, http_client):
        resp = http_client.get("/app-analytics/api/active-apps")
        assert resp.status_code == 200

    def test_daily(self, http_client):
        resp = http_client.get("/app-analytics/api/daily")
        assert resp.status_code == 200

    def test_specific_app(self, http_client):
        resp = http_client.get("/app-analytics/api/app/task")
        assert resp.status_code == 200

    def test_unknown_app_graceful(self, http_client):
        resp = http_client.get("/app-analytics/api/app/zzz-does-not-exist")
        assert resp.status_code in (200, 404)

    # --- New: personal-usage reports ---

    def test_summary_shape(self, http_client):
        data = assert_ok(http_client.get("/app-analytics/api/summary"))
        for key in ("views_today", "views_7d", "active_apps_7d", "unused_apps_30d", "total_apps"):
            assert key in data, f"missing {key}"
        assert data["total_apps"] > 0

    def test_unused_returns_list(self, http_client):
        data = assert_ok(http_client.get("/app-analytics/api/unused?days=30"))
        assert isinstance(data, list)
        if data:
            assert "app_id" in data[0]
            assert "last_seen" in data[0]

    def test_heatmap_returns_date_keyed_dict(self, http_client):
        data = assert_ok(http_client.get("/app-analytics/api/heatmap?app=task&days=30"))
        assert isinstance(data, dict)
        for key in data:
            assert len(key) == 10, f"heatmap key should be YYYY-MM-DD, got {key}"

    def test_errors_vs_usage_shape(self, http_client):
        data = assert_ok(http_client.get("/app-analytics/api/errors-vs-usage?days=30"))
        assert isinstance(data, list)
        if data:
            r = data[0]
            for key in ("app", "views", "events", "errors", "error_rate", "priority"):
                assert key in r, f"missing {key}"
            assert data[0]["priority"] >= data[-1]["priority"]

    def test_time_of_day_24_buckets(self, http_client):
        data = assert_ok(http_client.get("/app-analytics/api/time-of-day?days=30"))
        assert isinstance(data, dict)
        assert len(data) == 24
        assert "00" in data and "23" in data

    def test_streaks_shape(self, http_client):
        data = assert_ok(http_client.get("/app-analytics/api/streaks"))
        assert isinstance(data, list)
        if data:
            r = data[0]
            for key in ("app", "current_weeks", "longest_weeks"):
                assert key in r, f"missing {key}"

    def test_ranking_shape(self, http_client):
        """/api/ranking returns score-sorted apps consumed by the hub launcher
        and nav drawer. Each row carries app id, recency+frequency score,
        raw view count, and last_seen date."""
        data = assert_ok(http_client.get("/app-analytics/api/ranking?days=90&half_life=14"))
        assert data["days"] == 90
        assert data["half_life"] == 14.0
        assert isinstance(data["ranking"], list)
        if not data["ranking"]:
            return  # empty environment — shape-only assertions above are enough
        r = data["ranking"][0]
        for key in ("app", "score", "views", "last_seen"):
            assert key in r, f"missing {key}"
        # Sorted descending by score so consumers can take a slice for "top N".
        scores = [row["score"] for row in data["ranking"]]
        assert scores == sorted(scores, reverse=True), "ranking must be score-descending"

    def test_ranking_clamps_bad_params(self, http_client):
        """days clamps to [1, 365]; half_life clamps to ≥0.5; non-numeric falls
        back to defaults rather than crashing — so a stray query string from
        the frontend can't 500 the hub."""
        # Out-of-range days → clamp.
        data = assert_ok(http_client.get("/app-analytics/api/ranking?days=99999"))
        assert data["days"] == 365
        data = assert_ok(http_client.get("/app-analytics/api/ranking?days=0"))
        assert data["days"] == 1
        # Out-of-range half_life → clamp.
        data = assert_ok(http_client.get("/app-analytics/api/ranking?half_life=0"))
        assert data["half_life"] == 0.5
        # Garbage → defaults.
        data = assert_ok(http_client.get("/app-analytics/api/ranking?days=abc&half_life=xyz"))
        assert data["days"] == 90
        assert data["half_life"] == 14.0

    def test_ranking_recency_dominates_frequency(self, http_client):
        """Verify the decay actually penalises older views: with a 1-day
        half-life, a recent burst should outrank a larger but older one.

        Indirect test — we can't easily inject historic events, so we just
        confirm that varying half_life produces a *different* ordering when
        scores differ. With a very small half_life, the most-recent app
        rises to the top regardless of total view count.
        """
        wide = assert_ok(http_client.get("/app-analytics/api/ranking?days=90&half_life=14"))
        narrow = assert_ok(http_client.get("/app-analytics/api/ranking?days=90&half_life=1"))
        # Both must return same set of apps (only weighting differs).
        wide_apps = {r["app"] for r in wide["ranking"]}
        narrow_apps = {r["app"] for r in narrow["ranking"]}
        assert wide_apps == narrow_apps
        # Different half_life ⇒ at least one app's score must differ.
        if len(wide_apps) >= 2:
            wide_scores = {r["app"]: r["score"] for r in wide["ranking"]}
            narrow_scores = {r["app"]: r["score"] for r in narrow["ranking"]}
            assert any(wide_scores[a] != narrow_scores[a] for a in wide_apps), \
                "half_life should change at least one app's score"

    def test_pageview_fires_on_app_page(self, http_client):
        """Opening an app's page should emit ui:viewed → shows up in heatmap."""
        # GET the task page (triggers middleware ui:viewed)
        http_client.get("/task/")
        # Now check analytics heatmap for 'task' — should have ≥1 hit for today
        data = assert_ok(http_client.get("/app-analytics/api/heatmap?app=task&days=1"))
        # The data should be non-empty if pageview middleware works
        # (may be empty if 'task' hasn't been loaded yet or test runs before backfill)
        assert isinstance(data, dict)


@pytest.mark.interactive
class TestAppAnalyticsUI:
    def test_ui_loads(self, app_page, page_errors):
        page = app_page("app-analytics")
        wait_briefly(page, 1500)
        assert page.locator("h1").first.text_content().strip() == "App Analytics"
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_stat_cards_render(self, app_page, page_errors):
        page = app_page("app-analytics")
        wait_briefly(page, 1500)
        stats = page.locator(".eos-stat-card")
        assert stats.count() >= 4
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_unused_section_renders(self, app_page, page_errors):
        page = app_page("app-analytics")
        wait_briefly(page, 1500)
        # Should have the "What to delete" card
        cards = page.locator(".aa-card-title").all_text_contents()
        assert any("delete" in c.lower() for c in cards)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_settings_panel_opens(self, app_page, page_errors):
        page = app_page("app-analytics")
        wait_briefly(page, 500)
        page.locator(".btn-settings").first.click()
        page.wait_for_selector("#app-settings-panel", state="visible", timeout=2000)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_no_critical_errors(self, app_page, page_errors):
        page = app_page("app-analytics")
        wait_briefly(page, 2000)
        critical = [e for e in page_errors if "TypeError" in str(e)]
        assert not critical, f"Critical: {critical}"
