from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "plugins" / "sandbox-pool"


class _StubConfig:
    def __init__(self, root: Path):
        self.path = root / "emptyos.toml"
        self.root = root

    def get(self, key: str, default=None):
        if key == "network.port":
            return 9000
        if key == "os.data_dir":
            return str(self.root / "data")
        return default


class _StubKernel:
    def __init__(self, root: Path):
        self.config = _StubConfig(root)


def _load_plugin_module():
    if str(PLUGIN_DIR) not in sys.path:
        sys.path.insert(0, str(PLUGIN_DIR))
    if "plugin" in sys.modules:
        return importlib.reload(sys.modules["plugin"])
    return importlib.import_module("plugin")


def _make_plugin(tmp_path):
    mod = _load_plugin_module()
    plugin = mod.SandboxPoolPlugin(_StubKernel(tmp_path), manifest={})
    plugin._config = {
        "enabled": True,
        "pool_size": 1,
        "base_port": 19002,
        "boot_timeout_s": 1,
        "lease_ttl_s": 60,
        "autostart": False,
        "autoboot_members": False,
    }
    port = plugin._iter_ports()[0]
    plugin._members = {
        port: mod.PoolMember(port=port, dir=plugin._member_dir(port), state="idle")
    }
    return plugin, port


def test_status_distinguishes_reachable_from_ready(tmp_path):
    async def run():
        plugin, port = _make_plugin(tmp_path)

        async def _member_health(_port):
            return {"reachable": True, "ready": False, "status": "starting", "apps": 0}

        plugin._member_health = _member_health
        status = await plugin.status()
        member = status["members"][0]
        assert member["port"] == port
        assert member["reachable"] is True
        assert member["ready"] is False
        assert member["health_status"] == "starting"
        assert member["apps"] == 0

    asyncio.run(run())


def test_lease_respawns_live_member_that_is_not_ready(tmp_path):
    async def run():
        plugin, _port = _make_plugin(tmp_path)
        calls = {"terminated": 0, "spawned": 0}

        async def _member_health(_port):
            return {"reachable": True, "ready": False, "status": "starting", "apps": 0}

        async def _terminate(port):
            calls["terminated"] += 1
            plugin._members[port].state = "dead"
            return {"ok": True}

        async def _spawn(port):
            calls["spawned"] += 1
            plugin._members[port].state = "idle"
            return True

        plugin._member_health = _member_health
        plugin._terminate_member = _terminate
        plugin._spawn_member = _spawn

        lease = await plugin.lease(purpose="readiness")
        assert lease["ok"] is True
        assert calls == {"terminated": 1, "spawned": 1}

    asyncio.run(run())


def test_stale_relative_member_code_paths_are_repaired(tmp_path):
    plugin, port = _make_plugin(tmp_path)
    for name in ("apps", "plugins", "engines"):
        (tmp_path / name).mkdir()

    member_dir = tmp_path / f"sandbox-{port}"
    member_dir.mkdir(parents=True)
    cfg = member_dir / "emptyos.toml"
    cfg.write_text(
        """
[apps]
path = "apps"

[plugins]
path = "plugins"

[engines]
path = "engines"
""".strip(),
        encoding="utf-8",
    )

    assert plugin._member_code_paths_ready(port) is False
    assert plugin._ensure_member_code_paths(port, None) is True
    assert plugin._member_code_paths_ready(port) is True

    text = cfg.read_text(encoding="utf-8")
    assert f'path = "{(tmp_path / "apps").as_posix()}"' in text
    assert f'path = "{(tmp_path / "plugins").as_posix()}"' in text
    assert f'path = "{(tmp_path / "engines").as_posix()}"' in text


def test_member_resolution_accepts_port_reference(tmp_path):
    plugin, port = _make_plugin(tmp_path)
    member = plugin._members[port]
    member.lease_id = "lease-real"

    assert plugin._resolve_member(str(port), None) is member
    assert plugin._resolve_member(f":{port}", None) is member
    assert plugin._resolve_member("lease-real", None) is member


def test_restart_by_port_recovers_expired_lease(tmp_path):
    async def run():
        plugin, port = _make_plugin(tmp_path)
        member = plugin._members[port]
        member.state = "leased"
        member.lease_id = "lease-expired"
        member.lease_expires_at = 1.0

        calls = {"terminated": 0, "spawned": 0}

        async def _terminate(_port):
            calls["terminated"] += 1
            member.state = "dead"
            return {"ok": True}

        async def _spawn(_port):
            calls["spawned"] += 1
            member.state = "idle"
            return True

        plugin._ensure_member_code_paths = lambda _port, _source_root: False
        plugin._terminate_member = _terminate
        plugin._spawn_member = _spawn

        result = await plugin.restart(str(port))

        assert result["ok"] is True
        assert result["port"] == port
        assert "lease_id" not in result
        assert "expires_at" not in result
        assert member.state == "idle"
        assert member.lease_id is None
        assert calls == {"terminated": 1, "spawned": 1}

    asyncio.run(run())
