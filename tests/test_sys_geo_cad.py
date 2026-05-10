"""System app tests: Geo-CAD — georeferenced layers + GeoJSON storage."""

from __future__ import annotations

import time

import pytest

from helpers import TEST_PREFIX, assert_ok


def _uniq(stem: str) -> str:
    return f"{TEST_PREFIX}{stem}-{int(time.time() * 1000)}"


_LINE_GEOM = {
    "type": "LineString",
    "coordinates": [[151.20, -33.86], [151.30, -33.91]],
}
_POINT_GEOM = {"type": "Point", "coordinates": [151.21, -33.87]}
_POLY_GEOM = {
    "type": "Polygon",
    "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
}


@pytest.mark.api
class TestGeoCadAPI:
    def test_app_page_loads(self, http_client):
        resp = http_client.get("/geo-cad/")
        assert resp.status_code == 200

    def test_create_layer_round_trip(self, http_client):
        title = _uniq("trunk-cables")
        created = assert_ok(http_client.post(
            "/geo-cad/api/layers",
            json={
                "title": title,
                "project_id": "test-inbox",
                "attribute_schema": {"voltage_kv": "number", "material": "string"},
            },
        ))
        assert created["ok"] is True
        lid = created["id"]
        fetched = assert_ok(http_client.get(f"/geo-cad/api/layers/{lid}"))
        meta = fetched["meta"]
        assert meta["title"] == title
        assert meta["project_id"] == "test-inbox"
        assert meta["attribute_schema"]["voltage_kv"] == "number"
        assert meta["feature_count"] == 0
        assert fetched["geojson"]["type"] == "FeatureCollection"
        assert fetched["geojson"]["features"] == []

    def test_create_rejects_empty_title(self, http_client):
        resp = http_client.post("/geo-cad/api/layers", json={"title": ""})
        assert "error" in resp.json()

    def test_list_contains_created_layer(self, http_client):
        title = _uniq("listed")
        lid = assert_ok(http_client.post(
            "/geo-cad/api/layers",
            json={"title": title, "project_id": "test-inbox"},
        ))["id"]
        listing = assert_ok(http_client.get("/geo-cad/api/layers"))
        match = next((l for l in listing["layers"] if l["id"] == lid), None)
        assert match is not None
        assert match["title"] == title

    def test_list_filtered_by_project_id(self, http_client):
        pid = _uniq("proj")
        lid = assert_ok(http_client.post(
            "/geo-cad/api/layers",
            json={"title": _uniq("scoped"), "project_id": pid},
        ))["id"]
        # Filter
        scoped = assert_ok(http_client.get(f"/geo-cad/api/layers?project_id={pid}"))
        ids = [l["id"] for l in scoped["layers"]]
        assert lid in ids
        # Negative — different project shouldn't include it
        other = assert_ok(http_client.get("/geo-cad/api/layers?project_id=does-not-exist"))
        assert lid not in [l["id"] for l in other["layers"]]

    def test_add_feature_appends_and_increments_count(self, http_client):
        lid = assert_ok(http_client.post(
            "/geo-cad/api/layers",
            json={"title": _uniq("featured"), "project_id": "test-inbox"},
        ))["id"]
        added = assert_ok(http_client.post(
            f"/geo-cad/api/layers/{lid}/features",
            json={"geometry": _LINE_GEOM, "properties": {"voltage_kv": 22}},
        ))
        assert added["ok"] is True
        fid = added["id"]
        layer = assert_ok(http_client.get(f"/geo-cad/api/layers/{lid}"))
        assert layer["meta"]["feature_count"] == 1
        feats = layer["geojson"]["features"]
        assert len(feats) == 1
        assert feats[0]["geometry"]["type"] == "LineString"
        assert feats[0]["properties"]["voltage_kv"] == 22
        assert feats[0]["properties"]["_fid"] == fid

    def test_add_feature_rejects_invalid_geometry(self, http_client):
        lid = assert_ok(http_client.post(
            "/geo-cad/api/layers",
            json={"title": _uniq("badgeo"), "project_id": "test-inbox"},
        ))["id"]
        # Wrong type
        bad = http_client.post(
            f"/geo-cad/api/layers/{lid}/features",
            json={"geometry": {"type": "Hexagon", "coordinates": []}},
        )
        assert "error" in bad.json()
        # Missing coordinates
        bad2 = http_client.post(
            f"/geo-cad/api/layers/{lid}/features",
            json={"geometry": {"type": "LineString"}},
        )
        assert "error" in bad2.json()

    def test_replace_geojson_swaps_full_collection(self, http_client):
        lid = assert_ok(http_client.post(
            "/geo-cad/api/layers",
            json={"title": _uniq("replaced"), "project_id": "test-inbox"},
        ))["id"]
        # Add one then replace with three
        assert_ok(http_client.post(
            f"/geo-cad/api/layers/{lid}/features",
            json={"geometry": _POINT_GEOM},
        ))
        replaced = assert_ok(http_client.post(
            f"/geo-cad/api/layers/{lid}/geojson",
            json={"features": [
                {"type": "Feature", "geometry": _POINT_GEOM, "properties": {"role": "a"}},
                {"type": "Feature", "geometry": _LINE_GEOM, "properties": {"role": "b"}},
                {"type": "Feature", "geometry": _POLY_GEOM, "properties": {"role": "c"}},
            ]},
        ))
        assert replaced["ok"] is True
        assert replaced["feature_count"] == 3
        layer = assert_ok(http_client.get(f"/geo-cad/api/layers/{lid}"))
        assert layer["meta"]["feature_count"] == 3
        assert {f["properties"]["role"] for f in layer["geojson"]["features"]} == {"a", "b", "c"}

    def test_update_feature_merges_properties(self, http_client):
        lid = assert_ok(http_client.post(
            "/geo-cad/api/layers",
            json={"title": _uniq("updated"), "project_id": "test-inbox"},
        ))["id"]
        fid = assert_ok(http_client.post(
            f"/geo-cad/api/layers/{lid}/features",
            json={"geometry": _LINE_GEOM, "properties": {"voltage_kv": 22, "material": "XLPE"}},
        ))["id"]
        assert_ok(http_client.patch(
            f"/geo-cad/api/layers/{lid}/features/{fid}",
            json={"properties": {"voltage_kv": 33}},
        ))
        layer = assert_ok(http_client.get(f"/geo-cad/api/layers/{lid}"))
        feat = layer["geojson"]["features"][0]
        assert feat["properties"]["voltage_kv"] == 33
        # Merge: material stays
        assert feat["properties"]["material"] == "XLPE"

    def test_delete_feature_drops_it_from_collection(self, http_client):
        lid = assert_ok(http_client.post(
            "/geo-cad/api/layers",
            json={"title": _uniq("deletable"), "project_id": "test-inbox"},
        ))["id"]
        fid = assert_ok(http_client.post(
            f"/geo-cad/api/layers/{lid}/features",
            json={"geometry": _POINT_GEOM},
        ))["id"]
        assert_ok(http_client.delete(f"/geo-cad/api/layers/{lid}/features/{fid}"))
        layer = assert_ok(http_client.get(f"/geo-cad/api/layers/{lid}"))
        assert layer["meta"]["feature_count"] == 0
        assert layer["geojson"]["features"] == []

    def test_export_geojson_works_without_gdal(self, http_client):
        lid = assert_ok(http_client.post(
            "/geo-cad/api/layers",
            json={"title": _uniq("exported"), "project_id": "test-inbox"},
        ))["id"]
        assert_ok(http_client.post(
            f"/geo-cad/api/layers/{lid}/features",
            json={"geometry": _LINE_GEOM, "properties": {"voltage_kv": 11}},
        ))
        res = assert_ok(http_client.post(
            "/geo-cad/api/export",
            json={"layer_id": lid, "format": "geojson"},
        ))
        assert res["ok"] is True
        assert res["format"] == "geojson"
        assert "FeatureCollection" in res["content"]
        assert res["filename"].endswith(".geojson")

    def test_export_dxf_gates_on_gdal(self, http_client):
        """Without GDAL plugin, DXF export must fail cleanly with a recovery
        hint — never silently succeed or 500."""
        lid = assert_ok(http_client.post(
            "/geo-cad/api/layers",
            json={"title": _uniq("gdal-gated"), "project_id": "test-inbox"},
        ))["id"]
        gdal_status = http_client.get("/geo-cad/api/gdal-status").json()
        if gdal_status.get("available"):
            pytest.skip("GDAL plugin is installed — skipping the unavailable path")
        resp = http_client.post(
            "/geo-cad/api/export",
            json={"layer_id": lid, "format": "dxf"},
        ).json()
        assert resp.get("error") == "gdal_unavailable"
        assert "recovery" in resp

    def test_settable_field_whitelist_via_set_field(self, http_client):
        """Boards-as-view-layer integration: set_field must reject non-whitelisted fields."""
        lid = assert_ok(http_client.post(
            "/geo-cad/api/layers",
            json={"title": _uniq("settable"), "project_id": "test-inbox"},
        ))["id"]
        # Allowed: title
        ok = assert_ok(http_client.patch(
            f"/geo-cad/api/layers/{lid}",
            json={"title": "renamed"},
        ))
        assert ok["ok"] is True
        # Disallowed: feature_count is not in SETTABLE_FIELDS
        rejected = http_client.patch(
            f"/geo-cad/api/layers/{lid}",
            json={"feature_count": 999},
        ).json()
        assert "error" in rejected
        assert "not settable" in rejected["error"]

    def test_delete_layer_archives_it(self, http_client):
        lid = assert_ok(http_client.post(
            "/geo-cad/api/layers",
            json={"title": _uniq("archived"), "project_id": "test-inbox"},
        ))["id"]
        assert_ok(http_client.delete(f"/geo-cad/api/layers/{lid}"))
        listing = assert_ok(http_client.get("/geo-cad/api/layers"))
        # archived layers drop from list
        assert lid not in [l["id"] for l in listing["layers"]]


