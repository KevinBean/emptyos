"""Tailscale plugin + /api/tailnet + audit middleware.

Three layers:

  TestTailscalePluginLogic   pure logic — binary resolve, status parsing,
                              is_peer, funnel detection, peer sort. Loads
                              the plugin module via importlib; never spawns
                              subprocesses or touches the kernel.

  TestTailnetAPI             /api/tailnet endpoint shape — requires the
                              live daemon. Skips when tailscale plugin
                              isn't loaded.

  TestAuditMiddleware        unauthenticated requests produce a syslog
                              `auth` entry with throttling. The labelling
                              path (tailnet peer name in the message) is
                              exercised by sending an X-Forwarded-For of
                              a known peer IP.

Run: python -m pytest tests/test_sys_tailscale.py -v
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

from helpers import assert_ok

REPO = Path(__file__).resolve().parent.parent
PLUGIN_PATH = REPO / "plugins" / "tailscale" / "plugin.py"


# ── Pure plugin logic (no daemon) ─────────────────────────────────────────


@pytest.fixture(scope="module")
def plugin_module():
    spec = importlib.util.spec_from_file_location(
        "tailscale_under_test", PLUGIN_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _make_plugin(mod, cache: dict | None = None):
    """Build a TailscalePlugin without kernel boot.

    Bypasses the BasePlugin.__init__ kernel requirement by setting only the
    attributes the methods under test actually read.
    """
    p = mod.TailscalePlugin.__new__(mod.TailscalePlugin)
    p._binary = ""
    p._cache = cache
    p._cache_at = time.time() if cache else 0.0
    p._cache_ttl = 30.0
    import asyncio
    p._probe_lock = asyncio.Lock()
    p._config = {}
    return p


class TestTailscalePluginLogic:
    def test_resolve_binary_honors_override(self, plugin_module, tmp_path):
        fake = tmp_path / "fake_tailscale"
        fake.write_text("")
        resolved = plugin_module.TailscalePlugin._resolve_binary(str(fake))
        assert resolved == str(fake)

    def test_resolve_binary_missing_override_falls_through(self, plugin_module):
        # Non-existent override path → method probes the candidate list.
        # Result depends on the test machine; we only assert it doesn't
        # erroneously return the bogus override.
        result = plugin_module.TailscalePlugin._resolve_binary("/no/such/path")
        assert result != "/no/such/path"

    @pytest.mark.asyncio
    async def test_is_peer_matches_self_ip(self, plugin_module):
        p = _make_plugin(plugin_module, cache={
            "BackendState": "Running",
            "Self": {"TailscaleIPs": ["100.1.1.1"]},
            "Peer": {},
        })
        assert await p.is_peer("100.1.1.1") is True
        assert await p.is_peer("100.2.2.2") is False
        assert await p.is_peer("") is False

    @pytest.mark.asyncio
    async def test_is_peer_matches_peer_ip(self, plugin_module):
        p = _make_plugin(plugin_module, cache={
            "BackendState": "Running",
            "Self": {"TailscaleIPs": ["100.1.1.1"]},
            "Peer": {
                "nodekey1": {"TailscaleIPs": ["100.5.5.5", "fd7a::1"], "Online": True},
            },
        })
        assert await p.is_peer("100.5.5.5") is True
        assert await p.is_peer("fd7a::1") is True
        assert await p.is_peer("100.6.6.6") is False

    @pytest.mark.asyncio
    async def test_peers_sorts_online_first(self, plugin_module):
        p = _make_plugin(plugin_module, cache={
            "BackendState": "Running",
            "Self": {"TailscaleIPs": ["100.1.1.1"]},
            "Peer": {
                "k1": {"HostName": "z-offline", "TailscaleIPs": ["100.2.2.2"], "Online": False, "OS": "linux"},
                "k2": {"HostName": "a-online",  "TailscaleIPs": ["100.3.3.3"], "Online": True,  "OS": "windows"},
                "k3": {"HostName": "b-online",  "TailscaleIPs": ["100.4.4.4"], "Online": True,  "OS": "macOS"},
            },
        })
        peers = await p.peers()
        assert [pr["name"] for pr in peers] == ["a-online", "b-online", "z-offline"]
        assert peers[0]["online"] is True
        assert peers[-1]["online"] is False

    @pytest.mark.asyncio
    async def test_peers_excludes_offline_when_requested(self, plugin_module):
        p = _make_plugin(plugin_module, cache={
            "BackendState": "Running",
            "Self": {"TailscaleIPs": []},
            "Peer": {
                "k1": {"HostName": "off", "TailscaleIPs": ["100.2.2.2"], "Online": False},
                "k2": {"HostName": "on",  "TailscaleIPs": ["100.3.3.3"], "Online": True},
            },
        })
        peers = await p.peers(include_offline=False)
        assert [pr["name"] for pr in peers] == ["on"]

    @pytest.mark.asyncio
    async def test_funnel_enabled_new_shape(self, plugin_module):
        p = _make_plugin(plugin_module, cache={
            "BackendState": "Running",
            "CurrentTailnet": {"FunnelEnabled": True},
            "Self": {},
        })
        assert await p.funnel_enabled() is True

    @pytest.mark.asyncio
    async def test_funnel_enabled_old_shape(self, plugin_module):
        p = _make_plugin(plugin_module, cache={
            "BackendState": "Running",
            "CurrentTailnet": {},
            "Self": {"CapMap": {"https://tailscale.com/cap/funnel": {}}},
        })
        assert await p.funnel_enabled() is True

    @pytest.mark.asyncio
    async def test_funnel_enabled_default_false(self, plugin_module):
        p = _make_plugin(plugin_module, cache={
            "BackendState": "Running",
            "Self": {},
        })
        assert await p.funnel_enabled() is False

    @pytest.mark.asyncio
    async def test_available_false_when_no_binary(self, plugin_module):
        p = _make_plugin(plugin_module)
        p._binary = ""
        assert await p.available() is False


# ── /api/tailnet endpoint (requires live daemon) ──────────────────────────


@pytest.mark.api
class TestTailnetAPI:
    def test_endpoint_returns_json(self, http_client):
        data = assert_ok(http_client.get("/api/tailnet"))
        assert "available" in data
        assert isinstance(data["available"], bool)

    def test_endpoint_never_raises(self, http_client):
        """Even if tailscale plugin/CLI is missing, endpoint must answer 200."""
        r = http_client.get("/api/tailnet")
        assert r.status_code == 200
        d = r.json()
        if not d["available"]:
            assert "reason" in d, "unavailable response must carry a reason"

    def test_shape_when_available(self, http_client):
        d = http_client.get("/api/tailnet").json()
        if not d.get("available"):
            pytest.skip(f"tailscale unavailable: {d.get('reason', '?')}")
        # Shape contract
        assert "status" in d and "identity" in d and "peers" in d
        s = d["status"]
        assert "self_ip" in s and "self_name" in s and "peer_count" in s
        assert isinstance(d["peers"], list)
        assert isinstance(d["funnel_enabled"], bool)


# ── Audit middleware (requires live daemon) ───────────────────────────────


def _wait_for_syslog_entry(http_client, source: str, contains: str, *, timeout_s: float = 3.0):
    """Poll /api/syslog for an entry from `source` whose message contains
    `contains`. Returns the entry dict, or None on timeout."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = http_client.get(f"/api/syslog?source={source}&limit=20")
        if r.status_code == 200:
            for entry in r.json():
                if contains in (entry.get("message") or ""):
                    return entry
        time.sleep(0.2)
    return None


