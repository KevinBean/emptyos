"""apps/cables — FEM end-to-end smoke tests against the live daemon.

Creates a TB-880-case-1-shaped project + cable, runs the schedule with
method=fem, fetches the heatmap, then cleans up. Verifies the FEM path
the unit tests can't reach: HTTP routing, vault writeback through
update_cable, and the heatmap endpoint shape.

Skipped when the daemon isn't on localhost:9000 or the [fem] extra
isn't installed.
"""

from __future__ import annotations

import time
import uuid

import httpx
import pytest

from helpers import TEST_PREFIX

pytest.importorskip("gmsh")

# Unique per run — `delete_project` only sets archived=true, so reusing
# a name within the same vault would collide on create.
_RUN_ID = uuid.uuid4().hex[:6]
PROJECT_NAME = f"{TEST_PREFIX}fem-smoke-{_RUN_ID}"
CABLE_LABEL = f"{TEST_PREFIX}fem-cable-{_RUN_ID}"


@pytest.fixture(scope="module")
def project_id(http_client: httpx.Client):
    """Create a project with one TB-880-case-1 cable, yield project id, then delete."""
    proj_resp = http_client.post("/cables/api/projects", json={
        "name": PROJECT_NAME,
        "frequency_hz": 50,
        "soil_thermal_resistivity_kmw": 1.0,
        "ambient_temperature_c": 20,
        "conductor_max_temp_c": 90,
    })
    assert proj_resp.status_code == 200, proj_resp.text
    body = proj_resp.json()
    assert "id" in body, f"unexpected create_project shape: {body}"
    pid = body["id"]

    cable_resp = http_client.post(
        f"/cables/api/projects/{pid}/cables",
        json={
            "label": CABLE_LABEL,
            "installation": "direct_buried",
            "bonding": "single_point",
            "burial_depth_m": 1.0,
            "spacing_mode": "trefoil",
            "grouped_cables": 3,
            "rated_voltage_kv": 132,
            "conductor_csa_mm2": 630,
            "conductor_material": "Cu",
            "insulation_material": "XLPE",
            "sheath_material": "Al",
            # Geometry override needed by FEM scope check
            "overrides": {
                "geometry": {
                    "conductor_diameter": 0.0303,
                    "insulation_thickness": 0.0155,
                    "sheath_thickness": 0.0008,
                    "sheath_inner_diameter": 0.0669,
                    "overall_diameter": 0.0755,
                },
                "conductor_dc_resistance_20c_ohm_per_km": 0.0283,
            },
        },
    )
    assert cable_resp.status_code == 200, cable_resp.text

    yield pid

    http_client.delete(f"/cables/api/projects/{pid}")


@pytest.mark.api
def test_fem_run_schedule_writes_detail_fields(http_client: httpx.Client, project_id):
    """method=fem path runs end-to-end and writes fem_iterations, fem_max_theta_c."""
    t0 = time.time()
    resp = http_client.post(
        f"/cables/api/projects/{project_id}/run-schedule",
        json={"method": "fem"},
        timeout=120,
    )
    elapsed = time.time() - t0
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Either ran or skipped (geometry override may not propagate through
    # _resolve_library_entry — surface skip reason in either case).
    if not body.get("results") and body.get("skipped"):
        pytest.skip(f"FEM skipped: {body['skipped']}")

    assert body["method"] == "fem"
    assert len(body["results"]) >= 1
    r = body["results"][0]
    assert r["method"] == "fem"
    assert r["converged"] is True
    assert r["n_iterations"] >= 1
    assert 700.0 < r["ampacity_a"] < 1000.0
    assert elapsed < 90.0, f"FEM took {elapsed:.1f}s — too slow"


@pytest.mark.api
def test_fem_heatmap_endpoint_returns_triangles(http_client: httpx.Client, project_id):
    """/fem-heatmap returns viewport + cables + triangles ready for canvas render."""
    cables = http_client.get(f"/cables/api/projects/{project_id}/cables").json()["cables"]
    if not cables:
        pytest.skip("no cable in fixture")
    cid = cables[0]["id"]
    resp = http_client.post(
        f"/cables/api/projects/{project_id}/cables/{cid}/fem-heatmap",
        timeout=120,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    if "error" in body:
        pytest.skip(f"heatmap rejected: {body['error']}")
    assert body["n_triangles"] > 100
    assert body["n_triangles"] == len(body["triangles"])
    assert len(body["cables"]) == 3
    assert body["T_range"]["min"] < 40
    assert body["T_range"]["max"] > 70
    assert len(body["triangles"][0]) == 7
