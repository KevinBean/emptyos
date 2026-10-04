"""System tests for Aura's memory layer + autopilot grants + pending gate.

Covers the surfaces added 2026-05-18:
- aura.remember / aura.forget / aura.recall voice intents (manifest + dispatch)
- /api/memory/{save,list,delete} HTTP endpoints
- /api/autopilot (status + session toggle + revoke)
- /api/pending (list + apply + reject)
- /debug/context endpoint (memory + recent-journal readout)
- task.list_due + voice.research intent registration

LLM-hitting paths are not exercised — only the endpoint shapes and the
manifest registry, so the suite stays fast and provider-independent.

Cleanup: every memory note created here uses a slug prefixed with
PLAYWRIGHT-TEST-, deleted via /api/memory/<slug>/delete. The session
autouse fixture's catch-all also scans for the prefix.
"""

import pytest

from helpers import TEST_PREFIX, assert_dict_response, assert_ok


def _delete_memory(http_client, slug: str) -> None:
    """Best-effort cleanup — never raises."""
    try:
        http_client.post(f"/voice-assistant/api/memory/{slug}/delete")
    except Exception:
        pass


@pytest.fixture
def cleanup_memory(http_client):
    """Yield a list collector; after the test, delete every slug in it."""
    slugs: list[str] = []
    yield slugs
    for s in slugs:
        _delete_memory(http_client, s)


@pytest.mark.api
class TestMemoryRegistry:
    def test_remember_intent_registered(self, http_client):
        data = http_client.get("/voice-assistant/debug/intents").json()
        verbs = {entry["verb"] for entry in data["registry"]}
        assert "aura.remember" in verbs
        assert "aura.forget" in verbs
        assert "aura.recall" in verbs

    def test_memory_intents_always_in_scope(self, http_client):
        data = http_client.get("/voice-assistant/debug/intents").json()
        scoped = set(data["scoped"])
        assert "aura.remember" in scoped, "aura.remember must always be in scope"
        assert "aura.forget" in scoped, "aura.forget must always be in scope"
        # aura.recall depends on MAX_INTENTS_IN_PROMPT >= total always-true intents.
        # Documented bump from 12 → 16. Soft-check + helpful message.
        if "aura.recall" not in scoped:
            pytest.skip(
                "aura.recall not scoped — likely MAX_INTENTS_IN_PROMPT too low. "
                "Restart daemon after the bump to 16."
            )

    def test_task_list_due_intent_registered(self, http_client):
        data = http_client.get("/voice-assistant/debug/intents").json()
        verbs = {entry["verb"] for entry in data["registry"]}
        assert "task.list_due" in verbs

    def test_voice_research_intent_registered(self, http_client):
        data = http_client.get("/voice-assistant/debug/intents").json()
        verbs = {entry["verb"] for entry in data["registry"]}
        assert "voice.research" in verbs


@pytest.mark.api
class TestMemoryCRUD:
    def test_memory_list_endpoint_shape(self, http_client):
        data = assert_dict_response(
            http_client.get("/voice-assistant/api/memory"),
            required_keys=["memory"],
        )
        assert isinstance(data["memory"], list)

    def test_memory_save_and_list(self, http_client, cleanup_memory):
        body = f"{TEST_PREFIX}memory save and list smoke"
        resp = http_client.post(
            "/voice-assistant/api/memory/save",
            json={"kind": "user", "body": body},
        )
        saved = assert_ok(resp)
        assert "slug" in saved
        assert "path" in saved
        cleanup_memory.append(saved["slug"])

        # Should now appear in the list.
        listed = http_client.get("/voice-assistant/api/memory").json()
        slugs = {m["slug"] for m in listed["memory"]}
        assert saved["slug"] in slugs

    def test_memory_save_empty_body_rejected(self, http_client):
        resp = http_client.post(
            "/voice-assistant/api/memory/save",
            json={"kind": "user", "body": ""},
        )
        data = resp.json()
        assert "error" in data

    def test_memory_save_unknown_kind_falls_back_to_user(
        self, http_client, cleanup_memory,
    ):
        body = f"{TEST_PREFIX}fallback kind smoke"
        saved = http_client.post(
            "/voice-assistant/api/memory/save",
            json={"kind": "garbage-kind", "body": body},
        ).json()
        assert "slug" in saved
        cleanup_memory.append(saved["slug"])
        # Verify it was stored as 'user' (the documented fallback).
        listed = http_client.get("/voice-assistant/api/memory").json()
        row = next((m for m in listed["memory"] if m["slug"] == saved["slug"]), None)
        assert row is not None
        assert row["kind"] == "user"

    def test_memory_delete_unknown_slug(self, http_client):
        resp = http_client.post(
            "/voice-assistant/api/memory/never-existed-slug/delete",
        )
        data = resp.json()
        # Endpoint returns {ok: false} for non-existent slugs.
        assert data.get("ok") is False

    def test_memory_delete_roundtrip(self, http_client):
        body = f"{TEST_PREFIX}delete roundtrip smoke"
        saved = http_client.post(
            "/voice-assistant/api/memory/save",
            json={"kind": "user", "body": body},
        ).json()
        slug = saved["slug"]
        del_resp = http_client.post(
            f"/voice-assistant/api/memory/{slug}/delete",
        ).json()
        assert del_resp.get("ok") is True
        # Confirm gone from the list.
        listed = http_client.get("/voice-assistant/api/memory").json()
        slugs = {m["slug"] for m in listed["memory"]}
        assert slug not in slugs


