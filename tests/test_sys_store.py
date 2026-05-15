"""System tests for the store app + emptyos/runtime/store_state.

Two layers:
- API tests against the live daemon (`http_client` fixture) — restart-required
  semantics, catalog shape, install/uninstall flows with dep + dependent
  surfacing, essential lock.
- Unit tests on `emptyos.runtime.store_state` — pure file IO, no daemon.

The API tests are careful to clean up after themselves so the local
installed-apps.json doesn't get a permanent test artifact in it. They use
`model-bench` as the install/uninstall target since it has no dependents in
a typical workspace; if that changes, swap to another leaf-node app.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from helpers import assert_dict_response, assert_ok


TARGET_APP = "model-bench"   # leaf app, low dependent risk
TARGET_PLUGIN = "telegram"   # no other plugin depends on it


# ──────────────────────────────────────────────────────────────────────────
# Unit tests — emptyos.runtime.store_state
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_data_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


def test_load_returns_empty_shape_when_missing(tmp_data_dir):
    from emptyos.runtime import store_state
    state = store_state.load(tmp_data_dir, "apps")
    assert state["installed"] == {}
    assert state["disabled"] == []
    assert state["last_change"] is None
    # schema_version is forward-bumped; assertion is "at least 2" so this test
    # survives future bumps without churn.
    assert state["schema_version"] >= 2


def test_seed_if_missing_creates_file(tmp_data_dir):
    from emptyos.runtime import store_state
    changed = store_state.seed_if_missing(
        tmp_data_dir, "apps", [("foo", "1.0.0"), ("bar", "2.0.0")]
    )
    assert changed is True
    state = store_state.load(tmp_data_dir, "apps")
    assert set(state["installed"].keys()) == {"foo", "bar"}
    assert state["installed"]["foo"]["version"] == "1.0.0"
    # last_change stays null on seed — only real install/uninstall sets it
    assert state["last_change"] is None


def test_seed_is_idempotent(tmp_data_dir):
    from emptyos.runtime import store_state
    store_state.seed_if_missing(tmp_data_dir, "apps", [("foo", "1.0.0")])
    changed = store_state.seed_if_missing(tmp_data_dir, "apps", [("bar", "2.0.0")])
    assert changed is False
    state = store_state.load(tmp_data_dir, "apps")
    # Seed didn't run again — bar isn't there, only foo.
    assert "foo" in state["installed"]
    assert "bar" not in state["installed"]


def test_mark_install_uninstall_roundtrip(tmp_data_dir):
    from emptyos.runtime import store_state
    store_state.seed_if_missing(tmp_data_dir, "apps", [])
    store_state.mark_installed(tmp_data_dir, "apps", "newapp", "0.5.0")
    assert store_state.is_installed(tmp_data_dir, "apps", "newapp")
    assert store_state.last_change(tmp_data_dir, "apps") is not None
    changed = store_state.mark_uninstalled(tmp_data_dir, "apps", "newapp")
    assert changed is True
    assert not store_state.is_installed(tmp_data_dir, "apps", "newapp")
    # Re-uninstall is a no-op
    changed = store_state.mark_uninstalled(tmp_data_dir, "apps", "newapp")
    assert changed is False


def test_disable_enable_roundtrip(tmp_data_dir):
    from emptyos.runtime import store_state
    store_state.mark_installed(tmp_data_dir, "apps", "alpha", "1.0.0")
    # Initial state — installed, not disabled
    assert store_state.is_installed(tmp_data_dir, "apps", "alpha")
    assert not store_state.is_disabled(tmp_data_dir, "apps", "alpha")
    assert "alpha" in store_state.enabled_ids(tmp_data_dir, "apps")

    # Disable
    assert store_state.mark_disabled(tmp_data_dir, "apps", "alpha") is True
    assert store_state.is_disabled(tmp_data_dir, "apps", "alpha")
    assert "alpha" in store_state.installed_ids(tmp_data_dir, "apps")
    assert "alpha" not in store_state.enabled_ids(tmp_data_dir, "apps")

    # Re-disable is no-op
    assert store_state.mark_disabled(tmp_data_dir, "apps", "alpha") is False

    # Enable
    assert store_state.mark_enabled(tmp_data_dir, "apps", "alpha") is True
    assert not store_state.is_disabled(tmp_data_dir, "apps", "alpha")
    assert "alpha" in store_state.enabled_ids(tmp_data_dir, "apps")

    # Re-enable is no-op
    assert store_state.mark_enabled(tmp_data_dir, "apps", "alpha") is False


def test_disable_not_installed_is_noop(tmp_data_dir):
    from emptyos.runtime import store_state
    # Disabling something never installed should not silently add it to disabled
    assert store_state.mark_disabled(tmp_data_dir, "apps", "ghost") is False
    assert "ghost" not in store_state.disabled_ids(tmp_data_dir, "apps")


def test_uninstall_clears_disabled_state(tmp_data_dir):
    """Uninstalling a disabled app should drop it from the disabled list too —
    otherwise the contradiction "disabled but not installed" persists in state."""
    from emptyos.runtime import store_state
    store_state.mark_installed(tmp_data_dir, "apps", "beta", "1.0.0")
    store_state.mark_disabled(tmp_data_dir, "apps", "beta")
    assert store_state.is_disabled(tmp_data_dir, "apps", "beta")
    store_state.mark_uninstalled(tmp_data_dir, "apps", "beta")
    assert not store_state.is_installed(tmp_data_dir, "apps", "beta")
    assert not store_state.is_disabled(tmp_data_dir, "apps", "beta")


def test_discover_marks_catalog_apps_as_parked(tmp_path):
    """Manifests under apps/_catalog/<id>/ should surface in the registry with
    parked=True; manifests under apps/<id>/ should be parked=False. Same id
    appearing in both → active wins (the parked copy is leftover)."""
    import tomllib  # noqa
    from emptyos.kernel.app_loader import AppLoader

    # Build a minimal apps/ tree
    apps_root = tmp_path / "apps"
    (apps_root / "live-app").mkdir(parents=True)
    (apps_root / "live-app" / "manifest.toml").write_text(
        '[app]\nid = "live-app"\nname = "Live"\nversion = "1.0.0"\ndescription = "live"\n',
        encoding="utf-8",
    )
    (apps_root / "_catalog" / "parked-app").mkdir(parents=True)
    (apps_root / "_catalog" / "parked-app" / "manifest.toml").write_text(
        '[app]\nid = "parked-app"\nname = "Parked"\nversion = "1.0.0"\ndescription = "parked"\n',
        encoding="utf-8",
    )
    # Same id in both — active should win
    (apps_root / "_catalog" / "live-app").mkdir(parents=True)
    (apps_root / "_catalog" / "live-app" / "manifest.toml").write_text(
        '[app]\nid = "live-app"\nname = "Live (catalog leftover)"\nversion = "0.9.0"\ndescription = "leftover"\n',
        encoding="utf-8",
    )

    # Mock kernel just enough to satisfy app_loader.discover
    class _SyslogStub:
        def warn(self, *a, **kw): pass
        def error(self, *a, **kw): pass
        def info(self, *a, **kw): pass
    class _ConfigStub:
        def __init__(self, root):
            self.path = root / "emptyos.toml"
            self.path.write_text("", encoding="utf-8")
            self.data_dir = root / "data"
            self.data_dir.mkdir(exist_ok=True)
            self._values = {"apps.path": str(root / "apps")}
        def get(self, key, default=None):
            return self._values.get(key, default)
        @property
        def demo_enabled(self):
            return False
    class _KernelStub:
        def __init__(self, root):
            self.config = _ConfigStub(root)
            self.syslog = _SyslogStub()

    kernel = _KernelStub(tmp_path)
    loader = AppLoader(kernel)
    loader.discover()

    # parked-app is parked
    parked = loader.manifests["parked-app"]
    assert parked.parked is True
    # live-app is NOT parked (active scan wins over catalog scan)
    live = loader.manifests["live-app"]
    assert live.parked is False
    # parked_ids() helper agrees
    assert loader.parked_ids() == {"parked-app"}
    # installed_ids() (after seed) excludes parked apps even though they're discovered
    installed = loader.installed_ids()
    assert "live-app" in installed
    assert "parked-app" not in installed


def test_load_migrates_v1_to_v2(tmp_data_dir):
    """A v1 state file (no `disabled` field) should be tolerated and read as if
    disabled were empty — the kernel must boot on existing daemons."""
    import json
    from emptyos.runtime import store_state
    path = tmp_data_dir / "store" / "installed-apps.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    v1_state = {
        "schema_version": 1,
        "installed": {"foo": {"installed_at": "2026-01-01T00:00:00", "version": "1.0.0"}},
        "last_change": None,
    }
    path.write_text(json.dumps(v1_state), encoding="utf-8")
    state = store_state.load(tmp_data_dir, "apps")
    assert state["installed"] == v1_state["installed"]
    assert state["disabled"] == []
    assert store_state.enabled_ids(tmp_data_dir, "apps") == {"foo"}


def test_load_tolerates_corrupt_json(tmp_data_dir):
    from emptyos.runtime import store_state
    path = tmp_data_dir / "store" / "installed-apps.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")
    # Should return empty shape rather than raising — boot must not be blocked.
    state = store_state.load(tmp_data_dir, "apps")
    assert state["installed"] == {}


# ──────────────────────────────────────────────────────────────────────────
# API tests — live daemon
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.api
class TestStoreCatalog:
    def test_apps_catalog_shape(self, http_client):
        data = assert_dict_response(http_client.get("/store/api/catalog/apps"))
        assert "items" in data
        assert isinstance(data["items"], list)
        assert len(data["items"]) > 5  # at least some apps discovered
        assert "essentials" in data
        # essentials should be a subset of items[].id where essential=True
        for item in data["items"]:
            for key in ("id", "name", "installed", "essential", "category"):
                assert key in item, f"missing {key}: {item}"

    def test_plugins_catalog_shape(self, http_client):
        data = assert_dict_response(http_client.get("/store/api/catalog/plugins"))
        assert isinstance(data["items"], list)
        assert "essentials" in data
        # health is the canonical essential plugin
        ids = {p["id"] for p in data["items"]}
        if "health" in ids:
            health = next(p for p in data["items"] if p["id"] == "health")
            assert health["essential"] is True

    def test_skills_catalog_shape(self, http_client):
        data = assert_dict_response(http_client.get("/store/api/catalog/skills"))
        assert isinstance(data["items"], list)
        # Each skill has id, name, description, installed, essential
        for item in data["items"]:
            for key in ("id", "name", "installed", "essential"):
                assert key in item, f"missing {key}: {item}"
            # All `eos-` prefix per the scope
            assert item["id"].startswith("eos-"), f"unexpected skill id: {item['id']}"

    def test_essential_apps_listed_as_essential(self, http_client):
        data = http_client.get("/store/api/catalog/apps").json()
        for essential in ("store", "settings", "system", "hub"):
            matching = [it for it in data["items"] if it["id"] == essential]
            if matching:  # may not all be discovered in every test env
                assert matching[0]["essential"] is True


@pytest.mark.api
class TestStoreInstallFlow:
    """Round-trip uninstall → reinstall against a leaf app.

    Tests are ordered: uninstall first, reinstall second, so a clean
    install state is restored at the end.
    """

    def test_01_uninstall_app(self, http_client):
        # First check the target is currently installed; if not, the test env
        # has already pruned — skip.
        cat = http_client.get("/store/api/catalog/apps").json()
        target = next((it for it in cat["items"] if it["id"] == TARGET_APP), None)
        if target is None or not target["installed"]:
            pytest.skip(f"{TARGET_APP} not installed in this env")
        resp = http_client.post(
            f"/store/api/uninstall/apps/{TARGET_APP}",
            json={},
        )
        body = assert_dict_response(resp)
        # Either uninstalled or needs_confirm if it has dependents — handle both.
        if body.get("needs_confirm"):
            resp = http_client.post(
                f"/store/api/uninstall/apps/{TARGET_APP}",
                json={"force": True},
            )
            body = assert_dict_response(resp)
        assert body.get("ok") is True
        assert body.get("restart_required") is True
        # Catalog reflects uninstall
        cat2 = http_client.get("/store/api/catalog/apps").json()
        target2 = next((it for it in cat2["items"] if it["id"] == TARGET_APP), None)
        assert target2 is not None
        assert target2["installed"] is False

    def test_02_reinstall_app(self, http_client):
        resp = http_client.post(
            f"/store/api/install/apps/{TARGET_APP}",
            json={"install_deps": True},
        )
        body = assert_dict_response(resp)
        assert body.get("ok") is True
        assert body.get("restart_required") is True
        cat = http_client.get("/store/api/catalog/apps").json()
        target = next((it for it in cat["items"] if it["id"] == TARGET_APP), None)
        assert target is not None
        assert target["installed"] is True


@pytest.mark.api
class TestStoreEssentialLock:
    def test_cannot_uninstall_essential_app(self, http_client):
        resp = http_client.post("/store/api/uninstall/apps/store", json={"force": True})
        body = resp.json()
        assert "error" in body
        assert "essential" in body["error"].lower()

    def test_cannot_uninstall_essential_plugin(self, http_client):
        resp = http_client.post("/store/api/uninstall/plugins/health", json={"force": True})
        body = resp.json()
        assert "error" in body
        assert "essential" in body["error"].lower()

    def test_cannot_disable_essential_app(self, http_client):
        resp = http_client.post("/store/api/disable/apps/store", json={})
        body = resp.json()
        assert "error" in body
        assert "essential" in body["error"].lower()

    def test_skills_reject_disable(self, http_client):
        """Skills don't have a separate disable state — folder location is the toggle."""
        resp = http_client.post("/store/api/disable/skills/eos-design-review", json={})
        body = resp.json()
        assert "error" in body