@pytest.mark.api
class TestAuditMiddleware:
    def test_bad_token_logs_audit_entry(self, http_client):
        """Unauth request to a non-exempt path writes a syslog `auth` entry."""
        import httpx
        # Unique path so this test's entry doesn't collide with the 30s
        # throttle from another test run / previous probe.
        probe_path = "/api/capabilities/full"
        with httpx.Client(base_url=str(http_client.base_url), timeout=5) as bad:
            r = bad.get(probe_path, headers={"Authorization": "Bearer DEFINITELY_NOT_VALID"})
            assert r.status_code == 401

        entry = _wait_for_syslog_entry(http_client, "auth", probe_path)
        assert entry is not None, "expected syslog auth entry for bad-token request"
        assert entry["level"] == "warn"
        assert "unauthenticated" in entry["message"]

    def test_tailnet_label_resolves_when_peer_known(self, http_client):
        """When a tailnet peer IP arrives via X-Forwarded-For, audit message
        carries the peer name. Skipped when no peers are known to the plugin."""
        ts_data = http_client.get("/api/tailnet").json()
        if not ts_data.get("available"):
            pytest.skip("tailscale not available on this host")
        peers = ts_data.get("peers") or []
        if not peers:
            pytest.skip("no tailnet peers known — can't exercise label path")
        peer = peers[0]
        peer_ip = peer.get("ip") or ""
        peer_name = peer.get("name") or ""
        if not peer_ip or not peer_name:
            pytest.skip("peer record missing ip/name")

        import httpx
        # Unique path so we get past the 30s (ip, path-prefix) throttle even
        # if a prior run hit the same peer. Throttle bucket is the second
        # segment of the path, so vary the first /api/ segment via query.
        probe_path = f"/api/syslog?_probe={int(time.time())}"
        # Hit a protected endpoint without auth, claiming to be the peer.
        with httpx.Client(base_url=str(http_client.base_url), timeout=5) as bad:
            bad.get(
                probe_path,
                headers={
                    "Authorization": "Bearer NOPE",
                    "X-Forwarded-For": peer_ip,
                },
            )

        entry = _wait_for_syslog_entry(http_client, "auth", peer_ip)
        assert entry is not None, f"expected audit entry mentioning peer IP {peer_ip}"
        assert peer_name in entry["message"], (
            f"expected tailnet peer label '{peer_name}' in audit message; got: {entry['message']!r}"
        )
