"""System tests: Sandbox Pool — HTTP API + plugin lease state machine.

Two layers:

1. **Unit tests** against the plugin object with a stub kernel — exercise
   lease lifecycle, conflict on full pool, expiry, restart contract — no
   subprocess spawning required.
2. **API tests** against a running daemon — exercise the /sandbox/api/*
   route shapes. These auto-skip if the daemon is unreachable. They do
   NOT touch the spawn paths (which would actually boot sandboxes); the
   plugin gracefully reports `pool_full` / `disabled_in_config` shapes
   that we can assert on.

Spawning real :9002+ daemons is a manual smoke (`POST /sandbox/api/lease`
+ wait for HTTP on the returned port). Wiring that into CI would burn
~30 seconds per test on boot+teardown; the unit layer + state file
inspection covers the contract without paying that.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

# Make the repo importable for the unit layer.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helpers import assert_dict_response

PLUGIN_PREFIX = "/sandbox"


# ─── Unit layer: stub-kernel plugin tests ─────────────────────────────────


class _StubConfig:
    def __init__(self, path: Path):
        self.path = path

    def get(self, key: str, default=None):
        # Stub kernel uses a flat path-style getter; we only need
        # network.port (so _is_inner_member doesn't mistake us for one)
        # and os.data_dir (for state persistence).
        if key == "network.port":
            return 9000   # main daemon — NOT one of the pool ports
        if key == "os.data_dir":
            return str(self.path / "data")
        return default


class _StubKernel:
    def __init__(self, root: Path):
        self.config = _StubConfig(root)


def _load_plugin_module():
    """Direct module import — sandbox-pool dir name has a hyphen so it's
    not a valid Python package; load via path injection."""
    import importlib
    target = str(ROOT / "plugins" / "sandbox-pool")
    if target not in sys.path:
        sys.path.insert(0, target)
    if "plugin" in sys.modules:
        return importlib.reload(sys.modules["plugin"])
    return importlib.import_module("plugin")


def _make_plugin(tmp_path, **config_overrides):
    """Build a SandboxPoolPlugin against a stub kernel rooted at tmp_path."""
    mod = _load_plugin_module()
    kernel = _StubKernel(tmp_path)
    plugin = mod.SandboxPoolPlugin(kernel, manifest={})
    plugin._config = {
        "enabled": True,
        "pool_size": 2,
        "base_port": 19002,        # well above 9002/9001/9000 — no collision
        "boot_timeout_s": 1,       # not used in unit tests, but tight
        "lease_ttl_s": 60,
        "autostart": False,
        "autoboot_members": False,
        **config_overrides,
    }
    return plugin, mod


@pytest.fixture
def plugin(tmp_path):
    """Stub-kernel plugin instance for unit tests."""
    p, _ = _make_plugin(tmp_path)
    return p


def _materialise_members(plugin):
    """Replay what connect() does *without* the aiohttp session — we
    can't await real HTTP probes in unit tests without a session."""
    mod = _load_plugin_module()
    plugin._members = {
        port: mod.PoolMember(port=port, dir=plugin._member_dir(port))
        for port in plugin._iter_ports()
    }
    # Mark all as idle so _lease has something to pick — bypasses real spawn.
    for m in plugin._members.values():
        m.state = "idle"


async def _patch_no_spawn_no_probe(plugin):
    """Replace I/O-bound methods so unit tests don't touch the network or
    spawn subprocesses. Probes always succeed, spawns are no-ops."""
    async def _spawn(port):
        m = plugin._members.get(port)
        if m is None:
            return False
        m.state = "leased" if m.lease_id else "idle"
        m.last_started = time.time()
        return True

    async def _probe(port):
        m = plugin._members.get(port)
        return m is not None and m.state in ("idle", "leased")

    async def _terminate(port):
        m = plugin._members.get(port)
        if m is not None:
            m.proc = None
            m.state = "dead"
        return {"ok": True}

    plugin._spawn_member = _spawn
    plugin._probe_port = _probe
    plugin._terminate_member = _terminate