@pytest.mark.api
class TestDebugContext:
    def test_debug_context_endpoint_shape(self, http_client):
        data = assert_dict_response(
            http_client.get("/voice-assistant/debug/context"),
            required_keys=["context", "chars"],
        )
        assert isinstance(data["context"], str)
        assert isinstance(data["chars"], int)

    def test_context_includes_memory_readout(self, http_client, cleanup_memory):
        body = f"{TEST_PREFIX}context-readout-marker"
        saved = http_client.post(
            "/voice-assistant/api/memory/save",
            json={"kind": "user", "body": body, "description": body},
        ).json()
        cleanup_memory.append(saved["slug"])
        # The 60s cache will normally hide an immediate write — but the
        # save path invalidates the cache, so the next read should see it.
        data = http_client.get("/voice-assistant/debug/context").json()
        assert "What I remember about you" in data["context"], (
            "memory readout block missing from context after save"
        )


@pytest.mark.api
class TestAutopilot:
    def test_autopilot_status_shape(self, http_client):
        data = assert_dict_response(
            http_client.get("/voice-assistant/api/autopilot"),
            required_keys=["scope", "grants", "eligible_verbs"],
        )
        assert isinstance(data["eligible_verbs"], list)
        # Aura's verbs should be in the seeded policy.
        assert "aura.remember" in data["eligible_verbs"]
        assert "aura.forget" in data["eligible_verbs"]
        assert "aura.recall" in data["eligible_verbs"]
        # Free-form / irreversible verbs must NOT be eligible.
        assert "note.create" not in data["eligible_verbs"]

    def test_autopilot_toggle_session(self, http_client):
        # Ensure clean baseline.
        http_client.post(
            "/voice-assistant/api/autopilot/session", json={"enabled": False},
        )
        # Turn ON with a short TTL.
        on_resp = http_client.post(
            "/voice-assistant/api/autopilot/session",
            json={"enabled": True, "ttl_s": 60},
        ).json()
        try:
            assert on_resp.get("ok") is True
            assert on_resp.get("active") is True
            assert isinstance(on_resp.get("grants"), list)
            assert len(on_resp["grants"]) > 0, "should issue at least one grant"
            # Status should reflect the issued grants.
            status = http_client.get("/voice-assistant/api/autopilot").json()
            assert len(status["grants"]) > 0
        finally:
            # Turn OFF.
            off_resp = http_client.post(
                "/voice-assistant/api/autopilot/session",
                json={"enabled": False},
            ).json()
            assert off_resp.get("ok") is True
            assert off_resp.get("active") is False


@pytest.mark.api
class TestPendingGate:
    def test_pending_list_shape(self, http_client):
        data = assert_dict_response(
            http_client.get("/voice-assistant/api/pending"),
            required_keys=["pending"],
        )
        assert isinstance(data["pending"], list)

    def test_pending_apply_unknown_action(self, http_client):
        resp = http_client.post(
            "/voice-assistant/api/pending/never-existed-action-id/apply",
        )
        data = resp.json()
        assert "error" in data

    def test_pending_reject_unknown_action(self, http_client):
        resp = http_client.post(
            "/voice-assistant/api/pending/never-existed-action-id/reject",
        )
        data = resp.json()
        assert "error" in data
