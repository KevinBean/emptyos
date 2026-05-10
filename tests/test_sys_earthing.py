"""System app tests: Earthing — RESAP soil + IEEE 80 grid + tolerable voltages."""

from __future__ import annotations

import time

import pytest

from helpers import TEST_PREFIX, assert_ok


def _uniq(stem: str) -> str:
    return f"{TEST_PREFIX}{stem}-{int(time.time() * 1000)}"


@pytest.mark.api
class TestEarthingAPI:
    def test_soil_fit_recovers_synthetic_model(self, http_client):
        # Synthetic ρ₁=80, ρ₂=400, h=3 — ρ_a values pre-computed via the engine.
        body = {
            "spacings": [0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0],
            "rho_a": [81.4, 84.4, 96.0, 153.7, 235.2, 311.9, 366.5],
        }
        data = assert_ok(http_client.post("/earthing/api/soil/fit-two-layer", json=body))
        assert data["ok"] is True
        # Fit should land within 15% of ground truth (grid resolution + rounding)
        assert 65 < data["rho1"] < 95
        assert 340 < data["rho2"] < 460
        assert 2.5 < data["h"] < 3.5
        assert len(data["predicted"]) == len(body["spacings"])

    def test_soil_fit_rejects_too_few_points(self, http_client):
        resp = http_client.post(
            "/earthing/api/soil/fit-two-layer",
            json={"spacings": [1.0, 2.0], "rho_a": [100.0, 110.0]},
        )
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_soil_predict_round_trip(self, http_client):
        body = {"rho1": 100.0, "rho2": 100.0, "h": 2.0, "spacings": [1, 5, 20]}
        data = assert_ok(http_client.post("/earthing/api/soil/predict", json=body))
        # ρ₁=ρ₂=100 → homogeneous → all predicted ρ_a = 100
        assert all(abs(v - 100.0) < 0.01 for v in data["predicted"])

    def test_grid_resistance_iee80_canonical_70m_grid(self, http_client):
        body = {"rho_soil": 400.0, "total_length": 2940.0, "area": 4900.0, "depth": 0.5}
        data = assert_ok(http_client.post("/earthing/api/grid/resistance", json=body))
        assert 2.5 < data["rg"] < 3.0  # IEEE 80 Annex B example
        assert data["method"] == "sverak"

    def test_grid_resistance_rejects_negative(self, http_client):
        resp = http_client.post(
            "/earthing/api/grid/resistance",
            json={"rho_soil": -1, "total_length": 100, "area": 100, "depth": 0.5},
        )
        assert "error" in resp.json()

    def test_tolerable_voltages_50kg_default(self, http_client):
        data = assert_ok(http_client.post(
            "/earthing/api/voltages/tolerable",
            json={"fault_duration": 0.5, "rho_soil": 400.0, "body_weight_kg": 50},
        ))
        # E_touch_50 = (1000 + 1.5·400) · 0.116 / √0.5 ≈ 262 V
        assert 255 < data["touch_v"] < 270
        # Step always > touch
        assert data["step_v"] > data["touch_v"]

    def test_tolerable_voltages_70kg_higher(self, http_client):
        body50 = {"fault_duration": 0.5, "rho_soil": 200.0, "body_weight_kg": 50}
        body70 = {"fault_duration": 0.5, "rho_soil": 200.0, "body_weight_kg": 70}
        v50 = http_client.post("/earthing/api/voltages/tolerable", json=body50).json()
        v70 = http_client.post("/earthing/api/voltages/tolerable", json=body70).json()
        assert v70["touch_v"] > v50["touch_v"]

    def test_surface_layer_raises_tolerable(self, http_client):
        bare = http_client.post("/earthing/api/voltages/tolerable", json={
            "fault_duration": 0.5, "rho_soil": 200.0,
        }).json()
        with_rock = http_client.post("/earthing/api/voltages/tolerable", json={
            "fault_duration": 0.5, "rho_soil": 200.0,
            "rho_surface": 2500.0, "surface_thickness": 0.10,
        }).json()
        assert with_rock["touch_v"] > 2 * bare["touch_v"]

    def test_voltages_reject_zero_duration(self, http_client):
        resp = http_client.post("/earthing/api/voltages/tolerable", json={
            "fault_duration": 0, "rho_soil": 200.0,
        })
        assert "error" in resp.json()

    def test_app_page_loads(self, http_client):
        resp = http_client.get("/earthing/")
        assert resp.status_code == 200
        assert "Earthing" in resp.text

    # ── V2 endpoints: Schwarz + 2-layer + safety verdict ─────────

    def test_grid_schwarz_grid_only_matches_sverak_band(self, http_client):
        body = {
            "rho_soil": 400.0, "total_length": 2940.0,
            "area": 4900.0, "length": 70.0, "width": 70.0,
            "depth": 0.5, "conductor_diameter": 0.010,
        }
        data = assert_ok(http_client.post("/earthing/api/grid/schwarz", json=body))
        # Schwarz grid-only on the IEEE 80 Annex B grid lands within
        # ~5% of Sverak's 2.78 Ω.
        assert 2.5 < data["R_g"] < 3.0
        assert data["method"] == "schwarz"
        assert data["R_rods"] is None or data["R_mutual"] == 0.0

    def test_grid_schwarz_rods_lower_resistance(self, http_client):
        base = {
            "rho_soil": 400.0, "total_length": 2940.0,
            "area": 4900.0, "length": 70.0, "width": 70.0,
            "depth": 0.5, "conductor_diameter": 0.010,
        }
        bare = http_client.post("/earthing/api/grid/schwarz", json=base).json()
        with_rods = http_client.post("/earthing/api/grid/schwarz", json={
            **base, "n_rods": 20, "rod_length": 3.0, "rod_diameter": 0.016,
        }).json()
        assert with_rods["R_g"] <= bare["R_g"]

    def test_two_layer_rho_between_layers(self, http_client):
        data = assert_ok(http_client.post("/earthing/api/grid/two-layer-rho", json={
            "rho1": 80.0, "rho2": 400.0, "h_layer1": 3.0, "area": 4900.0,
        }))
        assert 80.0 < data["rho_a"] < 400.0
        assert data["method"] == "tagg_burgsdorf"

    def test_safety_verdict_safe_when_gpr_below_touch(self, http_client):
        data = assert_ok(http_client.post("/earthing/api/safety/verdict", json={
            "rg": 0.5, "fault_current_a": 200.0,   # GPR = 100 V
            "touch_v": 262.0, "step_v": 800.0,
        }))
        assert data["verdict"] == "safe_by_gpr"
        assert data["gpr_v"] == 100.0
        assert data["margin_to_touch_v"] > 0

    def test_safety_verdict_mesh_required_when_gpr_exceeds_touch(self, http_client):
        data = assert_ok(http_client.post("/earthing/api/safety/verdict", json={
            "rg": 2.5, "fault_current_a": 5000.0,   # GPR = 12500 V
            "touch_v": 262.0, "step_v": 800000.0,    # absurd step keeps mesh-required
        }))
        assert data["verdict"] == "mesh_design_required"

    def test_safety_verdict_redesign_when_above_step(self, http_client):
        data = assert_ok(http_client.post("/earthing/api/safety/verdict", json={
            "rg": 5.0, "fault_current_a": 10000.0,   # GPR = 50000 V
            "touch_v": 262.0, "step_v": 800.0,
        }))
        assert data["verdict"] == "redesign_likely"

    def test_safety_verdict_rejects_negative_rg(self, http_client):
        resp = http_client.post("/earthing/api/safety/verdict", json={
            "rg": -1.0, "fault_current_a": 1000.0,
            "touch_v": 262.0, "step_v": 800.0,
        })
        assert "error" in resp.json()

    # ── IEEE 80 §15 — fault-current splitting ─────────────────────

    def test_split_factor_default_unity_backward_compat(self, http_client):
        # Without split_factor, GPR = Rg · I_f (unchanged from before).
        data = assert_ok(http_client.post("/earthing/api/safety/verdict", json={
            "rg": 2.0, "fault_current_a": 1000.0,
            "touch_v": 262.0, "step_v": 800.0,
        }))
        assert data["gpr_v"] == 2000.0
        assert data["split_factor"] == 1.0
        assert data["decrement_factor"] == 1.0
        assert data["i_g_a"] == 1000.0

    def test_split_factor_halves_gpr(self, http_client):
        # S_f = 0.5 → only half the fault current returns through the grid →
        # GPR halved → previously "redesign" verdict can flip to "safe_by_gpr".
        full = http_client.post("/earthing/api/safety/verdict", json={
            "rg": 2.0, "fault_current_a": 1000.0,
            "touch_v": 1500.0, "step_v": 5000.0,
        }).json()
        half = http_client.post("/earthing/api/safety/verdict", json={
            "rg": 2.0, "fault_current_a": 1000.0, "split_factor": 0.5,
            "touch_v": 1500.0, "step_v": 5000.0,
        }).json()
        assert full["gpr_v"] == 2000.0   # exceeds touch
        assert half["gpr_v"] == 1000.0   # below touch
        assert full["verdict"] == "mesh_design_required"
        assert half["verdict"] == "safe_by_gpr"
        assert half["i_g_a"] == 500.0
        assert half["split_factor"] == 0.5

    def test_decrement_factor_scales_gpr(self, http_client):
        data = assert_ok(http_client.post("/earthing/api/safety/verdict", json={
            "rg": 1.0, "fault_current_a": 1000.0,
            "split_factor": 0.5, "decrement_factor": 1.4,
            "touch_v": 1500.0, "step_v": 5000.0,
        }))
        # I_G = 1.4 · 0.5 · 1000 = 700 → GPR = 700 V
        assert abs(data["i_g_a"] - 700.0) < 1e-6
        assert abs(data["gpr_v"] - 700.0) < 1e-6

    def test_split_factor_rejects_out_of_range(self, http_client):
        for bad in (0, -0.1, 1.1, 2.0):
            resp = http_client.post("/earthing/api/safety/verdict", json={
                "rg": 2.0, "fault_current_a": 1000.0, "split_factor": bad,
                "touch_v": 262.0, "step_v": 800.0,
            })
            assert "error" in resp.json(), f"S_f={bad} should be rejected"

    def test_decrement_factor_rejects_below_one(self, http_client):
        resp = http_client.post("/earthing/api/safety/verdict", json={
            "rg": 2.0, "fault_current_a": 1000.0, "decrement_factor": 0.9,
            "touch_v": 262.0, "step_v": 800.0,
        })
        assert "error" in resp.json()

    def test_mesh_step_split_factor_scales_em(self, http_client):
        body = {
            "rho_a": 400.0, "fault_current_a": 3000.0,
            "grid_length": 70.0, "grid_width": 70.0,
            "grid_total_length": 2940.0, "spacing": 7.0,
            "depth": 0.5, "conductor_diameter": 0.010,
        }
        full = assert_ok(http_client.post("/earthing/api/grid/mesh-step", json=body))
        half = assert_ok(http_client.post(
            "/earthing/api/grid/mesh-step", json={**body, "split_factor": 0.5},
        ))
        # E_m ∝ I_G — halving I_G halves E_m.
        assert abs(half["E_m_v"] - 0.5 * full["E_m_v"]) < 1e-6
        assert abs(half["E_s_v"] - 0.5 * full["E_s_v"]) < 1e-6
        assert half["i_g_a"] == 1500.0
        assert half["i_f_a"] == 3000.0
        assert half["split_factor"] == 0.5

    def test_project_persists_split_and_decrement(self, http_client):
        name = _uniq("split-factor-project")
        pid = assert_ok(http_client.post(
            "/earthing/api/projects", json={"name": name},
        ))["id"]
        assert_ok(http_client.patch(
            f"/earthing/api/projects/{pid}/settings",
            json={"split_factor": 0.6, "decrement_factor": 1.2},
        ))
        proj = assert_ok(http_client.get(
            f"/earthing/api/projects/{pid}",
        ))["project"]
        assert float(proj["split_factor"]) == 0.6
        assert float(proj["decrement_factor"]) == 1.2

    # ── IEEE 80 Annex C — split-factor estimator ───────────────────

    def test_annex_c_estimate_transmission_only(self, http_client):
        data = assert_ok(http_client.post(
            "/earthing/api/safety/split-factor-estimate",
            json={"n_transmission": 2, "r_grid": 1.0},
        ))
        assert 0.0 < data["s_f"] < 1.0
        assert data["z_inf_distribution"] is None
        assert data["z_inf_transmission"]["re"] > 0
        assert data["z_lines"]["re"] > 0

    def test_annex_c_estimate_mixed_lower_than_tx_only(self, http_client):
        only_tx = assert_ok(http_client.post(
            "/earthing/api/safety/split-factor-estimate",
            json={"n_transmission": 2, "r_grid": 1.0},
        ))
        mixed = assert_ok(http_client.post(
            "/earthing/api/safety/split-factor-estimate",
            json={"n_transmission": 2, "n_distribution": 4, "r_grid": 1.0},
        ))
        assert mixed["s_f"] < only_tx["s_f"]

    def test_annex_c_estimate_low_rg_high_s_f(self, http_client):
        data = assert_ok(http_client.post(
            "/earthing/api/safety/split-factor-estimate",
            json={"n_transmission": 2, "r_grid": 0.001},
        ))
        assert data["s_f"] > 0.99

    def test_annex_c_estimate_rejects_no_lines(self, http_client):
        resp = http_client.post(
            "/earthing/api/safety/split-factor-estimate",
            json={"r_grid": 1.0},
        )
        assert "error" in resp.json()

    def test_annex_c_estimate_rejects_missing_rg(self, http_client):
        resp = http_client.post(
            "/earthing/api/safety/split-factor-estimate",
            json={"n_transmission": 2},
        )
        assert "error" in resp.json()

    def test_annex_c_estimate_accepts_complex_z_span(self, http_client):
        data = assert_ok(http_client.post(
            "/earthing/api/safety/split-factor-estimate",
            json={
                "n_transmission": 2, "r_grid": 1.0,
                "z_span_transmission_re": 0.4,
                "z_span_transmission_im": 1.5,
                "r_tower_transmission": 15.0,
            },
        ))
        # Brief's reference scenario lands in the canonical 0.3-0.85 band.
        assert 0.3 <= data["s_f"] <= 0.85

    # ── Vault-backed projects ─────────────────────────────────────

    def test_project_create_patch_fetch_round_trip(self, http_client):
        name = _uniq("north-substation")
        created = assert_ok(http_client.post("/earthing/api/projects", json={
            "name": name,
            "grid_total_length_m": 2940.0,
            "burial_depth_m": 0.5,
        }))
        assert created["ok"] is True
        pid = created["id"]

        patch = assert_ok(http_client.patch(
            f"/earthing/api/projects/{pid}/settings",
            json={
                "grid_area_m2": 4900.0,
                "rho1": 80.0, "rho2": 400.0, "h_layer1": 3.0,
                "fault_duration_s": 0.5, "fault_current_a": 5000.0,
                "body_weight_kg": 50.0,
            },
        ))
        assert patch["ok"] is True

        fetched = assert_ok(http_client.get(f"/earthing/api/projects/{pid}"))
        proj = fetched["project"]
        assert proj["name"] == name
        assert float(proj["grid_total_length_m"]) == 2940.0
        assert float(proj["grid_area_m2"]) == 4900.0
        assert float(proj["rho1"]) == 80.0
        assert float(proj["fault_current_a"]) == 5000.0

        # Bad field is rejected (whitelist)
        bad = http_client.patch(
            f"/earthing/api/projects/{pid}/settings",
            json={"backdoor_field": 42},
        ).json()
        assert "error" in bad

    def test_project_appears_in_list(self, http_client):
        name = _uniq("listed-project")
        created = assert_ok(http_client.post("/earthing/api/projects", json={"name": name}))
        pid = created["id"]
        listing = assert_ok(http_client.get("/earthing/api/projects"))
        ids = [p["id"] for p in listing["projects"]]
        assert pid in ids

    def test_soundings_sidecar_round_trip(self, http_client):
        name = _uniq("sounding-project")
        created = assert_ok(http_client.post("/earthing/api/projects", json={"name": name}))
        pid = created["id"]

        # Empty by default
        empty = assert_ok(http_client.get(f"/earthing/api/projects/{pid}/soundings"))
        assert empty["readings"] == []

        # Save 4 readings
        readings = [
            {"a": 0.5, "rho_a": 81.4},
            {"a": 1.0, "rho_a": 84.4},
            {"a": 5.0, "rho_a": 153.7},
            {"a": 20.0, "rho_a": 311.9},
        ]
        saved = assert_ok(http_client.put(
            f"/earthing/api/projects/{pid}/soundings",
            json={"readings": readings},
        ))
        assert saved["n"] == 4

        # Read back
        loaded = assert_ok(http_client.get(f"/earthing/api/projects/{pid}/soundings"))
        assert len(loaded["readings"]) == 4
        assert loaded["readings"][0]["a"] == 0.5
        assert loaded["readings"][0]["rho_a"] == 81.4

        # Missing project
        missing = http_client.get("/earthing/api/projects/does-not-exist/soundings").json()
        assert "error" in missing

    def test_geometry_empty_for_new_project(self, http_client):
        """A new project's geometry endpoint returns an empty model so the
        editor can render without branching."""
        name = _uniq("geo-empty-project")
        created = assert_ok(http_client.post("/earthing/api/projects", json={"name": name}))
        pid = created["id"]
        data = assert_ok(http_client.get(f"/earthing/api/projects/{pid}/geometry"))
        assert data["geometry"]["segments"] == []
        assert data["geometry"]["rods"] == []
        assert data["derived"]["total_length_m"] == 0.0
        assert data["derived"]["n_rods"] == 0

    def test_geometry_put_then_get_round_trip(self, http_client):
        """Save a 50×50 m square + 4 corner rods; read back; derived scalars
        match expected (200 m total, 2500 m² area, 200 m perimeter)."""
        name = _uniq("geo-rt-project")
        created = assert_ok(http_client.post("/earthing/api/projects", json={"name": name}))
        pid = created["id"]
        geo = {
            "scale_m_per_unit": 1.0,
            "segments": [
                {"x1": 0, "y1": 0, "x2": 50, "y2": 0},
                {"x1": 50, "y1": 0, "x2": 50, "y2": 50},
                {"x1": 50, "y1": 50, "x2": 0, "y2": 50},
                {"x1": 0, "y1": 50, "x2": 0, "y2": 0},
            ],
            "rods": [
                {"x": 0, "y": 0}, {"x": 50, "y": 0},
                {"x": 50, "y": 50}, {"x": 0, "y": 50},
            ],
        }
        saved = assert_ok(http_client.put(
            f"/earthing/api/projects/{pid}/geometry", json=geo,
        ))
        d = saved["derived"]
        assert abs(d["total_length_m"] - 200.0) < 0.01
        assert abs(d["area_m2"] - 2500.0) < 0.01
        assert abs(d["perimeter_m"] - 200.0) < 0.01
        assert d["n_rods"] == 4

        # Read back — same scalars
        loaded = assert_ok(http_client.get(f"/earthing/api/projects/{pid}/geometry"))
        assert len(loaded["geometry"]["segments"]) == 4
        assert len(loaded["geometry"]["rods"]) == 4
        assert abs(loaded["derived"]["total_length_m"] - 200.0) < 0.01

    def test_geometry_drops_malformed_rows(self, http_client):
        """Save mixes valid + malformed entries; only valid ones persist."""
        name = _uniq("geo-mal-project")
        created = assert_ok(http_client.post("/earthing/api/projects", json={"name": name}))
        pid = created["id"]
        body = {
            "segments": [
                {"x1": 0, "y1": 0, "x2": 10, "y2": 0},   # valid
                {"x1": "bad", "y1": 0, "x2": 5, "y2": 5},  # bad x1
                {"y1": 0},  # missing keys
                {"x1": 0, "y1": 0, "x2": 0, "y2": 5},   # valid
            ],
            "rods": [
                {"x": 1, "y": 1},
                None,
                "string",
                {"x": "bad", "y": 0},
            ],
        }
        saved = assert_ok(http_client.put(
            f"/earthing/api/projects/{pid}/geometry", json=body,
        ))
        assert len(saved["geometry"]["segments"]) == 2
        assert len(saved["geometry"]["rods"]) == 1

    def test_soil_fit_delegated_response_shape(self, http_client):
        """After delegation to engines/soil/, the earthing soil endpoint must
        keep returning the existing shape: rho1, rho2, h, predicted, rms_pct.
        The earthing UI binds to these field names; breaking the shape is a
        UI break."""
        body = {
            "spacings": [0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0],
            "rho_a": [81.4, 84.4, 96.0, 153.7, 235.2, 311.9, 366.5],
        }
        data = assert_ok(http_client.post("/earthing/api/soil/fit-two-layer", json=body))
        for k in ("rho1", "rho2", "h", "predicted", "rms_pct", "spacings"):
            assert k in data, f"missing key {k!r} — delegation broke UI contract"
        assert isinstance(data["predicted"], list)
        assert len(data["predicted"]) == len(body["spacings"])

    # ── Scenarios ─────────────────────────────────────────────────

    @staticmethod
    def _baseline_snapshot(L_T: float = 800.0, n_rods: int = 20):
        """Minimal snapshot the compare engine accepts: enough to compute
        Sverak R_g + tolerable + verdict via the GPR-only path."""
        return {
            "geometry": {"segments": [], "rods": []},
            "grid_spec": {
                "rho_soil": 100.0,
                "total_length_m": L_T,
                "area_m2": 2500.0,
                "burial_depth_m": 0.5,
                "n_rods": n_rods,
                "rg_method": "sverak",
            },
            "fault": {
                "fault_current_a": 5000.0,
                "fault_duration_s": 0.5,
                "split_factor": 1.0,
                "decrement_factor": 1.0,
                "projection_factor": 1.0,
            },
            "tolerable": {
                "body_weight_kg": 50,
                "rho_surface": 2500.0,
                "surface_thickness": 0.10,
            },
        }

    def test_scenario_create_get_list_round_trip(self, http_client):
        name = _uniq("scenario-rt-project")
        created = assert_ok(http_client.post("/earthing/api/projects", json={"name": name}))
        pid = created["id"]

        # List initially empty
        empty = assert_ok(http_client.get(f"/earthing/api/projects/{pid}/scenarios"))
        assert empty["scenarios"] == []

        # Create scenario
        snap = self._baseline_snapshot()
        body = {"label": "Design A — baseline", "snapshot": snap}
        created_sc = assert_ok(http_client.post(
            f"/earthing/api/projects/{pid}/scenarios", json=body,
        ))
        sid = created_sc["scenario"]["id"]
        assert sid  # non-empty slug
        assert created_sc["scenario"]["label"] == "Design A — baseline"

        # List shows it
        listed = assert_ok(http_client.get(f"/earthing/api/projects/{pid}/scenarios"))
        assert len(listed["scenarios"]) == 1
        assert listed["scenarios"][0]["id"] == sid

        # Get returns full payload
        got = assert_ok(http_client.get(f"/earthing/api/projects/{pid}/scenarios/{sid}"))
        assert got["scenario"]["grid_spec"]["total_length_m"] == 800.0
        assert got["scenario"]["fault"]["fault_current_a"] == 5000.0

    def test_scenario_label_required(self, http_client):
        """Empty / missing label is a hard error — scenarios are user-named."""
        name = _uniq("scenario-nolabel")
        created = assert_ok(http_client.post("/earthing/api/projects", json={"name": name}))
        pid = created["id"]
        resp = http_client.post(f"/earthing/api/projects/{pid}/scenarios",
                                 json={"label": "", "snapshot": self._baseline_snapshot()})
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_scenario_disambiguates_colliding_slugs(self, http_client):
        """Saving two scenarios with the same label produces unique IDs."""
        name = _uniq("scenario-collide")
        created = assert_ok(http_client.post("/earthing/api/projects", json={"name": name}))
        pid = created["id"]
        snap = self._baseline_snapshot()
        first = assert_ok(http_client.post(
            f"/earthing/api/projects/{pid}/scenarios",
            json={"label": "Design A", "snapshot": snap},
        ))
        second = assert_ok(http_client.post(
            f"/earthing/api/projects/{pid}/scenarios",
            json={"label": "Design A", "snapshot": snap},
        ))
        assert first["scenario"]["id"] != second["scenario"]["id"]

    def test_scenario_update_overwrites_snapshot_and_label(self, http_client):
        name = _uniq("scenario-update")
        created = assert_ok(http_client.post("/earthing/api/projects", json={"name": name}))
        pid = created["id"]
        snap = self._baseline_snapshot(L_T=800.0)
        sc = assert_ok(http_client.post(
            f"/earthing/api/projects/{pid}/scenarios",
            json={"label": "Design A", "snapshot": snap},
        ))
        sid = sc["scenario"]["id"]

        # Update with new label + denser conductor
        new_snap = self._baseline_snapshot(L_T=1600.0)
        updated = assert_ok(http_client.put(
            f"/earthing/api/projects/{pid}/scenarios/{sid}",
            json={"label": "Design A — denser", "snapshot": new_snap},
        ))
        assert updated["scenario"]["label"] == "Design A — denser"
        assert updated["scenario"]["grid_spec"]["total_length_m"] == 1600.0

        # GET reflects the update
        got = assert_ok(http_client.get(f"/earthing/api/projects/{pid}/scenarios/{sid}"))
        assert got["scenario"]["grid_spec"]["total_length_m"] == 1600.0

    def test_scenario_delete_removes_from_list(self, http_client):
        name = _uniq("scenario-del")
        created = assert_ok(http_client.post("/earthing/api/projects", json={"name": name}))
        pid = created["id"]
        sc = assert_ok(http_client.post(
            f"/earthing/api/projects/{pid}/scenarios",
            json={"label": "Doomed", "snapshot": self._baseline_snapshot()},
        ))
        sid = sc["scenario"]["id"]
        assert_ok(http_client.delete(f"/earthing/api/projects/{pid}/scenarios/{sid}"))
        listed = assert_ok(http_client.get(f"/earthing/api/projects/{pid}/scenarios"))
        assert listed["scenarios"] == []
        # Second delete returns an error, not a 500
        resp = http_client.delete(f"/earthing/api/projects/{pid}/scenarios/{sid}")
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_scenario_compare_reruns_math_per_scenario(self, http_client):
        """Two scenarios with different total conductor length must produce
        different R_g — proving compare re-runs the engine, not snapshots."""
        name = _uniq("scenario-cmp")
        created = assert_ok(http_client.post("/earthing/api/projects", json={"name": name}))
        pid = created["id"]
        # A: short conductor, B: 2× conductor → B should have lower R_g (Sverak)
        a = assert_ok(http_client.post(
            f"/earthing/api/projects/{pid}/scenarios",
            json={"label": "Sparse", "snapshot": self._baseline_snapshot(L_T=400.0)},
        ))
        b = assert_ok(http_client.post(
            f"/earthing/api/projects/{pid}/scenarios",
            json={"label": "Dense", "snapshot": self._baseline_snapshot(L_T=2000.0)},
        ))
        cmp_resp = assert_ok(http_client.post(
            f"/earthing/api/projects/{pid}/scenarios/compare",
            json={"scenario_ids": [a["scenario"]["id"], b["scenario"]["id"]]},
        ))
        rows = {r["id"]: r for r in cmp_resp["rows"]}
        rg_a = rows[a["scenario"]["id"]]["rg_ohm"]
        rg_b = rows[b["scenario"]["id"]]["rg_ohm"]
        assert rg_a > 0 and rg_b > 0, f"R_g must be positive: A={rg_a}, B={rg_b}"
        assert rg_b < rg_a, (
            f"Doubling conductor must lower R_g: A(L=400)={rg_a:.3f}, B(L=2000)={rg_b:.3f}"
        )

    def test_scenario_compare_includes_default_state(self, http_client):
        """include_default lets the UI compare a saved scenario against the
        live working state without forcing a save first."""
        name = _uniq("scenario-cmp-default")
        created = assert_ok(http_client.post("/earthing/api/projects", json={"name": name}))
        pid = created["id"]
        a = assert_ok(http_client.post(
            f"/earthing/api/projects/{pid}/scenarios",
            json={"label": "Saved", "snapshot": self._baseline_snapshot(L_T=800.0)},
        ))
        cmp_resp = assert_ok(http_client.post(
            f"/earthing/api/projects/{pid}/scenarios/compare",
            json={
                "scenario_ids": [a["scenario"]["id"]],
                "include_default": True,
                "default_state": self._baseline_snapshot(L_T=1200.0),
            },
        ))
        ids = [r["id"] for r in cmp_resp["rows"]]
        assert "_default" in ids
        assert a["scenario"]["id"] in ids
        # Default has L_T=1200, scenario has L_T=800 → different R_g
        rows = {r["id"]: r for r in cmp_resp["rows"]}
        assert rows["_default"]["rg_ohm"] != rows[a["scenario"]["id"]]["rg_ohm"]

    def test_scenario_compare_handles_missing_scenario_gracefully(self, http_client):
        """A bad scenario id in compare must not 500 the whole call."""
        name = _uniq("scenario-cmp-bad")
        created = assert_ok(http_client.post("/earthing/api/projects", json={"name": name}))
        pid = created["id"]
        good = assert_ok(http_client.post(
            f"/earthing/api/projects/{pid}/scenarios",
            json={"label": "Real", "snapshot": self._baseline_snapshot()},
        ))
        cmp_resp = assert_ok(http_client.post(
            f"/earthing/api/projects/{pid}/scenarios/compare",
            json={"scenario_ids": [good["scenario"]["id"], "no-such-scenario"]},
        ))
        rows = {r["id"]: r for r in cmp_resp["rows"]}
        assert good["scenario"]["id"] in rows
        assert rows[good["scenario"]["id"]].get("error") is None
        assert "no-such-scenario" in rows
        assert "error" in rows["no-such-scenario"]

    # ── Parameter sweep (what-if matrix) ────────────────────────────

    def test_sweep_returns_ranked_passing_designs(self, http_client):
        """Sweep produces ranked passing designs ordered by cost."""
        name = _uniq("sweep-basic")
        created = assert_ok(http_client.post("/earthing/api/projects", json={"name": name}))
        pid = created["id"]
        # Mesh inputs added so _evaluate_scenario_state takes the mesh-step
        # path. The GPR-only fast-pass branch (gpr <= e_touch) cannot
        # succeed at substation-scale fault currents — the realistic path
        # is mesh voltage vs touch tolerable, which is what real users hit.
        # rod_length_m is required for the swept n_rods axis to actually
        # reduce mesh voltage; without it rods are zero-length and contribute
        # nothing.
        base = self._baseline_snapshot(L_T=1000.0)
        base["grid_spec"].update({
            "grid_length_m": 50.0,
            "grid_width_m": 50.0,
            "spacing_m": 5.0,
            "conductor_diameter_m": 0.01,
            "rod_length_m": 3.0,
        })
        resp = assert_ok(http_client.post(
            f"/earthing/api/projects/{pid}/sweep",
            json={
                "base_state": base,
                "budget": {"max_total_length_m": 2000, "max_n_rods": 20, "max_depth_m": 1.0},
                "sweep": {
                    "total_length_m": [500, 1000, 1500, 2000],
                    "n_rods": [0, 8, 16],
                    "burial_depth_m": [0.5, 1.0],
                },
                "limit": 5,
            },
        ))
        assert resp["combos_evaluated"] == 4 * 3 * 2
        assert resp["candidates_passing"] >= 1, "at least one design must pass"
        assert len(resp["ranked"]) <= 5
        # Ordered ascending by cost
        costs = [r["cost"] for r in resp["ranked"]]
        assert costs == sorted(costs), f"ranked must be ascending by cost: {costs}"
        # Every returned row must pass and carry a re-runnable snapshot
        for row in resp["ranked"]:
            assert row["passes"] is True
            assert row["band"] == "pass"
            assert row["snapshot"]["grid_spec"]["total_length_m"] == row["params"]["total_length_m"]
            assert row["snapshot"]["grid_spec"]["n_rods"] == row["params"]["n_rods"]

    def test_sweep_respects_max_rg_target(self, http_client):
        """target.max_rg_ohm filters out designs that pass tolerable but exceed R_g cap."""
        name = _uniq("sweep-rg-cap")
        created = assert_ok(http_client.post("/earthing/api/projects", json={"name": name}))
        pid = created["id"]
        resp = assert_ok(http_client.post(
            f"/earthing/api/projects/{pid}/sweep",
            json={
                "base_state": self._baseline_snapshot(L_T=1000.0),
                "target": {"max_rg_ohm": 0.5, "require_pass": True},
                "sweep": {
                    "total_length_m": [500, 2000],
                    "n_rods": [0, 20],
                    "burial_depth_m": [0.5],
                },
                "limit": 5,
            },
        ))
        for row in resp["ranked"]:
            assert row["rg_ohm"] <= 0.5, f"R_g cap violated: {row['rg_ohm']}"

    def test_sweep_caps_combos(self, http_client):
        """Sweep larger than _SWEEP_MAX_COMBOS is rejected, not silently truncated."""
        name = _uniq("sweep-too-big")
        created = assert_ok(http_client.post("/earthing/api/projects", json={"name": name}))
        pid = created["id"]
        # 10 × 10 × 5 = 500 > 200 cap
        resp = http_client.post(
            f"/earthing/api/projects/{pid}/sweep",
            json={
                "base_state": self._baseline_snapshot(),
                "sweep": {
                    "total_length_m": [200, 400, 600, 800, 1000, 1200, 1400, 1600, 1800, 2000],
                    "n_rods": list(range(0, 20, 2)),
                    "burial_depth_m": [0.3, 0.5, 0.7, 0.9, 1.0],
                },
                "budget": {"max_total_length_m": 2000, "max_n_rods": 20, "max_depth_m": 1.0},
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "error" in body
        assert "too large" in body["error"]

    def test_sweep_unknown_project_404s(self, http_client):
        """Sweep on a missing project returns an error, not a stack trace."""
        resp = http_client.post(
            "/earthing/api/projects/no-such-project/sweep",
            json={"base_state": self._baseline_snapshot()},
        )
        assert resp.status_code == 200
        assert "error" in resp.json()

    # ── DXF import ────────────────────────────────────────────────

    @staticmethod
    def _build_dxf_bytes(scale_layer: str = "GRID", rod_layer: str = "RODS") -> bytes:
        """Synthesize a tiny DXF: a 50×50 m square mesh on `scale_layer`
        plus 4 corner rods on `rod_layer`. Uses ezdxf, which is required
        on the test box anyway for the parser to function — if missing,
        the test will skip via _require_ezdxf().
        """
        import ezdxf, io
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        doc.layers.add(name=scale_layer, color=1)
        doc.layers.add(name=rod_layer, color=2)
        # Rectangular mesh: 4 perimeter LINEs + 1 LWPOLYLINE diagonal cross.
        msp.add_line((0, 0), (50, 0), dxfattribs={"layer": scale_layer})
        msp.add_line((50, 0), (50, 50), dxfattribs={"layer": scale_layer})
        msp.add_line((50, 50), (0, 50), dxfattribs={"layer": scale_layer})
        msp.add_line((0, 50), (0, 0), dxfattribs={"layer": scale_layer})
        msp.add_lwpolyline(
            [(0, 0), (50, 50)],
            dxfattribs={"layer": scale_layer},
        )
        # 4 corner rods
        for x, y in [(0, 0), (50, 0), (50, 50), (0, 50)]:
            msp.add_point((x, y), dxfattribs={"layer": rod_layer})
        # Stray entity on a third layer — should appear in layers_seen
        # but be filterable via conductor_layers.
        doc.layers.add(name="ANNOTATION", color=7)
        msp.add_line((100, 100), (110, 110), dxfattribs={"layer": "ANNOTATION"})
        buf = io.StringIO()
        doc.write(buf)
        return buf.getvalue().encode("utf-8")

    @staticmethod
    def _require_ezdxf():
        try:
            import ezdxf  # noqa: F401
        except ImportError:
            pytest.skip("ezdxf not installed — install with pip install 'emptyos[dxf]'")

    def test_dxf_import_preview_returns_segments_and_rods(self, http_client):
        """DXF import (apply=false) returns parsed geometry without writing."""
        self._require_ezdxf()
        name = _uniq("dxf-preview")
        created = assert_ok(http_client.post("/earthing/api/projects", json={"name": name}))
        pid = created["id"]
        dxf = self._build_dxf_bytes()
        files = {"file": ("test.dxf", dxf, "application/dxf")}
        data = {"scale_m_per_unit": "1.0"}
        resp = assert_ok(http_client.post(
            f"/earthing/api/projects/{pid}/geometry/import-dxf",
            files=files, data=data,
        ))
        assert resp["applied"] is False
        # 4 perimeter LINEs + 1 LWPOLYLINE-as-1-segment + 1 ANNOTATION LINE = 6 segments
        assert len(resp["geometry"]["segments"]) == 6
        assert len(resp["geometry"]["rods"]) == 4
        # Did NOT persist
        geo = assert_ok(http_client.get(f"/earthing/api/projects/{pid}/geometry"))
        assert geo["geometry"]["segments"] == []
        assert geo["geometry"]["rods"] == []
        # Summary mirrors counts
        s = resp["summary"]
        assert s["n_lines"] == 5  # 4 perimeter + 1 stray
        assert s["n_polys"] == 1
        assert s["n_points"] == 4
        assert "GRID" in s["layers_seen"]
        assert "RODS" in s["layers_seen"]

    def test_dxf_import_apply_writes_geometry(self, http_client):
        """apply=true overwrites the project geometry."""
        self._require_ezdxf()
        name = _uniq("dxf-apply")
        created = assert_ok(http_client.post("/earthing/api/projects", json={"name": name}))
        pid = created["id"]
        dxf = self._build_dxf_bytes()
        resp = assert_ok(http_client.post(
            f"/earthing/api/projects/{pid}/geometry/import-dxf",
            files={"file": ("g.dxf", dxf, "application/dxf")},
            data={"apply": "true", "scale_m_per_unit": "1.0"},
        ))
        assert resp["applied"] is True
        geo = assert_ok(http_client.get(f"/earthing/api/projects/{pid}/geometry"))
        assert len(geo["geometry"]["segments"]) >= 4
        assert len(geo["geometry"]["rods"]) == 4

    def test_dxf_import_layer_filter_excludes_other_layers(self, http_client):
        """conductor_layers filter restricts which LINEs / POLYLINEs are imported."""
        self._require_ezdxf()
        name = _uniq("dxf-layer")
        created = assert_ok(http_client.post("/earthing/api/projects", json={"name": name}))
        pid = created["id"]
        dxf = self._build_dxf_bytes()
        resp = assert_ok(http_client.post(
            f"/earthing/api/projects/{pid}/geometry/import-dxf",
            files={"file": ("g.dxf", dxf, "application/dxf")},
            data={"conductor_layers": "GRID"},  # excludes ANNOTATION
        ))
        # 4 perimeter + 1 polyline = 5 segments; the ANNOTATION LINE is filtered
        assert len(resp["geometry"]["segments"]) == 5

    def test_dxf_import_scale_multiplier_applied(self, http_client):
        """scale_m_per_unit scales coordinates (mm → m: 0.001)."""
        self._require_ezdxf()
        name = _uniq("dxf-scale")
        created = assert_ok(http_client.post("/earthing/api/projects", json={"name": name}))
        pid = created["id"]
        dxf = self._build_dxf_bytes()
        resp = assert_ok(http_client.post(
            f"/earthing/api/projects/{pid}/geometry/import-dxf",
            files={"file": ("g.dxf", dxf, "application/dxf")},
            data={"scale_m_per_unit": "0.001", "conductor_layers": "GRID"},
        ))
        # First perimeter LINE (0,0)→(50,0) becomes (0,0)→(0.05,0)
        first = resp["geometry"]["segments"][0]
        assert abs(first["x2"] - 0.05) < 1e-9
        assert abs(first["y2"]) < 1e-9

    def test_dxf_import_missing_file_field_errors(self, http_client):
        name = _uniq("dxf-no-file")
        created = assert_ok(http_client.post("/earthing/api/projects", json={"name": name}))
        pid = created["id"]
        resp = http_client.post(
            f"/earthing/api/projects/{pid}/geometry/import-dxf",
            data={"scale_m_per_unit": "1.0"},
        )
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_dxf_import_unknown_project_404s(self, http_client):
        self._require_ezdxf()
        dxf = self._build_dxf_bytes()
        resp = http_client.post(
            "/earthing/api/projects/no-such-project/geometry/import-dxf",
            files={"file": ("g.dxf", dxf, "application/dxf")},
        )
        assert resp.status_code == 200
        assert "error" in resp.json()

    # ── Scenario cloning ──────────────────────────────────────────

    def test_scenario_clone_creates_new_with_cloned_from_marker(self, http_client):
        """Cloning an existing scenario produces a new id seeded with the
        same snapshot fields and a cloned_from pointer."""
        name = _uniq("clone-rt")
        created = assert_ok(http_client.post("/earthing/api/projects", json={"name": name}))
        pid = created["id"]
        src_label = _uniq("Original")
        src = assert_ok(http_client.post(
            f"/earthing/api/projects/{pid}/scenarios",
            json={"label": src_label, "snapshot": self._baseline_snapshot(L_T=1234.0)},
        ))
        sid = src["scenario"]["id"]
        clone_resp = assert_ok(http_client.post(
            f"/earthing/api/projects/{pid}/scenarios/{sid}/clone",
            json={"label": _uniq("Clone-Tweaked")},
        ))
        cloned = clone_resp["scenario"]
        assert cloned["id"] != sid
        assert cloned["cloned_from"] == sid
        assert cloned["grid_spec"]["total_length_m"] == 1234.0
        # Both scenarios live in the listing
        listing = assert_ok(http_client.get(f"/earthing/api/projects/{pid}/scenarios"))
        ids = {s["id"] for s in listing["scenarios"]}
        assert sid in ids and cloned["id"] in ids

    def test_scenario_clone_default_label_is_copy_of_original(self, http_client):
        """Omitting label defaults to 'Copy of <original>'."""
        name = _uniq("clone-default")
        created = assert_ok(http_client.post("/earthing/api/projects", json={"name": name}))
        pid = created["id"]
        src_label = _uniq("Design-A")
        src = assert_ok(http_client.post(
            f"/earthing/api/projects/{pid}/scenarios",
            json={"label": src_label, "snapshot": self._baseline_snapshot()},
        ))
        sid = src["scenario"]["id"]
        clone_resp = assert_ok(http_client.post(
            f"/earthing/api/projects/{pid}/scenarios/{sid}/clone",
            json={},
        ))
        assert clone_resp["scenario"]["label"] == f"Copy of {src_label}"

    def test_scenario_clone_disambiguates_duplicate_labels(self, http_client):
        """Cloning the same scenario twice produces non-colliding ids."""
        name = _uniq("clone-dup")
        created = assert_ok(http_client.post("/earthing/api/projects", json={"name": name}))
        pid = created["id"]
        src = assert_ok(http_client.post(
            f"/earthing/api/projects/{pid}/scenarios",
            json={"label": _uniq("Src"), "snapshot": self._baseline_snapshot()},
        ))
        sid = src["scenario"]["id"]
        a = assert_ok(http_client.post(
            f"/earthing/api/projects/{pid}/scenarios/{sid}/clone",
            json={"label": "Same Label"},
        ))
        b = assert_ok(http_client.post(
            f"/earthing/api/projects/{pid}/scenarios/{sid}/clone",
            json={"label": "Same Label"},
        ))
        assert a["scenario"]["id"] != b["scenario"]["id"]

    def test_scenario_clone_unknown_scenario_404s(self, http_client):
        name = _uniq("clone-missing")
        created = assert_ok(http_client.post("/earthing/api/projects", json={"name": name}))
        pid = created["id"]
        resp = http_client.post(
            f"/earthing/api/projects/{pid}/scenarios/no-such/clone",
            json={},
        )
        assert resp.status_code == 200
        assert "error" in resp.json()

    # ── Auto-design (CDEGS AutoGround-style iterative solver) ─────

    @staticmethod
    def _auto_baseline():
        """Snapshot for auto-design tests — has zone footprint + safety
        inputs, no grid_spec total_length/n_h/n_v (heuristic seeds those).
        """
        return {
            "geometry": {"segments": [], "rods": []},
            "grid_spec": {
                "rho_soil": 100.0,
                "area_m2": 2500.0,
                "grid_length_m": 50.0,
                "grid_width_m": 50.0,
                "burial_depth_m": 1.0,
                "rg_method": "sverak",
            },
            "fault": {
                "fault_current_a": 5000.0,
                "fault_duration_s": 0.5,
                "split_factor": 1.0,
                "decrement_factor": 1.0,
                "projection_factor": 1.0,
            },
            "tolerable": {
                "body_weight_kg": 50,
                "rho_surface": 2500.0,
                "surface_thickness": 0.10,
            },
        }

    def test_auto_design_converges_and_returns_grid(self, http_client):
        """Auto-design seeds + refines until safety met, returns one design."""
        name = _uniq("auto-design")
        created = assert_ok(http_client.post("/earthing/api/projects", json={"name": name}))
        pid = created["id"]
        resp = assert_ok(http_client.post(
            f"/earthing/api/projects/{pid}/auto-design",
            json={"base_state": self._auto_baseline()},
        ))
        assert resp["ok"] is True
        assert resp["feasibility"]["feasible"] is True
        # Initial grid carries n_h, n_v, derived total_length + spacing
        init = resp["initial_grid"]
        assert init["n_h"] >= 2 and init["n_v"] >= 2
        assert init["total_length_m"] > 0
        # Refinement converged
        ref = resp["refinement"]
        assert ref["converged"] is True, f"did not converge: {ref}"
        assert ref["iterations"] >= 1
        # Final iteration in history must pass
        last = ref["history"][-1]
        assert last["passes"] is True
        # Each iteration grows n_h and n_v monotonically
        n_h_seq = [r["n_h"] for r in ref["history"]]
        n_v_seq = [r["n_v"] for r in ref["history"]]
        assert all(n_h_seq[i] <= n_h_seq[i+1] for i in range(len(n_h_seq)-1))
        assert all(n_v_seq[i] <= n_v_seq[i+1] for i in range(len(n_v_seq)-1))

    def test_auto_design_persists_as_scenario(self, http_client):
        """When persist_as is supplied and design converges, scenario is saved."""
        name = _uniq("auto-persist")
        created = assert_ok(http_client.post("/earthing/api/projects", json={"name": name}))
        pid = created["id"]
        label = _uniq("auto-design-result")
        resp = assert_ok(http_client.post(
            f"/earthing/api/projects/{pid}/auto-design",
            json={"base_state": self._auto_baseline(), "persist_as": label},
        ))
        assert resp["scenario"] is not None
        sid = resp["scenario"]["id"]
        # Round-trip — scenario appears in list and can be fetched
        listing = assert_ok(http_client.get(f"/earthing/api/projects/{pid}/scenarios"))
        assert any(s["id"] == sid for s in listing["scenarios"])
        fetched = assert_ok(http_client.get(f"/earthing/api/projects/{pid}/scenarios/{sid}"))
        assert fetched["scenario"]["label"] == label
        assert "auto_design" in fetched["scenario"]
        assert fetched["scenario"]["auto_design"]["iterations"] >= 1

    def test_auto_design_infeasible_zone_refuses(self, http_client):
        """If even a metal plate the size of the zone fails safety, refuse."""
        name = _uniq("auto-infeasible")
        created = assert_ok(http_client.post("/earthing/api/projects", json={"name": name}))
        pid = created["id"]
        snap = self._auto_baseline()
        # Tiny zone + huge fault current — plate baseline cannot save us
        snap["grid_spec"]["area_m2"] = 25.0
        snap["grid_spec"]["grid_length_m"] = 5.0
        snap["grid_spec"]["grid_width_m"] = 5.0
        snap["fault"]["fault_current_a"] = 50000.0
        resp = http_client.post(
            f"/earthing/api/projects/{pid}/auto-design",
            json={"base_state": snap},
        ).json()
        assert resp["ok"] is False
        assert resp["feasibility"]["feasible"] is False
        assert "enlarge zone" in resp["feasibility"]["reason"]

    def test_auto_design_unknown_project_404s(self, http_client):
        resp = http_client.post(
            "/earthing/api/projects/no-such-project/auto-design",
            json={"base_state": self._auto_baseline()},
        )
        assert resp.status_code == 200
        assert "error" in resp.json()