@pytest.mark.api
class TestSandboxPoolUnit:
    """Plugin lease state machine — no HTTP, no subprocesses."""

    def test_default_pool_size_clamps(self, plugin):
        plugin._config["pool_size"] = 99
        assert plugin._pool_size() == 3
        plugin._config["pool_size"] = 0
        assert plugin._pool_size() == 1
        plugin._config["pool_size"] = "garbage"
        assert plugin._pool_size() == 2

    def test_iter_ports_uses_base_port(self, plugin):
        plugin._config["base_port"] = 9500
        plugin._config["pool_size"] = 3
        assert plugin._iter_ports() == [9500, 9501, 9502]

    def test_lease_picks_idle_member(self, plugin):
        async def run():
            _materialise_members(plugin)
            await _patch_no_spawn_no_probe(plugin)
            res = await plugin.lease(purpose="unit-test")
            assert res["ok"] is True
            assert res["port"] in plugin._iter_ports()
            assert res["lease_id"].startswith("lease-")
            assert res["purpose"] == "unit-test"
        asyncio.run(run())

    def test_lease_full_pool_returns_pool_full(self, plugin):
        async def run():
            _materialise_members(plugin)
            await _patch_no_spawn_no_probe(plugin)
            # Lease every slot.
            results = []
            for _ in range(plugin._pool_size()):
                results.append(await plugin.lease(purpose="fill"))
            assert all(r["ok"] for r in results)
            # Next lease must fail with pool_full.
            res = await plugin.lease(purpose="overflow")
            assert res["ok"] is False
            assert res["error"] == "pool_full"
            assert isinstance(res.get("members"), list)
            assert len(res["members"]) == plugin._pool_size()
        asyncio.run(run())

    def test_release_makes_slot_available(self, plugin):
        async def run():
            _materialise_members(plugin)
            await _patch_no_spawn_no_probe(plugin)
            first = await plugin.lease(purpose="hold")
            assert first["ok"]
            rel = await plugin.release(first["lease_id"])
            assert rel["ok"] is True
            # The same slot can be re-leased now.
            second = await plugin.lease(purpose="second")
            assert second["ok"] is True
            assert second["port"] == first["port"]
        asyncio.run(run())

    def test_release_unknown_lease_returns_error(self, plugin):
        async def run():
            _materialise_members(plugin)
            await _patch_no_spawn_no_probe(plugin)
            res = await plugin.release("lease-does-not-exist")
            assert res["ok"] is False
            assert res["error"] == "no_such_lease"
        asyncio.run(run())

    def test_touch_extends_expiry(self, plugin):
        async def run():
            _materialise_members(plugin)
            await _patch_no_spawn_no_probe(plugin)
            lease = await plugin.lease(purpose="touch", ttl_s=60)
            assert lease["ok"]
            original = lease["expires_at"]
            await asyncio.sleep(0.01)
            touched = await plugin.touch(lease["lease_id"], ttl_s=180)
            assert touched["ok"] is True
            assert touched["expires_at"] > original
        asyncio.run(run())

    def test_expired_lease_auto_releases_on_status(self, plugin):
        async def run():
            _materialise_members(plugin)
            await _patch_no_spawn_no_probe(plugin)
            lease = await plugin.lease(purpose="expire", ttl_s=30)
            assert lease["ok"]
            # Forcibly age the lease past expiry.
            member = plugin._find_by_lease(lease["lease_id"])
            assert member is not None
            member.lease_expires_at = time.time() - 1
            # status() should expire it. With no aiohttp session the
            # member may transition idle→dead since probe returns False,
            # but the load-bearing assertion is that the lease was cleared.
            status = await plugin.status()
            ports = {m["port"]: m for m in status["members"]}
            assert ports[member.port]["lease"] is None
            assert ports[member.port]["state"] in ("idle", "dead")
        asyncio.run(run())

    def test_restart_only_succeeds_for_lease_holder(self, plugin):
        async def run():
            _materialise_members(plugin)
            await _patch_no_spawn_no_probe(plugin)
            lease = await plugin.lease(purpose="restart")
            assert lease["ok"]
            # Bogus lease_id rejected.
            bad = await plugin.restart("lease-not-mine")
            assert bad["ok"] is False
            assert bad["error"] == "no_such_lease"
            # Real lease_id accepted; with patched _spawn that returns True,
            # restart should report ok + same port.
            good = await plugin.restart(lease["lease_id"])
            assert good["ok"] is True
            assert good["port"] == lease["port"]
        asyncio.run(run())

    def test_inner_member_detection_blocks_lease(self, plugin):
        # Simulate "we are inside a pool member" by making our own port
        # one of the pool ports.
        plugin._config["base_port"] = 9000   # collides with stub kernel network.port
        plugin._config["pool_size"] = 1
        async def run():
            _materialise_members(plugin)
            await _patch_no_spawn_no_probe(plugin)
            res = await plugin.lease(purpose="recursion-test")
            assert res["ok"] is False
            assert res["error"] == "inside_pool_member"
        asyncio.run(run())

    def test_disabled_plugin_refuses_lease(self, plugin):
        plugin._config["enabled"] = False
        async def run():
            _materialise_members(plugin)
            await _patch_no_spawn_no_probe(plugin)
            res = await plugin.lease(purpose="disabled-test")
            assert res["ok"] is False
            assert res["error"] == "disabled_in_config"
        asyncio.run(run())

    def test_member_config_template_renders(self, plugin, tmp_path):
        """First-boot template materialises emptyos.toml + data/ + vault/."""
        # Point project root at tmp_path so we don't pollute the real repo.
        plugin._project_root = lambda: tmp_path
        mod = _load_plugin_module()
        port = 19099
        plugin._members[port] = mod.PoolMember(port=port, dir=plugin._member_dir(port))
        cfg_path = plugin._write_member_config_if_missing(port)
        assert cfg_path.exists()
        text = cfg_path.read_text(encoding="utf-8")
        assert f'port = {port}' in text
        assert 'enabled = false' in text   # Recursion guards present.
        # Subsequent call is idempotent (doesn't rewrite).
        ts = cfg_path.stat().st_mtime
        cfg_path2 = plugin._write_member_config_if_missing(port)
        assert cfg_path2 == cfg_path
        assert cfg_path2.stat().st_mtime == ts

    def test_state_file_persists_active_lease(self, plugin, tmp_path):
        """After a lease, pool.json reflects state — lets a daemon restart
        reattach to a leased member instead of orphaning it."""
        async def run():
            _materialise_members(plugin)
            await _patch_no_spawn_no_probe(plugin)
            lease = await plugin.lease(purpose="persist-test", ttl_s=120)
            assert lease["ok"]
            state_path = plugin._state_path()
            assert state_path.exists()
            import json
            data = json.loads(state_path.read_text())
            assert "members" in data
            leased_rows = [r for r in data["members"] if r.get("lease_id") == lease["lease_id"]]
            assert len(leased_rows) == 1
            row = leased_rows[0]
            assert row["state"] == "leased"
            assert row["lease_purpose"] == "persist-test"
            assert row["lease_expires_at"] > time.time()
        asyncio.run(run())

    def test_touch_unknown_lease_returns_error(self, plugin):
        async def run():
            _materialise_members(plugin)
            await _patch_no_spawn_no_probe(plugin)
            res = await plugin.touch("lease-not-a-thing")
            assert res["ok"] is False
            assert res["error"] == "no_such_lease"
        asyncio.run(run())

    def test_get_log_falls_back_when_unleased(self, plugin):
        """get_log on an unknown lease returns a structured error rather
        than raising — the route surfaces this as {ok: False, error: ...}."""
        async def run():
            _materialise_members(plugin)
            await _patch_no_spawn_no_probe(plugin)
            res = await plugin.get_log("lease-not-real", tail=50)
            assert res["ok"] is False
            assert res["error"] == "no_such_lease"
        asyncio.run(run())

    def test_status_includes_inner_member_flag(self, plugin):
        """status() exposes is_inner_member so a UI / CLI can decide to
        hide the management surface inside pool members."""
        async def run():
            _materialise_members(plugin)
            await _patch_no_spawn_no_probe(plugin)
            status = await plugin.status()
            assert "is_inner_member" in status
            assert status["is_inner_member"] is False  # stub kernel uses :9000
            assert status["enabled"] is True
        asyncio.run(run())

    def test_lease_ttl_clamps_to_max_one_hour(self, plugin):
        """A caller asking for ttl_s=99999 must get clamped to 3600 — caps
        the runaway-lease risk if a buggy client never releases."""
        async def run():
            _materialise_members(plugin)
            await _patch_no_spawn_no_probe(plugin)
            lease = await plugin.lease(purpose="ttl-clamp", ttl_s=99999)
            assert lease["ok"]
            assert lease["expires_at"] <= time.time() + 3601
            assert lease["expires_at"] >= time.time() + 3500
        asyncio.run(run())

    def test_lease_ttl_clamps_to_min_30s(self, plugin):
        """Conversely, ttl_s=1 must get bumped to the 30s floor."""
        async def run():
            _materialise_members(plugin)
            await _patch_no_spawn_no_probe(plugin)
            lease = await plugin.lease(purpose="ttl-floor", ttl_s=1)
            assert lease["ok"]
            assert lease["expires_at"] >= time.time() + 25
        asyncio.run(run())

    def test_available_count_includes_dead_members(self, plugin):
        """A dead member is leaseable (will respawn), so available_count
        must count it. Previously bug: only counted idle + expired-leased."""
        async def run():
            _materialise_members(plugin)
            await _patch_no_spawn_no_probe(plugin)
            # Force one member to dead.
            ports = list(plugin._members.keys())
            assert len(ports) >= 2
            plugin._members[ports[0]].state = "dead"
            plugin._members[ports[1]].state = "idle"
            status = await plugin.status()
            # Both are leaseable: idle (immediate) + dead (will respawn).
            assert status["available_count"] == 2
        asyncio.run(run())

    def test_purpose_truncates_to_120_chars(self, plugin):
        """A pathological caller passing 10kb of "purpose" gets clipped —
        protects pool.json from unbounded growth."""
        long_purpose = "X" * 10_000
        async def run():
            _materialise_members(plugin)
            await _patch_no_spawn_no_probe(plugin)
            lease = await plugin.lease(purpose=long_purpose)
            assert lease["ok"]
            assert lease["purpose"] is not None
            assert len(lease["purpose"]) <= 120
        asyncio.run(run())


