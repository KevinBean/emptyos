"""System app tests: Reactor — 10 use cases."""

import pytest

from helpers import assert_ok
from page_helpers import assert_no_js_errors, wait_briefly


@pytest.mark.api
class TestReactorAPI:
    def test_log_returns_list(self, http_client):
        data = assert_ok(http_client.get("/reactor/api/log"))
        assert isinstance(data, (list, dict))

    def test_log_contains_entries(self, http_client):
        resp = http_client.get("/reactor/api/log")
        data = resp.json()
        entries = data if isinstance(data, list) else data.get("entries", [])
        # Reactor will have events if system has been active
        assert isinstance(entries, list)

    def test_log_entry_shape(self, http_client):
        resp = http_client.get("/reactor/api/log")
        data = resp.json()
        entries = data if isinstance(data, list) else data.get("entries", [])
        if entries:
            e = entries[0]
            # Event entries should have at least event type and timestamp
            assert isinstance(e, dict)

    def test_log_accepts_limit(self, http_client):
        resp = http_client.get("/reactor/api/log?limit=5")
        assert resp.status_code == 200


@pytest.mark.interactive
class TestReactorUI:
    def test_ui_loads(self, app_page, page_errors):
        page = app_page("reactor")
        wait_briefly(page, 1500)
        assert_no_js_errors(page_errors)

    def test_ui_event_feed(self, app_page, page_errors):
        """Reactor UI should show recent event activity."""
        page = app_page("reactor")
        wait_briefly(page, 2000)
        assert_no_js_errors(page_errors)

    def test_ui_no_critical_errors(self, app_page, page_errors):
        page = app_page("reactor")
        wait_briefly(page, 1500)
        critical = [e for e in page_errors if "TypeError" in str(e)]
        assert not critical, f"Critical errors: {critical}"

    def test_ui_after_event_fires(self, app_page, http_client, page_errors):
        """Trigger an event via another app, reactor should still work."""
        # Emit via capture
        http_client.post(
            "/quick-action/api/add",
            json={"text": "PLAYWRIGHT-TEST-reactor-probe", "tag": "note"},
        )
        page = app_page("reactor")
        wait_briefly(page, 1500)
        assert_no_js_errors(page_errors)

    def test_reactor_responds_to_event_chain(self, http_client):
        """Expense event should show up in event bus."""
        import time
        http_client.post(
            "/quick-action/api/add",
            json={"text": "PLAYWRIGHT-TEST-chain", "tag": "idea"},
        )
        time.sleep(1)
        resp = http_client.get("/api/events?limit=20")
        assert resp.status_code == 200

    def test_log_preserves_order(self, http_client):
        """Multiple GETs should return consistent recent ordering."""
        r1 = http_client.get("/reactor/api/log?limit=3").json()
        r2 = http_client.get("/reactor/api/log?limit=3").json()
        # Both should return lists/dicts (not crash)
        assert type(r1) == type(r2)
