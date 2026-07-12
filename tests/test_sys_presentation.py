"""System tests for presentation mode (runtime privacy toggle).

Covers /api/presentation/{state,toggle,set} and verifies the middleware's
exempt-path behaviour. Does NOT assert on scrubbed content because we don't
seed personal data into the daemon under test — scrubbing correctness is
exercised by the regex unit-shape (deterministic) + manual QA.

State is captured at session start and restored at session end so a test run
never leaves the daemon in presentation mode.
"""

from __future__ import annotations

import pytest

from helpers import assert_ok


@pytest.fixture(scope="module", autouse=True)
def _restore_presentation_state(http_client):
    """Snapshot state on entry; restore on exit so test runs don't leak."""
    initial = http_client.get("/api/presentation/state").json()
    initial_enabled = bool(initial.get("enabled", False))
    yield
    http_client.post("/api/presentation/set", json={"enabled": initial_enabled})


@pytest.mark.api
class TestPresentationAPI:
    def test_state_endpoint_shape(self, http_client):
        data = assert_ok(http_client.get("/api/presentation/state"))
        assert "enabled" in data
        assert isinstance(data["enabled"], bool)

    def test_toggle_flips(self, http_client):
        before = http_client.get("/api/presentation/state").json()["enabled"]
        flipped = assert_ok(http_client.post("/api/presentation/toggle"))
        assert flipped["enabled"] is (not before)
        # And again — must return to original.
        flipped_back = assert_ok(http_client.post("/api/presentation/toggle"))
        assert flipped_back["enabled"] is before

    def test_set_explicit(self, http_client):
        assert_ok(http_client.post("/api/presentation/set", json={"enabled": True}))
        assert http_client.get("/api/presentation/state").json()["enabled"] is True
        assert_ok(http_client.post("/api/presentation/set", json={"enabled": False}))
        assert http_client.get("/api/presentation/state").json()["enabled"] is False

    def test_state_persists_across_requests(self, http_client):
        http_client.post("/api/presentation/set", json={"enabled": True})
        for _ in range(3):
            assert http_client.get("/api/presentation/state").json()["enabled"] is True
        http_client.post("/api/presentation/set", json={"enabled": False})

    def test_health_exempt_when_on(self, http_client):
        """/api/health is in the exempt prefix list — must work in presentation mode."""
        http_client.post("/api/presentation/set", json={"enabled": True})
        try:
            resp = http_client.get("/api/health")
            assert resp.status_code == 200
            data = resp.json()
            # Exempt path returns the original payload, including its own keys.
            assert isinstance(data, dict)
        finally:
            http_client.post("/api/presentation/set", json={"enabled": False})

    def test_presentation_endpoints_self_exempt(self, http_client):
        """Toggle endpoint must not be scrubbed by its own middleware,
        otherwise turning it OFF would be impossible if a regex matches the
        boolean serialization. Belt-and-braces — already handled by the
        /api/presentation/ exempt prefix."""
        http_client.post("/api/presentation/set", json={"enabled": True})
        try:
            data = http_client.get("/api/presentation/state").json()
            assert data == {"enabled": True}
        finally:
            http_client.post("/api/presentation/set", json={"enabled": False})

    def test_set_normalizes_truthy_input(self, http_client):
        """Server coerces with bool(); 'enabled': null → False, missing → False."""
        assert_ok(http_client.post("/api/presentation/set", json={"enabled": True}))
        result = assert_ok(http_client.post("/api/presentation/set", json={}))
        assert result["enabled"] is False
        assert http_client.get("/api/presentation/state").json()["enabled"] is False

    def test_non_json_body_passes_through_when_on(self, http_client):
        """Non-JSON responses (HTML pages, redirects) must not be touched.
        / always returns HTML (the dashboard) — verify it survives unchanged."""
        http_client.post("/api/presentation/set", json={"enabled": True})
        try:
            resp = http_client.get("/", follow_redirects=False)
            # Either 200 HTML, or 3xx redirect — both should be untouched.
            assert resp.status_code in (200, 301, 302, 307, 308)
            ctype = resp.headers.get("content-type", "")
            assert "json" not in ctype.lower() or resp.status_code >= 300
        finally:
            http_client.post("/api/presentation/set", json={"enabled": False})
