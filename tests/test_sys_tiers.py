"""System tests for the Tiers app.

Tests are careful not to leave ``release.toml`` mutated: the autouse fixture
snapshots the file before each class runs and restores it afterwards. Toggle
tests use obviously-fake names (``TEST_PREFIX + ...``) so even a partial
restore failure won't pollute the real tier lists.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from helpers import TEST_PREFIX, assert_ok

REPO_ROOT = Path(__file__).resolve().parent.parent
RELEASE_TOML = REPO_ROOT / "release.toml"
FAKE_APP = f"{TEST_PREFIX}fakeapp"
FAKE_PLUGIN = f"{TEST_PREFIX}fakeplugin"


@pytest.fixture
def restore_release_toml():
    """Snapshot release.toml around each test that may mutate it."""
    original = RELEASE_TOML.read_bytes()
    try:
        yield
    finally:
        RELEASE_TOML.write_bytes(original)


@pytest.mark.api
class TestTiersAPI:
    def test_state_returns_tiers(self, http_client):
        data = assert_ok(http_client.get("/tiers/api/state"))
        assert "tiers" in data
        assert isinstance(data["tiers"], list)
        # release.toml ships with at least core + standard
        assert "core" in data["tiers"]
        assert "standard" in data["tiers"]

    def test_state_resolves_extends(self, http_client):
        data = assert_ok(http_client.get("/tiers/api/state"))
        # standard extends core, so core's apps must be in standard's effective set
        core_direct = set(data["direct_apps"]["core"])
        standard_effective = set(data["effective_apps"]["standard"])
        assert core_direct.issubset(standard_effective)

    def test_state_lists_apps_present(self, http_client):
        data = assert_ok(http_client.get("/tiers/api/state"))
        # tiers app itself must be discoverable since the test is hitting it
        assert "tiers" in data["apps_present"]

    def test_state_extends_chain_exposed(self, http_client):
        data = assert_ok(http_client.get("/tiers/api/state"))
        # demo extends core; standard extends core; dev extends standard
        assert data["extends_of"].get("standard") == "core"
        assert data["extends_of"].get("dev") == "standard"

    def test_git_status_shape(self, http_client):
        data = assert_ok(http_client.get("/tiers/api/git-status"))
        assert "dirty" in data
        assert isinstance(data["dirty"], bool)
        assert "head" in data

    def test_toggle_rejects_bad_kind(self, http_client):
        resp = http_client.post(
            "/tiers/api/toggle",
            json={"kind": "garbage", "tier": "dev", "name": FAKE_APP},
        )
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_toggle_rejects_unknown_tier(self, http_client):
        resp = http_client.post(
            "/tiers/api/toggle",
            json={"kind": "apps", "tier": "no-such-tier", "name": FAKE_APP},
        )
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_toggle_rejects_missing_fields(self, http_client):
        resp = http_client.post("/tiers/api/toggle", json={"kind": "apps"})
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_toggle_add_then_remove(self, http_client, restore_release_toml):
        # Add a fake app to dev tier, then remove it. Round-trip leaves
        # release.toml unchanged regardless of starting state.
        add = http_client.post(
            "/tiers/api/toggle",
            json={"kind": "apps", "tier": "dev", "name": FAKE_APP, "action": "add"},
        ).json()
        assert add.get("ok") is True
        assert add["state"] == "added"

        # Verify it shows up in state
        state = assert_ok(http_client.get("/tiers/api/state"))
        assert FAKE_APP in state["direct_apps"]["dev"]

        rm = http_client.post(
            "/tiers/api/toggle",
            json={"kind": "apps", "tier": "dev", "name": FAKE_APP, "action": "remove"},
        ).json()
        assert rm["state"] == "removed"

        # Final state: gone
        state2 = assert_ok(http_client.get("/tiers/api/state"))
        assert FAKE_APP not in state2["direct_apps"]["dev"]

    def test_toggle_plugins_kind(self, http_client, restore_release_toml):
        add = http_client.post(
            "/tiers/api/toggle",
            json={"kind": "plugins", "tier": "dev", "name": FAKE_PLUGIN, "action": "add"},
        ).json()
        assert add.get("ok") is True

        state = assert_ok(http_client.get("/tiers/api/state"))
        assert FAKE_PLUGIN in state["direct_plugins"]["dev"]

    def test_toggle_default_action_is_toggle(self, http_client, restore_release_toml):
        # No `action` supplied → toggle. First call adds, second call removes.
        first = http_client.post(
            "/tiers/api/toggle",
            json={"kind": "apps", "tier": "dev", "name": FAKE_APP},
        ).json()
        assert first["state"] == "added"

        second = http_client.post(
            "/tiers/api/toggle",
            json={"kind": "apps", "tier": "dev", "name": FAKE_APP},
        ).json()
        assert second["state"] == "removed"

    def test_release_toml_preserves_comments(self, http_client, restore_release_toml):
        # tomlkit must preserve the leading comment header through round-trip.
        before = RELEASE_TOML.read_text(encoding="utf-8")
        assert before.startswith("# EmptyOS Release Manifest")

        http_client.post(
            "/tiers/api/toggle",
            json={"kind": "apps", "tier": "dev", "name": FAKE_APP, "action": "add"},
        )
        after = RELEASE_TOML.read_text(encoding="utf-8")
        assert after.startswith("# EmptyOS Release Manifest")
        # And the platforms section's prose comment must survive too
        assert "Named deployment profiles" in after


@pytest.mark.api
class TestTierResolverPure:
    """Direct tests of the pure-data resolver. No daemon required."""

    def test_resolve_tier_handles_cycles(self):
        from emptyos.sdk.release_tiers import resolve_tier

        cyclic = {
            "a": {"apps": ["one"], "extends": "b"},
            "b": {"apps": ["two"], "extends": "a"},
        }
        # Must not infinite-loop
        result = resolve_tier(cyclic, "a", "apps")
        assert result == {"one", "two"}

    def test_resolve_tier_unknown_name(self):
        from emptyos.sdk.release_tiers import resolve_tier

        assert resolve_tier({"a": {"apps": ["one"]}}, "missing", "apps") == set()

    def test_tier_union_and_reverse_index(self):
        from emptyos.sdk.release_tiers import reverse_index, tier_union

        tiers = {
            "core": {"apps": ["a", "b"]},
            "ext": {"apps": ["c"], "extends": "core"},
        }
        assert tier_union(tiers, ["core", "ext"], "apps") == {"a", "b", "c"}
        idx = reverse_index(tiers, "apps")
        assert idx["a"] == {"core", "ext"}  # a is in core, ext inherits
        assert idx["c"] == {"ext"}
