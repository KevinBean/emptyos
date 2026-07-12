"""Outbound MCP foundry — daemon-side endpoint tests (requires :9000).

Validates the A1 /api/vault/query endpoint, the A3 verb-aggregation endpoint,
and the A4b grant panel API. The aggregation + status tests assert the
post-restart manifest state (task.add opted in), so they need a daemon that
has loaded the new [provides.mcp_foundry] manifests.
"""

import pytest

from helpers import assert_ok


@pytest.mark.api
class TestVaultQuery:
    def test_shape_never_leaks_bodies(self, http_client):
        data = assert_ok(http_client.get("/api/vault/query?tags=kb&limit=5"))
        assert "notes" in data and "count" in data
        for n in data["notes"]:
            assert "frontmatter" in n
            # FRONTMATTER ONLY — bodies must never appear (CLAUDE.md rule 19).
            assert "content" not in n and "body" not in n

    def test_limit_clamped(self, http_client):
        data = assert_ok(http_client.get("/api/vault/query?tags=kb&limit=9999"))
        assert data["count"] <= 500


@pytest.mark.api
class TestFoundryVerbs:
    def test_aggregation_shape(self, http_client):
        data = assert_ok(http_client.get("/agent/api/mcp/foundry/verbs"))
        assert isinstance(data.get("verbs"), list)

    def test_task_add_opted_in_and_non_eligible_excluded(self, http_client):
        verbs = assert_ok(http_client.get("/agent/api/mcp/foundry/verbs"))["verbs"]
        # task.add is declared in task/manifest.toml [provides.mcp_foundry] and
        # is eligible — it must be aggregated.
        assert "task.add" in verbs
        # non-eligible verbs are never aggregated regardless of any manifest.
        assert "rooms.write_note" not in verbs


@pytest.mark.api
class TestKbEndpointContract:
    """Pin the /kb/api shapes the foundry's KB resources + prompts consume
    (mcp_outbound_server._list_prompts / _read_resource)."""

    def test_pattern_list_shape(self, http_client):
        data = assert_ok(http_client.get("/kb/api/notes?kind=pattern"))
        assert "notes" in data and "count" in data
        for n in data["notes"]:
            assert "slug" in n and n.get("kind") == "pattern"


@pytest.mark.api
class TestFoundryPanelAPI:
    def test_status_shape(self, http_client):
        data = assert_ok(http_client.get("/agent/api/foundry"))
        for k in ("flag_on", "advertised_verbs", "grants", "flag_key"):
            assert k in data

    def test_grant_round_trip(self, http_client):
        actor = "PLAYWRIGHT-TEST-codex"
        body = assert_ok(http_client.post(
            "/agent/api/foundry/grant",
            json={"actor_id": actor, "verb_pattern": "task.add", "ttl_s": 120},
        ))
        assert body.get("ok"), body
        gid = body["grant"]["id"]
        try:
            st = assert_ok(http_client.get("/agent/api/foundry"))
            assert any(g.get("id") == gid for g in st["grants"])
        finally:
            rr = assert_ok(http_client.post(
                "/agent/api/foundry/revoke", json={"grant_id": gid}))
            assert rr.get("ok")

    def test_non_eligible_grant_refused(self, http_client):
        # Endpoint returns 200 with ok:false (refusal rides in the body).
        body = assert_ok(http_client.post(
            "/agent/api/foundry/grant",
            json={"actor_id": "PLAYWRIGHT-TEST-codex", "verb_pattern": "rooms.write_note"},
        ))
        assert body.get("ok") is False
