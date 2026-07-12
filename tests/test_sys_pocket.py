"""System app tests: Pocket Control Plane — mobile-narrow review aggregator.

Pocket holds NO run/pending state and duplicates NO gate logic. It contributes
only two backend routes of its own — the dark-default flag gate and a read-only
Tailscale reachability summary — and the frontend points at the *existing*
run-center + rooms-pending surfaces (each keeping its own review gate). These
tests pin that thin-aggregator contract: the two owned routes behave, the app
does NOT expose its own run/apply routes, and the page shell + dark-default gate
render. Pocket is read-only, so no fixtures are created and nothing is cleaned up.
"""

import pytest

from helpers import assert_dict_response
from page_helpers import assert_no_js_errors, wait_briefly

MOUNT_IDS = [
    "pocket-live", "pocket-banner", "pocket-tailnet",
    "pocket-gate", "pocket-runs", "pocket-pending",
]


@pytest.mark.api
class TestPocketAPI:
    # ── Owned route: dark-default flag gate ──────────────────────────────
    def test_config_envelope(self, http_client):
        data = assert_dict_response(http_client.get("/pocket/api/config"))
        assert "enabled" in data, f"config missing 'enabled': {list(data.keys())}"

    def test_config_enabled_is_bool(self, http_client):
        data = http_client.get("/pocket/api/config").json()
        assert isinstance(data["enabled"], bool), f"enabled not bool: {data['enabled']!r}"

    # ── Owned route: read-only Tailscale reachability ────────────────────
    def test_tailnet_envelope(self, http_client):
        data = assert_dict_response(http_client.get("/pocket/api/tailnet"))
        assert "available" in data, f"tailnet missing 'available': {list(data.keys())}"
        assert isinstance(data["available"], bool)

    def test_tailnet_available_shape(self, http_client):
        data = http_client.get("/pocket/api/tailnet").json()
        if not data.get("available"):
            pytest.skip("tailscale not available in this environment")
        for key in ("self_name", "self_ip", "peer_count", "online_peer_count", "peers"):
            assert key in data, f"available tailnet missing {key}: {list(data.keys())}"
        assert isinstance(data["peers"], list)

    def test_tailnet_peer_rows_normalized(self, http_client):
        data = http_client.get("/pocket/api/tailnet").json()
        peers = data.get("peers") or []
        if not peers:
            pytest.skip("no tailnet peers in this environment")
        for p in peers[:20]:
            for key in ("name", "ip", "online", "os"):
                assert key in p, f"peer row missing {key}: {list(p.keys())}"
            assert isinstance(p["online"], bool)

    def test_tailnet_unavailable_has_reason(self, http_client):
        data = http_client.get("/pocket/api/tailnet").json()
        if data.get("available"):
            pytest.skip("tailscale available — soft-fail path not exercised")
        assert data.get("reason"), "unavailable tailnet must carry a 'reason' for the strip"

    # ── Thin-aggregator contract: owns no run/pending state ──────────────
    def test_no_own_runs_route(self, http_client):
        # Pocket reads run-center's feed via the frontend; it exposes no run route.
        resp = http_client.get("/pocket/api/runs")
        assert resp.status_code in (404, 405), (
            f"pocket should NOT own /api/runs (thin aggregator); got {resp.status_code}"
        )

    def test_no_own_apply_route(self, http_client):
        # Apply/reject go to rooms' gated endpoint — pocket duplicates no gate.
        resp = http_client.post("/pocket/api/pending/PLAYWRIGHT-TEST-bogus/apply")
        assert resp.status_code in (404, 405), (
            f"pocket should NOT own a pending-apply route; got {resp.status_code}"
        )

    def test_aggregation_sources_reachable(self, http_client):
        # The surfaces the page points at must exist (review gates live there).
        assert http_client.get("/run-center/api/runs").status_code == 200
        assert http_client.get("/rooms/api/pending").status_code == 200

    # ── PWA identity: dedicated manifest scopes install to /pocket/ ──────
    def test_manifest_scoped_to_pocket(self, http_client):
        resp = http_client.get("/pocket/manifest.webmanifest")
        assert resp.status_code == 200, "pocket manifest route missing"
        assert "application/manifest+json" in resp.headers.get("content-type", ""), (
            "manifest must use application/manifest+json (iOS prefers it)"
        )
        m = resp.json()
        # The whole point: launch /pocket/, not the global hub start_url "/".
        assert m.get("start_url") == "/pocket/", f"start_url not scoped: {m.get('start_url')}"
        assert m.get("scope") == "/pocket/", f"scope not /pocket/: {m.get('scope')}"
        assert m.get("short_name") == "Pocket"
        assert isinstance(m.get("icons"), list) and m["icons"], "manifest needs icons"


@pytest.mark.interactive
class TestPocketUI:
    def test_ui_page_loads(self, page, base_url, page_errors):
        resp = page.goto(base_url + "/pocket/", wait_until="domcontentloaded", timeout=15000)
        assert resp.status == 200
        wait_briefly(page, 1200)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_mount_points_present(self, page, base_url, page_errors):
        page.goto(base_url + "/pocket/", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 1000)
        for el_id in MOUNT_IDS:
            assert page.locator("#" + el_id).count() == 1, f"missing mount point #{el_id}"
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_gate_matches_config(self, page, base_url, page_errors):
        page.goto(base_url + "/pocket/", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 1500)
        # The dark-default gate's visibility must follow /pocket/api/config.enabled:
        # gated off => #pocket-gate shown; enabled => hidden.
        enabled = page.evaluate(
            "() => fetch('/pocket/api/config').then(r => r.json()).then(d => !!d.enabled)"
        )
        gate_shown = page.evaluate(
            "() => { var g = document.getElementById('pocket-gate');"
            " return !!g && getComputedStyle(g).display !== 'none'; }"
        )
        assert gate_shown == (not enabled), (
            f"gate visibility ({gate_shown}) does not match config.enabled ({enabled})"
        )
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])
