"""System app tests: Interference — vault-backed studies + Biot-Savart analysis."""

from __future__ import annotations

import time

import pytest

from helpers import TEST_PREFIX, assert_ok


def _uniq(stem: str) -> str:
    return f"{TEST_PREFIX}{stem}-{int(time.time() * 1000)}"


@pytest.mark.api
class TestInterferenceAPI:
    def test_create_study_round_trip(self, http_client):
        name = _uniq("400kv-corridor")
        # Opt in to the 400 kV horizontal seed; the bare POST shape
        # intentionally saves an empty/incomplete study now.
        created = assert_ok(http_client.post(
            "/interference/api/studies",
            json={"name": name, "seed_default_geometry": True},
        ))
        assert created["ok"] is True
        sid = created["id"]
        fetched = assert_ok(http_client.get(f"/interference/api/studies/{sid}"))
        study = fetched["study"]
        assert study["name"] == name
        assert study["status"] == "ready"
        assert study.get("seeded_from") == "ieee_400kv_horizontal"
        assert len(study["conductors"]) == 3
        # Phase angles 0, -120, 120
        phases = sorted(c["current_phase_deg"] for c in study["conductors"])
        assert phases == [-120, 0, 120]

    def test_create_without_seed_saves_incomplete(self, http_client):
        name = _uniq("empty")
        created = assert_ok(http_client.post(
            "/interference/api/studies", json={"name": name},
        ))
        sid = created["id"]
        study = assert_ok(http_client.get(f"/interference/api/studies/{sid}"))["study"]
        assert study["status"] == "incomplete"
        assert study["conductors"] == []
        assert "hint" in study
        # Profile / grid analysis should refuse with a clear error,
        # not return all-zero samples.
        prof = http_client.post(
            f"/interference/api/studies/{sid}/profile",
            json={"axis": "y", "min": -10, "max": 10, "steps": 11},
        ).json()
        assert "error" in prof
        assert "no conductors" in prof["error"]

    def test_create_rejects_empty_name(self, http_client):
        resp = http_client.post("/interference/api/studies", json={"name": "  "})
        assert "error" in resp.json()

    def test_list_contains_created_study(self, http_client):
        name = _uniq("listed")
        sid = assert_ok(http_client.post(
            "/interference/api/studies", json={"name": name},
        ))["id"]
        listing = assert_ok(http_client.get("/interference/api/studies"))
        ids = [s["id"] for s in listing["studies"]]
        assert sid in ids

    def test_patch_updates_frequency(self, http_client):
        sid = assert_ok(http_client.post(
            "/interference/api/studies",
            json={"name": _uniq("freq"), "frequency_hz": 50,
                  "seed_default_geometry": True},
        ))["id"]
        patched = assert_ok(http_client.patch(
            f"/interference/api/studies/{sid}", json={"frequency_hz": 60.0},
        ))
        assert patched["ok"] is True
        study = assert_ok(http_client.get(f"/interference/api/studies/{sid}"))["study"]
        assert float(study["frequency_hz"]) == 60.0

    def test_patch_rejects_field_outside_whitelist(self, http_client):
        sid = assert_ok(http_client.post(
            "/interference/api/studies", json={"name": _uniq("whitelist")},
        ))["id"]
        bad = http_client.patch(
            f"/interference/api/studies/{sid}", json={"backdoor_field": 42},
        ).json()
        assert "error" in bad

    def test_profile_analysis_returns_samples(self, http_client):
        sid = assert_ok(http_client.post(
            "/interference/api/studies",
            json={"name": _uniq("profile"), "seed_default_geometry": True},
        ))["id"]
        result = assert_ok(http_client.post(
            f"/interference/api/studies/{sid}/profile",
            json={"axis": "y", "min": -20, "max": 20, "steps": 21,
                  "x": 0, "y": 0, "z": 1.5},
        ))
        assert result["ok"] is True
        assert result["axis"] == "y"
        assert result["n_samples"] == 21
        assert result["max_B"] > 0  # 1000 A balanced 3-phase produces a field
        # Sample shape — each entry has B + position
        for s in result["samples"][:3]:
            assert "B" in s and "position" in s

    def test_grid_analysis_returns_flat_grid(self, http_client):
        sid = assert_ok(http_client.post(
            "/interference/api/studies",
            json={"name": _uniq("grid"), "seed_default_geometry": True},
        ))["id"]
        result = assert_ok(http_client.post(
            f"/interference/api/studies/{sid}/grid",
            json={"plane": "yz", "fixed": 0,
                  "r1_min": -10, "r1_max": 10, "r1_steps": 5,
                  "r2_min": 0, "r2_max": 10, "r2_steps": 5},
        ))
        assert result["ok"] is True
        assert len(result["grid"]) == 25
        assert result["max_B"] > 0

    def test_delete_archives_and_removes_from_list(self, http_client):
        sid = assert_ok(http_client.post(
            "/interference/api/studies", json={"name": _uniq("doomed")},
        ))["id"]
        deleted = assert_ok(http_client.delete(f"/interference/api/studies/{sid}"))
        assert deleted["ok"] is True
        listing = assert_ok(http_client.get("/interference/api/studies"))
        assert sid not in [s["id"] for s in listing["studies"]]

    def test_app_page_loads(self, http_client):
        resp = http_client.get("/interference/")
        assert resp.status_code == 200
        assert "Induced voltage" in resp.text
        assert "/interference/api/induced-voltage" in resp.text

    def test_induced_voltage_api_returns_touch_limit_verdict(self, http_client):
        result = assert_ok(http_client.post(
            "/interference/api/induced-voltage",
            json={
                "fault_current_a": 10_000,
                "exposure_length_m": 500,
                "separation_m": 5,
                "soil_resistivity_ohm_m": 100,
                "screen_factor": 0.2,
                "limit_v": 60,
            },
        ))
        assert result["ok"] is True
        assert result["method"] == "carson_lfi"
        assert result["induced_voltage_v"] > 0
        assert result["induced_voltage_unscreened_v"] > result["induced_voltage_v"]
        assert result["limit_v"] == 60
        assert result["verdict"] in {"within_limit", "exceeds_limit"}

    def test_profile_mom3d_true_3d_efield(self, http_client):
        sid = assert_ok(http_client.post(
            "/interference/api/studies",
            json={"name": _uniq("mom3d"), "seed_default_geometry": True},
        ))["id"]
        result = assert_ok(http_client.post(
            f"/interference/api/studies/{sid}/profile",
            json={"axis": "y", "min": -30, "max": 30, "steps": 61,
                  "x": 0, "y": 0, "z": 1, "e_model": "mom3d"},
        ))
        assert result["ok"] is True
        assert result["has_efield"] is True
        assert result["e_model"] == "mom3d"
        # 400 kV seed → lateral |E| peak in the kV/m band.
        assert 200 < result["max_E"] < 5000, f"max_E={result['max_E']}"

    def test_scene_endpoint_returns_conductor_polylines(self, http_client):
        sid = assert_ok(http_client.post(
            "/interference/api/studies",
            json={"name": _uniq("scene"), "seed_default_geometry": True},
        ))["id"]
        sc = assert_ok(http_client.post(f"/interference/api/studies/{sid}/scene"))
        assert sc["ok"] is True
        assert len(sc["conductors"]) == 3
        # each conductor is a sampled 3D polyline (>=2 points, xyz each)
        for c in sc["conductors"]:
            assert len(c["points"]) >= 2
            assert len(c["points"][0]) == 3
        assert "x" in sc["bounds"] and "z" in sc["bounds"]

    # ── Validation reference cases ─────────────────────────────────────

    def test_references_list_includes_modric(self, http_client):
        d = assert_ok(http_client.get("/interference/api/references"))
        refs = {r["id"]: r for r in d["references"]}
        assert "modric-2015-400kv" in refs
        case = refs["modric-2015-400kv"]
        assert case["n_conductors"] == 8  # 6 phase + 2 shield wires
        assert case["expected"]["peak_B_uT"] == 31.65
        assert case["kb_slug"] == "modric-2015-3d-powerline-magfield-4-case"

    def test_reference_run_matches_published_cdegs_value(self, http_client):
        r = assert_ok(http_client.post(
            "/interference/api/references/modric-2015-400kv/run"))
        assert r["ok"] is True
        # Engine is a port of the paper's algorithm → should land on 31.65 µT.
        assert abs(r["computed_peak_uT"] - 31.65) / 31.65 < 0.03
        assert r["within_tol"] is True
        assert len(r["samples"]) == 121
        # Paper-reported context surfaced for the UI chips.
        assert r["context"]["cdegs_agreement_pct"] == 0.06

    def test_reference_run_unknown_id_errors(self, http_client):
        bad = http_client.post(
            "/interference/api/references/nope/run").json()
        assert "error" in bad

    def test_reference_load_creates_full_geometry_study(self, http_client):
        r = assert_ok(http_client.post(
            "/interference/api/references/modric-2015-400kv/load-study"))
        assert r["ok"] is True
        sid = r["id"]
        try:
            study = assert_ok(http_client.get(
                f"/interference/api/studies/{sid}"))["study"]
            assert len(study["conductors"]) == 8
            assert study.get("seeded_from") == "modric-2015-400kv"
            # Two shield wires carry is_phase False.
            shields = [c for c in study["conductors"]
                       if c.get("is_phase") is False]
            assert len(shields) == 2
        finally:
            # The loaded study name has no TEST_PREFIX (it's the reference's
            # name), so the conftest leak sweep won't catch it — archive it
            # here so the run leaves no vault residue.
            http_client.delete(f"/interference/api/studies/{sid}")
