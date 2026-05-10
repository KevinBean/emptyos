"""System tests for the Soil app (apps/soil/).

API smoke tests + a deeper user-story test that runs the RS_TUT1 reference
case end-to-end through the daemon.

Daemon must be running on localhost:9000.
"""

import time

import pytest

from helpers import TEST_PREFIX


def _uniq(stem: str) -> str:
    return f"{TEST_PREFIX}{stem}-{int(time.time() * 1000)}"


@pytest.mark.api
class TestSoilAPI:
    def test_example_endpoint_returns_reference_dataset(self, http_client):
        r = http_client.get("/soil/api/example")
        assert r.status_code == 200
        data = r.json()
        assert data["site"] == "East Central Substation"
        assert len(data["spacings_m"]) == 5
        assert data["spacings_m"][0] == 2.0
        assert data["rho_a_ohm_m"][0] == 190.0

    def test_forward_uniform_soil_returns_rho(self, http_client):
        r = http_client.post("/soil/api/forward", json={
            "resistivities": [100.0],
            "thicknesses": [],
            "spacings_m": [1.0, 2.0, 4.0, 8.0],
        })
        assert r.status_code == 200
        data = r.json()
        for v in data["rho_a_ohm_m"]:
            assert v == pytest.approx(100.0, rel=1e-9)

    def test_invert_reference_case_recovers_layers(self, http_client):
        body = {
            "spacings_m": [2.0, 4.0, 8.0, 16.0, 32.0],
            "rho_a_ohm_m": [190.0, 183.0, 147.0, 118.0, 107.0],
            "n_layers": 2,
        }
        r = http_client.post("/soil/api/invert", json=body, timeout=30)
        assert r.status_code == 200
        data = r.json()
        assert "soil_model" in data
        layers = data["soil_model"]["layers"]
        assert len(layers) == 2
        assert layers[0]["rho_ohm_m"] == pytest.approx(190.0, rel=0.05)
        assert layers[1]["rho_ohm_m"] == pytest.approx(105.5163, rel=0.05)
        assert layers[0]["thickness_m"] == pytest.approx(4.733190, rel=0.05)
        assert layers[1]["thickness_m"] is None
        assert data["rms_error_pct"] <= 2.0
        assert "jacobian" in data
        assert data["jacobian"]["is_well_conditioned"] is True
        assert "fit_curve" in data
        assert len(data["fit_curve"]["spacings_m"]) > 10

    def test_invert_over_parameterised_flagged_ill_conditioned(self, http_client):
        """5 measurements forced to 3 layers — should be flagged."""
        body = {
            "spacings_m": [2.0, 4.0, 8.0, 16.0, 32.0],
            "rho_a_ohm_m": [190.0, 183.0, 147.0, 118.0, 107.0],
            "n_layers": 3,
        }
        r = http_client.post("/soil/api/invert", json=body, timeout=30)
        assert r.status_code == 200
        data = r.json()
        assert ("jacobian" in data and not data["jacobian"]["is_well_conditioned"]) or any(
            "unresolved" in w.lower() or "condition" in w.lower() for w in data.get("warnings", [])
        )

    def test_invert_rejects_under_determined(self, http_client):
        body = {
            "spacings_m": [2.0, 4.0],
            "rho_a_ohm_m": [190.0, 183.0],
            "n_layers": 2,
        }
        r = http_client.post("/soil/api/invert", json=body)
        assert r.status_code == 200
        data = r.json()
        assert "error" in data

    def test_project_round_trip_with_soundings(self, http_client):
        """Create project → save 5 readings → read back → run inversion via project data → delete."""
        name = _uniq("soil-roundtrip")
        pid = None
        try:
            create = http_client.post("/soil/api/projects", json={"name": name}).json()
            assert "error" not in create, create
            pid = create.get("id") or create.get("project_id")
            assert pid

            readings = [
                {"a": 2.0, "rho_a": 190.0, "active": True, "comment": ""},
                {"a": 4.0, "rho_a": 183.0, "active": True, "comment": ""},
                {"a": 8.0, "rho_a": 147.0, "active": True, "comment": ""},
                {"a": 16.0, "rho_a": 118.0, "active": True, "comment": ""},
                {"a": 32.0, "rho_a": 107.0, "active": True, "comment": ""},
            ]
            put = http_client.put(
                f"/soil/api/projects/{pid}/soundings",
                json={"readings": readings},
            ).json()
            assert put.get("ok") is True
            assert put.get("n") == 5

            got = http_client.get(f"/soil/api/projects/{pid}/soundings").json()
            assert got.get("ok") is True
            assert len(got["readings"]) == 5
            assert got["readings"][0]["active"] is True
            assert "comment" in got["readings"][0]

            inv = http_client.post("/soil/api/invert", json={
                "spacings_m": [r["a"] for r in readings],
                "rho_a_ohm_m": [r["rho_a"] for r in readings],
                "n_layers": 2,
            }, timeout=30).json()
            assert "soil_model" in inv

            patch = http_client.patch(
                f"/soil/api/projects/{pid}/settings",
                json={"last_rms_pct": inv["rms_error_pct"], "n_layers": 2},
            ).json()
            assert "error" not in patch, patch

            proj = http_client.get(f"/soil/api/projects/{pid}").json()
            assert "project" in proj
            # vault_get_properties returns string values — cast at boundary
            assert float(proj["project"]["last_rms_pct"]) == pytest.approx(inv["rms_error_pct"], rel=1e-6)
        finally:
            if pid:
                http_client.delete(f"/soil/api/projects/{pid}")


@pytest.mark.interactive
class TestSoilUI:
    def test_index_page_loads(self, page):
        page.goto("http://localhost:9000/soil/")
        assert "Soil" in page.title()
        page.wait_for_selector("#meas-tbody tr", timeout=5000)
        rows = page.query_selector_all("#meas-tbody tr")
        assert len(rows) == 5

    def test_invert_button_runs_and_renders_diagnostics(self, page):
        page.goto("http://localhost:9000/soil/")
        page.wait_for_selector("#meas-tbody tr", timeout=5000)
        page.click("button:has-text('Invert')")
        page.wait_for_function(
            "document.getElementById('diagnostics-body').textContent.includes('cond')",
            timeout=15000,
        )
        diag = page.text_content("#diagnostics-body")
        assert "RMS" in diag
        assert "cond(J)" in diag