@pytest.mark.interactive
class TestGeoCadUI:
    def test_browse_tab_renders(self, page, base_url):
        page.goto(f"{base_url}/geo-cad/")
        page.wait_for_selector('.eos-tab[data-tab="browse"]', timeout=5000)
        assert page.is_visible('.eos-tab[data-tab="editor"]')
        assert page.is_visible('.eos-tab[data-tab="io"]')
        assert page.is_visible('button.btn-settings')

    def test_settings_panel_opens(self, page, base_url):
        page.goto(f"{base_url}/geo-cad/")
        page.click('button.btn-settings')
        page.wait_for_selector('#geo-cad-settings-panel.eos-settings-panel-open',
                               timeout=3000, state='attached')

    def test_create_layer_via_modal(self, page, base_url, http_client):
        title = _uniq("ui-created")
        page.goto(f"{base_url}/geo-cad/")
        page.wait_for_selector('button.primary')
        page.click('button.primary:has-text("+ New layer")')
        page.wait_for_selector('input[id="eos-form-title"]', timeout=3000)
        page.fill('input[id="eos-form-title"]', title)
        page.click('button:has-text("Save")')
        # Toast confirms
        page.wait_for_selector('.eos-toast.eos-toast-ok', timeout=3000)
        # Verify via API
        listing = assert_ok(http_client.get("/geo-cad/api/layers"))
        assert any(l["title"] == title for l in listing["layers"])
