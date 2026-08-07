"""Unit tests for the external-lab-host supervisor plugin — ownership,
recursion, and port-conflict logic with spawn/terminate/probe mocked.

No real subprocess is started: these pin the safety semantics
(.claude/rules/daemon-handling.md — only kill what you own, never an unrelated
program on :9100)."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_PATH = ROOT / "plugins" / "external-lab-host" / "plugin.py"


def _load_module():
    # Unique module name so it can't collide with other plugins' `plugin.py`.
    spec = importlib.util.spec_from_file_location("external_lab_host_plugin", PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _StubConfig:
    def __init__(self, root: Path, own_port: int = 9000, demo: bool = False):
        self.path = root / "emptyos.toml"
        self._own_port = own_port
        self.demo_enabled = demo

    @property
    def data_dir(self):
        return self.path.parent / "data"

    def get(self, key, default=None):
        if key == "network.port":
            return self._own_port
        return default


class _StubKernel:
    def __init__(self, root: Path, **cfg):
        self.config = _StubConfig(root, **cfg)
        self.settings = _StubSettings()
        self.services = _StubServices(self.settings)


class _StubSettings:
    def __init__(self):
        self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


class _StubServices:
    def __init__(self, settings):
        self.settings = settings

    def get_optional(self, name):
        return self.settings if name == "settings" else None


def _make_plugin(tmp_path, *, own_port=9000, demo=False, config=None):
    mod = _load_module()
    plugin = mod.ExternalLabHostPlugin(_StubKernel(tmp_path, own_port=own_port, demo=demo), manifest={})
    plugin._config = {"enabled": True, "autostart": False, **(config or {})}
    return mod, plugin


def test_recursion_guard_env(tmp_path, monkeypatch):
    monkeypatch.setenv("EOS_SANDBOX_POOL_MEMBER", "1")
    _, plugin = _make_plugin(tmp_path)
    assert plugin._is_inner() is True
    assert asyncio.run(plugin.auto_start()) is False
    assert asyncio.run(plugin.stop()) == {"ok": False, "reason": "recursion_guard"}


def test_recursion_guard_own_port_is_lab_port(tmp_path):
    # If our own daemon is already :9100, we ARE the lab host — refuse.
    _, plugin = _make_plugin(tmp_path, own_port=9100)
    assert plugin._is_inner() is True


def test_available_only_on_matching_identity(tmp_path):
    mod, plugin = _make_plugin(tmp_path)

    async def go():
        plugin._identity = lambda: _coro(mod.IDENTITY)
        assert await plugin.available() is True
        plugin._identity = lambda: _coro("something-else")
        assert await plugin.available() is False
        assert await plugin._port_conflict() is True
        plugin._identity = lambda: _coro(None)
        assert await plugin.available() is False
        assert await plugin._port_conflict() is False

    asyncio.run(go())


def test_stop_refuses_unowned_identity_match(tmp_path):
    mod, plugin = _make_plugin(tmp_path)
    plugin._proc = None
    plugin._identity = lambda: _coro(mod.IDENTITY)  # ours, but we didn't spawn it
    result = asyncio.run(plugin.stop())
    assert result["ok"] is False
    assert result["reason"] == "running_but_unowned"


def test_stop_refuses_port_conflict(tmp_path):
    mod, plugin = _make_plugin(tmp_path)
    plugin._proc = None
    plugin._identity = lambda: _coro("nginx")  # unrelated program on :9100
    result = asyncio.run(plugin.stop())
    assert result["ok"] is False
    assert result["reason"] == "port_conflict"


def test_stop_already_stopped_when_port_free(tmp_path):
    mod, plugin = _make_plugin(tmp_path)
    plugin._proc = None
    plugin._identity = lambda: _coro(None)  # port free
    assert asyncio.run(plugin.stop()) == {"ok": True, "already_stopped": True}


def test_owned_stop_terminates_and_clears_handle(tmp_path, monkeypatch):
    mod, plugin = _make_plugin(tmp_path)
    plugin._proc = object()  # a stand-in owned handle

    async def fake_terminate(proc, **kw):
        return {"ok": True}

    monkeypatch.setattr(mod, "terminate_daemon", fake_terminate)
    result = asyncio.run(plugin.stop())
    assert result == {"ok": True}
    assert plugin._proc is None


def test_auto_start_refuses_on_port_conflict(tmp_path, monkeypatch):
    mod, plugin = _make_plugin(tmp_path)
    plugin._identity = lambda: _coro("nginx")  # conflict
    spawned = {"called": False}

    def fake_spawn(**kw):
        spawned["called"] = True
        raise AssertionError("must not spawn on conflict")

    monkeypatch.setattr(mod, "spawn_emptyos_daemon", fake_spawn)
    assert asyncio.run(plugin.auto_start()) is False
    assert spawned["called"] is False


def test_auto_start_spawn_failure_is_soft(tmp_path, monkeypatch):
    mod, plugin = _make_plugin(tmp_path)
    plugin._identity = lambda: _coro(None)  # port free
    # Make the entry file appear present so we reach the spawn attempt.
    monkeypatch.setattr(mod.Path, "exists", lambda self: True)

    def fake_spawn(**kw):
        raise OSError("boom")

    monkeypatch.setattr(mod, "spawn_emptyos_daemon", fake_spawn)
    # Never raises — returns False, :9000 boot unaffected.
    assert asyncio.run(plugin.auto_start()) is False


def test_clean_bootstrap_generates_persistent_token_and_safe_config(tmp_path):
    _, plugin = _make_plugin(tmp_path)

    env = plugin._extra_env()

    assert env["CHATBOT_ADMIN_TOKEN"] == plugin.kernel.settings.values[
        "chatbot-studio.admin_token"
    ]
    assert len(env["CHATBOT_ADMIN_TOKEN"]) >= 32
    assert env["CHATBOT_PUBLIC_ORIGIN"] == "http://127.0.0.1:9100"
    sites = Path(env["CHATBOT_SITES_PATH"])
    assert sites.exists()
    assert "[defaults]" in sites.read_text(encoding="utf-8")
    assert Path(env["CHATBOT_DATA_DIR"]).is_dir()
    assert plugin._admin_token() == env["CHATBOT_ADMIN_TOKEN"]


def test_first_boot_migrates_legacy_config_and_data_once(tmp_path):
    legacy = tmp_path / "services" / "chatbot"
    (legacy / "data" / "catalogs").mkdir(parents=True)
    (legacy / "sites.toml").write_text("[defaults]\nmodel='legacy'\n", encoding="utf-8")
    (legacy / "data" / "catalogs" / "legacy.json").write_text("{}", encoding="utf-8")
    _, plugin = _make_plugin(tmp_path)

    sites, data = plugin._prepare_runtime()

    assert "model='legacy'" in sites.read_text(encoding="utf-8")
    assert (data / "catalogs" / "legacy.json").exists()
    # Managed state wins after migration; later legacy changes are not recopied.
    (legacy / "sites.toml").write_text("changed", encoding="utf-8")
    assert plugin._prepare_runtime()[0].read_text(encoding="utf-8") != "changed"


def test_saved_ownership_requires_pid_time_port_and_identity(tmp_path, monkeypatch):
    mod, plugin = _make_plugin(tmp_path)
    plugin._write_state(1234, 50.25)
    monkeypatch.setattr(plugin, "_process_create_time", lambda pid: 50.25)
    monkeypatch.setattr(plugin, "_port_owner_pid", lambda: 1234)
    plugin._identity = lambda: _coro(mod.IDENTITY)

    assert asyncio.run(plugin._reattach()) is True
    assert plugin._owned_pid == 1234

    monkeypatch.setattr(plugin, "_port_owner_pid", lambda: 9999)
    assert plugin._load_valid_state() is None


def test_disconnect_attempts_graceful_owned_shutdown(tmp_path):
    _, plugin = _make_plugin(tmp_path)
    called = {"stop": False}

    async def fake_stop():
        called["stop"] = True
        return {"ok": True}

    plugin.stop = fake_stop
    asyncio.run(plugin.disconnect())
    assert called["stop"] is True


def test_reattached_owned_process_can_be_stopped_after_final_validation(tmp_path, monkeypatch):
    mod, plugin = _make_plugin(tmp_path)
    plugin._owned_pid = 1234
    plugin._owned_create_time = 50.25
    monkeypatch.setattr(plugin, "_load_valid_state", lambda: (1234, 50.25))
    monkeypatch.setattr(plugin, "_port_owner_pid", lambda: 1234)
    monkeypatch.setattr(plugin, "_process_create_time", lambda pid: 50.25)
    calls = []

    class _Process:
        def terminate(self):
            calls.append("terminate")

        def wait(self, _timeout):
            calls.append("wait")

    monkeypatch.setattr(mod.psutil, "Process", lambda pid: _Process())
    result = asyncio.run(plugin._stop_reattached())

    assert result == {"ok": True, "reattached": True}
    assert calls == ["terminate", "wait"]
    assert plugin._owned_pid is None


async def _coro(value):
    return value
