"""System tests for /api/cloud/* (consent gate), /api/demo/*, and the auth
middleware login flow.

Covers the deployment-mode surface that ships in 0.1.0:
  - Cloud consent endpoints (status, pending, policy, consent submit)
  - Demo banner endpoint
  - Auth login page is reachable when the daemon has auth enabled

The auth middleware tests skip gracefully when the running daemon was started
without an auth token (the local-mode default).
"""

from __future__ import annotations

import pytest

from helpers import assert_dict_response, assert_ok


# ---------------------------------------------------------------------------
# /api/demo/status
# ---------------------------------------------------------------------------

@pytest.mark.api
class TestDemoStatus:
    def test_returns_expected_shape(self, http_client):
        data = assert_dict_response(
            http_client.get("/api/demo/status"),
            required_keys=["enabled", "banner", "install_url"],
        )
        assert isinstance(data["enabled"], bool)
        assert isinstance(data["banner"], str)
        assert data["install_url"].startswith("http")

    def test_banner_only_when_enabled(self, http_client):
        data = http_client.get("/api/demo/status").json()
        if data["enabled"]:
            assert data["banner"], "demo banner must be set when enabled=true"
        else:
            assert data["banner"] == "", "banner must be empty when demo is off"


# ---------------------------------------------------------------------------
# /api/cloud/status + /api/cloud/pending
# ---------------------------------------------------------------------------

@pytest.mark.api
class TestCloudStatus:
    def test_status_enabled(self, http_client):
        data = assert_dict_response(http_client.get("/api/cloud/status"))
        assert data.get("enabled") is True, (
            "cloud_consent manager must be wired into the kernel"
        )
        # Shape check — these come from CloudConsentManager.status()
        for key in ("policy", "approved", "pending", "last_decisions"):
            assert key in data, f"missing field {key!r} in status response"
        assert data["policy"] in ("ask", "always", "never")
        assert isinstance(data["approved"], list)
        assert isinstance(data["pending"], list)
        assert isinstance(data["last_decisions"], dict)

    def test_pending_returns_list(self, http_client):
        data = assert_dict_response(http_client.get("/api/cloud/pending"))
        assert isinstance(data.get("pending"), list)


# ---------------------------------------------------------------------------
# /api/cloud/policy — runtime policy switch
# ---------------------------------------------------------------------------

@pytest.mark.api
class TestCloudPolicy:
    @pytest.fixture(autouse=True)
    def _restore(self, http_client):
        """Snapshot the policy and restore after each test."""
        original = http_client.get("/api/cloud/status").json().get("policy", "ask")
        yield
        http_client.post("/api/cloud/policy", json={"policy": original})

    def test_set_policy_always(self, http_client):
        data = assert_ok(http_client.post("/api/cloud/policy", json={"policy": "always"}))
        assert data.get("policy") == "always"
        # Verify it stuck
        assert http_client.get("/api/cloud/status").json()["policy"] == "always"

    def test_set_policy_never(self, http_client):
        data = assert_ok(http_client.post("/api/cloud/policy", json={"policy": "never"}))
        assert data.get("policy") == "never"

    def test_set_policy_ask(self, http_client):
        data = assert_ok(http_client.post("/api/cloud/policy", json={"policy": "ask"}))
        assert data.get("policy") == "ask"

    def test_invalid_policy_rejected(self, http_client):
        resp = http_client.post("/api/cloud/policy", json={"policy": "maybe"})
        assert resp.status_code == 400
        assert "policy" in resp.text.lower()

    def test_missing_policy_rejected(self, http_client):
        resp = http_client.post("/api/cloud/policy", json={})
        assert resp.status_code == 400

    def test_invalid_json_rejected(self, http_client):
        resp = http_client.post(
            "/api/cloud/policy",
            content=b"not json",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# /api/cloud/consent — approve/deny submission
# ---------------------------------------------------------------------------

@pytest.mark.api
class TestCloudConsentSubmit:
    def test_missing_id_rejected(self, http_client):
        resp = http_client.post(
            "/api/cloud/consent",
            json={"approved": True, "remember": False},
        )
        assert resp.status_code == 400
        assert "id" in resp.text.lower()

    def test_unknown_id_returns_404(self, http_client):
        resp = http_client.post(
            "/api/cloud/consent",
            json={"id": "nonexistent-request-id-xyz", "approved": True},
        )
        assert resp.status_code == 404

    def test_invalid_json_rejected(self, http_client):
        resp = http_client.post(
            "/api/cloud/consent",
            content=b"not json",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Auth middleware — tests gated on whether the daemon was started with a token
# ---------------------------------------------------------------------------

def _auth_enabled(http_client) -> bool:
    """True if the running daemon enforces auth (login page exists)."""
    resp = http_client.get("/login", follow_redirects=False)
    return resp.status_code == 200 and "EmptyOS" in resp.text


@pytest.mark.api
class TestAuthMiddleware:
    def test_health_always_open(self, http_client):
        """/api/health must bypass auth in every mode — Docker healthchecks rely on it."""
        resp = http_client.get("/api/health", follow_redirects=False)
        assert resp.status_code == 200

    def test_login_page_present_when_auth_enabled(self, http_client):
        if not _auth_enabled(http_client):
            pytest.skip("daemon running without auth_token (local mode)")
        resp = http_client.get("/login")
        assert resp.status_code == 200
        assert "<form" in resp.text
        assert "token" in resp.text.lower()

    def test_login_redirects_with_invalid_token(self, http_client):
        if not _auth_enabled(http_client):
            pytest.skip("daemon running without auth_token (local mode)")
        resp = http_client.post(
            "/login",
            data={"token": "definitely-wrong", "next": "/"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("location", "")
        assert "err" in resp.headers.get("location", "")

    def test_protected_api_returns_401_without_token(self, http_client):
        if not _auth_enabled(http_client):
            pytest.skip("daemon running without auth_token (local mode)")
        # Use a fresh client with no cookies inherited
        import httpx
        with httpx.Client(base_url=str(http_client.base_url), timeout=5) as fresh:
            resp = fresh.get("/api/apps")
            assert resp.status_code == 401

    def test_protected_page_redirects_to_login_without_token(self, http_client):
        if not _auth_enabled(http_client):
            pytest.skip("daemon running without auth_token (local mode)")
        import httpx
        with httpx.Client(base_url=str(http_client.base_url), timeout=5) as fresh:
            resp = fresh.get("/", follow_redirects=False)
            assert resp.status_code == 302
            assert "/login" in resp.headers.get("location", "")

    def test_static_assets_bypass_auth(self, http_client):
        """Login page CSS/JS must load even when the user isn't signed in."""
        if not _auth_enabled(http_client):
            pytest.skip("daemon running without auth_token (local mode)")
        import httpx
        with httpx.Client(base_url=str(http_client.base_url), timeout=5) as fresh:
            # Any /static/ path — the prefix is what matters; 404 is fine, 401 is not
            resp = fresh.get("/static/eos-components.css")
            assert resp.status_code != 401
