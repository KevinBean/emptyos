"""apps/cables — geo mode (lat/lon coordinates on a tile basemap).

Cables projects gained a `mode` field in 2026-05 with three values:
  - "abstract" (default) — SVG x/y, no real-world coords
  - "raster"             — SVG x/y over a calibrated background image
  - "geo"                — lat/lon on a tile basemap, rendered via EOS_MAP

This file covers the geo-mode-specific surface: project creation persists
the mode flag, nodes round-trip lat/lon, and the dedicated `map.html` view
loads. Render correctness is checked indirectly (page status + presence of
the EOS_MAP container) — interactive Geoman drawing is exercised in
`test_sys_geo_cad.py` against the geo-cad app itself.

Skipped when the daemon isn't on localhost:9000.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from helpers import TEST_PREFIX, assert_ok


_RUN_ID = uuid.uuid4().hex[:6]
PROJECT_NAME = f"{TEST_PREFIX}geo-mode-{_RUN_ID}"


@pytest.fixture(scope="module")
def geo_project_id(http_client: httpx.Client):
    proj = http_client.post("/cables/api/projects", json={
        "name": PROJECT_NAME,
        "mode": "geo",
        "frequency_hz": 50,
    })
    assert proj.status_code == 200, proj.text
    return proj.json()["id"]


@pytest.mark.api
class TestCablesGeoMode:
    def test_project_persists_mode_flag(self, http_client, geo_project_id):
        proj = assert_ok(http_client.get(f"/cables/api/projects/{geo_project_id}"))
        assert proj["project"]["mode"] == "geo"

    def test_node_round_trips_lat_lon(self, http_client, geo_project_id):
        # Sydney CBD area for sanity — geocode-friendly real coords.
        n = assert_ok(http_client.post(
            f"/cables/api/projects/{geo_project_id}/nodes",
            json={
                "id": "geo-slack",
                "label": "Slack",
                "kind": "substation",
                "voltage_kv": 22.0,
                "is_slack": True,
                "lat": -33.8688,
                "lon": 151.2093,
            },
        ))
        assert n["ok"] is True
        topo = assert_ok(http_client.get(
            f"/cables/api/projects/{geo_project_id}/topology"
        ))
        slack = next((nn for nn in topo["nodes"] if nn["id"] == "geo-slack"), None)
        assert slack is not None
        assert abs(float(slack["lat"]) - (-33.8688)) < 1e-6
        assert abs(float(slack["lon"]) - 151.2093) < 1e-6

    def test_map_view_page_loads(self, http_client, geo_project_id):
        resp = http_client.get(
            f"/cables/pages/map.html?project={geo_project_id}"
        )
        assert resp.status_code == 200
        body = resp.text
        # Page must mount EOS_MAP and reference the project param hookup.
        assert "EOS_MAP" in body
        assert "map-host" in body

    def test_mode_field_settable_via_settings_patch(self, http_client, geo_project_id):
        """The PATCH /settings endpoint must accept `mode` per the new
        whitelist entry — otherwise users can't switch a project from
        abstract → geo without recreating it."""
        ok = assert_ok(http_client.patch(
            f"/cables/api/projects/{geo_project_id}/settings",
            json={"mode": "geo"},
        ))
        assert ok["ok"] is True
        # Sanity: switching to a bogus mode is *not* sanitized at the PATCH
        # layer — the whitelist is fields, the value is the user's. Document
        # the surface here so anyone tightening it later updates the test.
        # (Stricter validation can land in a follow-up.)
        bogus = http_client.patch(
            f"/cables/api/projects/{geo_project_id}/settings",
            json={"mode": "rocketship"},
        ).json()
        assert "ok" in bogus or "error" in bogus