# ─── API layer: HTTP smoke against a running daemon ──────────────────────


def _has_sandbox_route(http_client) -> bool:
    """Skip API tests cleanly if the sandbox app isn't loaded (e.g. demo
    daemon, or the user disabled it in their store gate)."""
    try:
        resp = http_client.get(f"{PLUGIN_PREFIX}/api/status")
    except Exception:
        return False
    return resp.status_code == 200


@pytest.mark.api
class TestSandboxPoolAPI:
    """End-to-end against the running main daemon at :9000.

    These do NOT trigger a real spawn — the plugin's `enabled` config
    decides whether `lease` would actually boot a subprocess. Tests cover
    the route shape + error paths, not the spawn lifecycle (which is
    exercised by the unit layer with patched I/O)."""

    def test_status_shape(self, http_client):
        if not _has_sandbox_route(http_client):
            pytest.skip("sandbox app not loaded (or daemon unreachable)")
        data = assert_dict_response(
            http_client.get(f"{PLUGIN_PREFIX}/api/status"),
            required_keys=["pool_size", "base_port", "members", "available_count"],
        )
        assert isinstance(data["members"], list)
        assert isinstance(data["available_count"], int)
        for m in data["members"]:
            assert "port" in m and "state" in m and "reachable" in m

    def test_release_unknown_lease_returns_no_such_lease(self, http_client):
        if not _has_sandbox_route(http_client):
            pytest.skip("sandbox app not loaded (or daemon unreachable)")
        resp = http_client.delete(f"{PLUGIN_PREFIX}/api/lease/lease-does-not-exist")
        data = assert_dict_response(resp)
        assert data["ok"] is False
        assert data["error"] == "no_such_lease"

    def test_touch_unknown_lease_returns_no_such_lease(self, http_client):
        if not _has_sandbox_route(http_client):
            pytest.skip("sandbox app not loaded (or daemon unreachable)")
        resp = http_client.post(f"{PLUGIN_PREFIX}/api/lease/lease-does-not-exist/touch")
        data = assert_dict_response(resp)
        assert data["ok"] is False
        assert data["error"] == "no_such_lease"

    def test_restart_unknown_lease_returns_no_such_lease(self, http_client):
        if not _has_sandbox_route(http_client):
            pytest.skip("sandbox app not loaded (or daemon unreachable)")
        resp = http_client.post(f"{PLUGIN_PREFIX}/api/lease/lease-does-not-exist/restart")
        data = assert_dict_response(resp)
        assert data["ok"] is False
        # Either the lease is unknown OR the plugin is missing — both valid.
        assert data["error"] in ("no_such_lease", "sandbox-pool plugin not loaded")

    def test_log_unknown_lease_returns_no_such_lease(self, http_client):
        if not _has_sandbox_route(http_client):
            pytest.skip("sandbox app not loaded (or daemon unreachable)")
        resp = http_client.get(f"{PLUGIN_PREFIX}/api/lease/lease-does-not-exist/log")
        data = assert_dict_response(resp)
        assert data["ok"] is False

    def test_hub_panel_renders_or_drops(self, http_client):
        """The sandbox hub panel must appear in /hub/api/panels exactly once
        when the plugin is loaded, OR be absent entirely when not."""
        if not _has_sandbox_route(http_client):
            pytest.skip("sandbox app not loaded (or daemon unreachable)")
        try:
            resp = http_client.get("/hub/api/panels")
        except Exception:
            pytest.skip("hub app not available")
        if resp.status_code != 200:
            pytest.skip(f"/hub/api/panels returned {resp.status_code}")
        data = resp.json()
        # Different hub versions wrap panels differently — accept dict-with-panels,
        # dict-with-results, or a bare list of panel records.
        records = (
            (data.get("blocks") if isinstance(data, dict) else None)
            or (data.get("panels") if isinstance(data, dict) else None)
            or (data.get("results") if isinstance(data, dict) else None)
            or (data if isinstance(data, list) else None)
            or []
        )
        sandbox_panels = [
            r for r in records
            if isinstance(r, dict) and (r.get("id") == "sandbox-pool" or r.get("panel_id") == "sandbox-pool")
        ]
        # 0 (panel returned None — pool empty) or 1 (panel rendered). Never duplicates.
        assert len(sandbox_panels) <= 1, f"duplicate sandbox-pool panels: {sandbox_panels!r}"

    def test_lease_with_bad_json_does_not_500(self, http_client):
        if not _has_sandbox_route(http_client):
            pytest.skip("sandbox app not loaded (or daemon unreachable)")
        # Send non-JSON body — handler should coerce to {} and either
        # succeed (pool enabled + has slot) or return a structured error.
        # CRITICAL: it must not 500.
        resp = http_client.post(
            f"{PLUGIN_PREFIX}/api/lease",
            content=b"this is not json",
            headers={"Content-Type": "text/plain"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
        # Either ok=True (a slot was free) or ok=False with an error key.
        if not data.get("ok"):
            assert "error" in data
            return
        # If we got a real lease, release it so the test doesn't leave the
        # pool half-full for the next run. Spawning a sandbox subprocess
        # is fine in CI; orphaning a lease is not.
        lease_id = data.get("lease_id")
        if lease_id:
            http_client.delete(f"{PLUGIN_PREFIX}/api/lease/{lease_id}")