@pytest.mark.api
class TestStoreDisableEnableFlow:
    """Round-trip disable → enable against a leaf app, then ensure the catalog
    reflects each state."""

    def test_01_disable_app(self, http_client):
        cat = http_client.get("/store/api/catalog/apps").json()
        target = next((it for it in cat["items"] if it["id"] == TARGET_APP), None)
        if target is None or not target["installed"]:
            pytest.skip(f"{TARGET_APP} not installed in this env")
        if target.get("disabled"):
            pytest.skip(f"{TARGET_APP} already disabled in this env")
        resp = http_client.post(f"/store/api/disable/apps/{TARGET_APP}", json={})
        body = assert_dict_response(resp)
        assert body.get("ok") is True
        assert body.get("restart_required") is True
        cat2 = http_client.get("/store/api/catalog/apps").json()
        target2 = next(it for it in cat2["items"] if it["id"] == TARGET_APP)
        assert target2["installed"] is True
        assert target2["disabled"] is True

    def test_02_enable_app(self, http_client):
        resp = http_client.post(f"/store/api/enable/apps/{TARGET_APP}", json={})
        body = assert_dict_response(resp)
        assert body.get("ok") is True
        assert body.get("restart_required") is True
        cat = http_client.get("/store/api/catalog/apps").json()
        target = next(it for it in cat["items"] if it["id"] == TARGET_APP)
        assert target["installed"] is True
        assert target["disabled"] is False


@pytest.mark.api
class TestStoreRestartFlag:
    def test_restart_flag_endpoint_returns_bool(self, http_client):
        resp = http_client.get("/store/api/restart-required")
        body = assert_dict_response(resp)
        assert "restart_required" in body
        assert isinstance(body["restart_required"], bool)
