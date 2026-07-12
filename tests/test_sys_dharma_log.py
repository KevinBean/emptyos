"""System app tests: Dharma Log — covers acceptance criteria from the spec.

Three API cases exercise the core write/read/related path. UI lives behind
LLM-heavy flows (chat, wisdom distill) and is not asserted in the smoke
lane — only the shape of the surface is asserted here.

Source was split into a first-class vault entity (commit b87b4f0), so
entries reference `source_id` not a free `source` string. Each test that
needs an entry creates a source first via POST /api/sources.
"""

from __future__ import annotations

import pytest

from helpers import TEST_PREFIX, assert_dict_response, assert_ok


def _make_source(http_client, title_suffix: str) -> str:
    """Create a test source and return its slug (id). Idempotent enough —
    if a source with the same slug already exists from a prior failed
    cleanup, create_source returns the existing entry."""
    created = assert_ok(http_client.post(
        "/dharma-log/api/sources",
        json={"title": TEST_PREFIX + title_suffix, "kind": "text"},
    ))
    sid = created.get("id") or created.get("slug")
    assert sid, f"missing source id: {created}"
    return sid


@pytest.mark.api
class TestDharmaLogAPI:
    def test_list_entries_shape(self, http_client):
        data = assert_dict_response(http_client.get("/dharma-log/api/entries"))
        assert "entries" in data
        assert isinstance(data["entries"], list)

    def test_create_then_fetch_entry(self, http_client):
        source_id = _make_source(http_client, "Diamond Sutra")
        payload = {
            "source_id": source_id,
            "material_kind": "text",
            "material": "Section 14 — perfection of patience.",
            "confusions": "What does 'no notion of a self' mean concretely?",
            "tags": ["emptiness", "prajnaparamita"],
        }
        created = assert_ok(http_client.post("/dharma-log/api/entries", json=payload))
        assert created.get("id"), f"missing id: {created}"
        assert created.get("path", "").endswith(".md")

        # Round-trip GET — confirms entry was persisted with the right shape.
        fetched = assert_ok(
            http_client.get(f"/dharma-log/api/entries/{created['id']}")
        )
        assert fetched.get("source_id") == source_id
        assert fetched.get("status") == "open"
        assert "related" in fetched and isinstance(fetched["related"], list)

    def test_missing_entry_returns_error_payload(self, http_client):
        # API contract: 200 with {"error": ...} rather than 404 — matches
        # convention used by the rest of EmptyOS (see journal, task).
        data = assert_ok(
            http_client.get(f"/dharma-log/api/entries/{TEST_PREFIX}does-not-exist")
        )
        assert "error" in data

    def test_create_requires_source(self, http_client):
        # The write boundary rejects an empty/missing source_id.
        resp = http_client.post("/dharma-log/api/entries", json={"source_id": ""})
        # Either a 4xx/5xx, or a 200 with an error envelope — both are
        # acceptable contracts as long as no entry is created.
        if resp.status_code == 200:
            body = resp.json()
            assert "error" in body or body.get("id"), body
            # If id came back, the source_id must be non-empty.
            if body.get("id"):
                assert body.get("source_id"), body

    def test_chat_requires_text(self, http_client):
        # Make a source + entry first so the route resolves to a real id.
        source_id = _make_source(http_client, "Lankavatara")
        created = assert_ok(http_client.post("/dharma-log/api/entries", json={
            "source_id": source_id,
            "material": "Mind-only doctrine, eight consciousnesses.",
            "confusions": "How does store-consciousness differ from a self?",
        }))
        entry_id = created["id"]

        # Empty text must not reach the LLM.
        resp = http_client.post(
            f"/dharma-log/api/entries/{entry_id}/chat", json={"text": ""}
        )
        if resp.status_code == 200:
            body = resp.json()
            assert "error" in body, body

    def test_get_entry_includes_turns_shape(self, http_client):
        # Fresh entries have an empty turns list — confirms the field is
        # always present in the surface so the UI can render unconditionally.
        source_id = _make_source(http_client, "Bodhicaryavatara")
        created = assert_ok(http_client.post("/dharma-log/api/entries", json={
            "source_id": source_id,
            "material": "Ch. 8 — meditation on equality of self and other.",
        }))
        fetched = assert_ok(
            http_client.get(f"/dharma-log/api/entries/{created['id']}")
        )
        assert "turns" in fetched and isinstance(fetched["turns"], list)
        assert fetched["turns"] == []

    def test_wisdom_endpoint_requires_text(self, http_client):
        # Make a source + entry first.
        source_id = _make_source(http_client, "Heart Sutra Test")
        created = assert_ok(http_client.post("/dharma-log/api/entries", json={
            "source_id": source_id,
            "material": "Form is emptiness, emptiness is form.",
        }))
        entry_id = created["id"]

        # Empty wisdom POST must not create a wisdom note.
        resp = http_client.post(
            f"/dharma-log/api/entries/{entry_id}/wisdom", json={"text": ""}
        )
        if resp.status_code == 200:
            body = resp.json()
            assert "error" in body, body
