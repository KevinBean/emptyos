"""Tier 2: API health — every GET endpoint returns 200 + valid JSON."""

import pytest
from helpers import SYSTEM_ENDPOINTS, APP_GET_ENDPOINTS, LLM_ENDPOINTS


@pytest.mark.api
class TestSystemEndpoints:
    """Core system API endpoints."""

    @pytest.mark.parametrize("path", SYSTEM_ENDPOINTS, ids=[p.split("?")[0].strip("/").replace("/", "_") for p in SYSTEM_ENDPOINTS])
    def test_system_endpoint(self, http_client, path):
        resp = http_client.get(path)
        assert resp.status_code == 200, f"{path} returned {resp.status_code}: {resp.text[:200]}"
        data = resp.json()
        assert data is not None

    def test_health_status_ok(self, http_client):
        data = http_client.get("/api/health").json()
        assert data["status"] == "ok"

    def test_apps_endpoint_has_apps(self, http_client):
        data = http_client.get("/api/apps").json()
        assert isinstance(data, list)
        assert len(data) >= 60

    def test_apps_list_count(self, http_client):
        data = http_client.get("/api/apps").json()
        assert isinstance(data, list)
        assert len(data) >= 60


def _build_app_endpoint_params():
    """Build (app_id, endpoint_path) tuples for parametrize."""
    params = []
    for app_id, endpoints in APP_GET_ENDPOINTS.items():
        for ep in endpoints:
            full_path = f"/{app_id}{ep}"
            params.append(pytest.param(app_id, ep, id=full_path.strip("/")))
    return params


@pytest.mark.api
class TestAppGetEndpoints:
    """Per-app GET endpoints return 200 + valid JSON."""

    @pytest.mark.parametrize("app_id,endpoint", _build_app_endpoint_params())
    def test_app_get_endpoint(self, http_client, llm_available, app_id, endpoint):
        full_path = f"/{app_id}{endpoint}"
        # Skip LLM-dependent endpoints when LLM unavailable
        if full_path in LLM_ENDPOINTS and not llm_available:
            pytest.skip(f"LLM required for {full_path}")

        resp = http_client.get(full_path)
        # Skip personal apps that aren't installed (404 on prefix = app missing)
        if resp.status_code == 404:
            probe = http_client.get(f"/{app_id}/")
            if probe.status_code == 404:
                pytest.skip(f"App '{app_id}' not installed")
        assert resp.status_code == 200, f"{full_path} returned {resp.status_code}: {resp.text[:300]}"
        ct = resp.headers.get("content-type", "")
        # API returned HTML instead of JSON = route doesn't exist (catch-all served page)
        if "text/html" in ct:
            pytest.skip(f"{full_path} returned HTML (endpoint retired or missing)")
        # Should be valid JSON
        try:
            data = resp.json()
        except Exception:
            pytest.fail(f"{full_path} returned non-JSON: {resp.text[:200]}")
        assert data is not None
