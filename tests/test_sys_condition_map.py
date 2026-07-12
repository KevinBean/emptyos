"""System app tests: Condition Map API."""

from __future__ import annotations

import pytest

from helpers import TEST_PREFIX, assert_dict_response, assert_ok


@pytest.mark.api
class TestConditionMapAPI:
    def test_maps_list_shape(self, http_client):
        data = assert_dict_response(http_client.get("/condition-map/api/maps"))
        assert "maps" in data
        assert isinstance(data["maps"], list)

    def test_analyze_requires_subject(self, http_client):
        resp = http_client.post("/condition-map/api/maps/analyze", json={"context": "only context"})
        if resp.status_code == 200:
            body = resp.json()
            assert "error" in body, body

    def test_illustrate_status_shape(self, http_client):
        data = assert_dict_response(http_client.get("/condition-map/api/illustrate/status"))
        assert isinstance(data.get("enabled"), bool)

    def test_illustrate_gating_and_validation(self, http_client):
        """Flag-off → refuses with enabled:false; flag-on → validates input.
        Environment-agnostic: branches on the live flag state. Never hits the LLM
        (no analysis/map_id supplied)."""
        enabled = http_client.get("/condition-map/api/illustrate/status").json().get("enabled")
        resp = http_client.post("/condition-map/api/maps/illustrate", json={})
        body = resp.json()
        assert body.get("ok") is False, body
        if not enabled:
            assert body.get("enabled") is False
            assert "disabled" in (body.get("error") or "").lower()
        else:
            # flag on but no analysis/map_id → input-validation error, not a crash
            assert body.get("enabled") is True
            assert "required" in (body.get("error") or "").lower()

    def test_save_then_fetch_map(self, http_client):
        subject = TEST_PREFIX + "work conflict conditions"
        analysis = {
            "subject": subject,
            "summary": "The conflict is sustained by several conditions rather than one fixed trait.",
            "conditions": [
                {
                    "name": "deadline pressure",
                    "type": "systemic",
                    "role": "maintains",
                    "description": "Time pressure narrows the room for generous interpretation.",
                    "changeable": "partial",
                }
            ],
            "dependencies": [
                {"from": "deadline pressure", "to": subject, "relation": "keeps the interaction reactive"}
            ],
            "fixed_story_to_loosen": ["This person is always the problem."],
            "levers": [
                {
                    "action": "ask for the constraint explicitly",
                    "condition": "deadline pressure",
                    "why": "It tests whether urgency is driving the tone.",
                }
            ],
            "unknowns": ["What the other person is responding to."],
            "reflection": "Which condition can I verify before I solidify the story?",
        }
        created = assert_ok(
            http_client.post(
                "/condition-map/api/maps",
                json={
                    "subject": subject,
                    "context": "A terse exchange about project scope.",
                    "analysis": analysis,
                    "user_note": "Keep the map tentative.",
                    "source_entry": "test-entry",
                    "provenance": {"mode": "local", "provider": "test", "model": "fixture"},
                },
            )
        )
        assert created.get("id"), created
        assert created.get("path", "").endswith(".md")
        assert created.get("source_entry") == "test-entry"

        fetched = assert_ok(http_client.get(f"/condition-map/api/maps/{created['id']}"))
        assert fetched.get("subject") == subject
        assert fetched.get("source_entry") == "test-entry"
        assert "deadline pressure" in fetched.get("body", "")
        assert "Keep the map tentative" in fetched.get("body", "")
        assert "Graph Data" not in fetched.get("body", "")
        graph = fetched.get("graph") or {}
        assert graph.get("center") == "subject"
        assert any(n.get("label") == "deadline pressure" for n in graph.get("nodes", []))
        assert any(e.get("to") == "subject" for e in graph.get("edges", []))
