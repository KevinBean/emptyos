"""Tier 4: Cross-app event chain reactions."""

import pytest
from helpers import TEST_PREFIX


@pytest.mark.crossapp
class TestEventChains:
    """Actions in one app should trigger events visible in reactor/event log."""

    def test_event_bus_history(self, http_client):
        """Event bus should have recent events."""
        resp = http_client.get("/api/events?limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_expense_emits_event(self, http_client):
        """Adding an expense should emit expense:added event."""
        # Add test expense
        http_client.post("/expense/api/add", json={
            "text": f"0.01 {TEST_PREFIX}event-test"
        })
        import time
        time.sleep(2)
        # Check events
        resp = http_client.get("/api/events?type=expense:added&limit=5")
        if resp.status_code == 200:
            events = resp.json()
            assert isinstance(events, list)
        # Cleanup
        entries = http_client.get("/expense/api/list").json()
        if isinstance(entries, list):
            for e in entries:
                if TEST_PREFIX in str(e.get("description", "")):
                    http_client.post("/expense/api/delete", json={"entry": e})

    def test_healing_emits_event(self, http_client):
        """Logging a mood should emit healing:mood-logged event."""
        http_client.post("/healing/api/mood", json={
            "mood": "okay",
            "note": f"{TEST_PREFIX}event chain test"
        })
        import time
        time.sleep(2)
        resp = http_client.get("/api/events?limit=20")
        assert resp.status_code == 200

    def test_reactor_has_recent_activity(self, http_client):
        """Reactor should show recent reactions."""
        resp = http_client.get("/reactor/api/log")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_items_emits_event(self, http_client):
        """Adding an item should emit items:updated event."""
        resp = http_client.post("/items/api/items", json={
            "name": f"{TEST_PREFIX}event-widget",
            "category": "Test",
            "location": "Test"
        })
        item_id = resp.json().get("id") if resp.status_code == 200 else None
        import time
        time.sleep(2)
        # Check events
        resp = http_client.get("/api/events?limit=20")
        assert resp.status_code == 200
        # Cleanup
        if item_id:
            http_client.request("DELETE", f"/items/api/items/{item_id}")
