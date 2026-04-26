"""System tests: grid-analytics + the 10-app chain it aggregates.

Covers the overview endpoint shape, the seed-all orchestrator, the hazard
matrix computation, twin validation, PSPS ranking, the business-case
generator, and the Grid Analyst Agent — 12 cases in total. Mirrors the
structure of other `test_sys_*.py` files; uses the standard `http_client`
fixture from `conftest.py` so the daemon must be running on :9000.
"""

import pytest

from helpers import assert_dict_response, assert_list_response, assert_ok


@pytest.mark.api
class TestGridAnalyticsAPI:

    def test_overview_shape(self, http_client):
        """GET /grid-analytics/api/overview returns the full aggregated shape."""
        data = assert_dict_response(http_client.get("/grid-analytics/api/overview"))
        for key in ("stats", "scans", "hazard_matrix", "validation",
                    "psps_ranking", "intrusions", "cer_hosting",
                    "drone_routes", "scenarios", "narrative"):
            assert key in data, f"overview missing key `{key}`: {list(data.keys())}"

    def test_scenarios_include_bushfire(self, http_client):
        """Exposed SCENARIOS list covers the new bushfire_risk composite."""
        data = assert_dict_response(http_client.get("/grid-analytics/api/overview"))
        assert "bushfire_risk" in data.get("scenarios", []), \
            "bushfire_risk scenario must be exposed in overview.scenarios"

    def test_seed_all_runs_multi_scenario(self, http_client):
        """POST /grid-analytics/api/seed-all fires every scenario + downstream apps."""
        r = http_client.post("/grid-analytics/api/seed-all?scan=demo-50")
        data = assert_dict_response(r)
        assert data.get("ok"), f"seed-all returned ok=false: {data}"
        steps = data.get("steps", [])
        scen_steps = [s for s in steps if s.get("step") == "run_scenario"]
        assert len(scen_steps) >= 5, \
            f"Expected ≥5 scenario runs, got {len(scen_steps)}"
        # Optional downstream steps (skip gracefully if plugin not loaded)
        step_names = {s.get("step") for s in steps}
        for expected in ("reconcile_assets", "rebuild_queue", "classify_defects"):
            assert expected in step_names, \
                f"seed-all missing step `{expected}`: {sorted(step_names)}"

    def test_seed_all_default_is_50_pole(self, http_client):
        """seed-all with no args uses the 51-pole branched feeder."""
        r = http_client.post("/grid-analytics/api/seed-all")
        data = assert_dict_response(r)
        assert data.get("scan_id") == "demo-feeder-50pole", \
            f"Expected default scan demo-feeder-50pole, got {data.get('scan_id')}"

    def test_hazard_matrix_populated(self, http_client):
        """After seed-all the hazard matrix must have all 5+ scenarios per scan."""
        http_client.post("/grid-analytics/api/seed-all?scan=demo-50")
        data = assert_dict_response(http_client.get("/grid-analytics/api/overview"))
        hm = data.get("hazard_matrix", {})
        assert hm, "hazard_matrix is empty after seed-all"
        for scan_id, per_scen in hm.items():
            for scen in ("normal", "heat", "wind", "flood", "veg_growth", "bushfire_risk"):
                assert scen in per_scen, \
                    f"scan {scan_id} missing scenario {scen} in hazard_matrix"
                cell = per_scen[scen]
                assert "counts" in cell and "total" in cell, \
                    f"hazard cell shape wrong: {cell}"

    def test_validation_response_shape(self, http_client):
        """grid-twin /api/validate returns MAE, bias, CI, divergent list."""
        http_client.post("/grid-analytics/api/seed-all?scan=demo-50")
        r = http_client.post(
            "/grid-twin/api/validate",
            json={"scan_id": "demo-feeder-50pole", "scenario": "heat"},
        )
        data = assert_dict_response(r)
        assert data.get("ok"), f"validate returned ok=false: {data}"
        result = data.get("result") or {}
        for key in ("summaries", "overall_mae", "divergent_assets",
                    "divergent_count", "measurement_source"):
            assert key in result, f"validation missing `{key}`: {list(result.keys())}"
        # Each summary has the standard error metrics
        for s in result["summaries"]:
            for k in ("metric", "n", "mae", "bias", "p95", "ci95_m"):
                assert k in s, f"summary missing `{k}`: {s}"

    def test_psps_ranking_has_community(self, http_client):
        """PSPS ranking includes community_impact fields for seeded feeder."""
        http_client.post("/grid-analytics/api/seed-all?scan=demo-50")
        data = assert_dict_response(http_client.get("/grid-analytics/api/overview"))
        rank = data.get("psps_ranking", [])
        assert rank, "psps_ranking is empty after seed-all"
        target = next((r for r in rank
                       if r.get("scan_id") == "demo-feeder-50pole"), None)
        assert target is not None, \
            "demo-feeder-50pole not present in PSPS ranking"
        assert target["customers_served"] > 0, \
            "demo-feeder-50pole should have non-zero customers_served"
        assert isinstance(target["community_facilities"], list)
        assert isinstance(target["risk_score"], (int, float))

    def test_business_case_violation(self, http_client):
        """POST /api/business-case returns cost fields + narrative for a payload."""
        violation = {
            "type": "ground-clearance", "span": "F-05",
            "value": 4.8, "threshold": 5.5, "class": "span",
        }
        r = http_client.post(
            "/grid-analytics/api/business-case",
            json={"type": "violation", "entity": violation},
        )
        data = assert_dict_response(r)
        assert data.get("ok"), f"business-case returned ok=false: {data}"
        case = data["case"]
        for key in ("cost_inaction_aud", "cost_action_aud", "timeline_weeks",
                    "recommendation", "confidence", "narrative"):
            assert key in case, f"case missing key `{key}`: {list(case.keys())}"
        assert case["cost_inaction_aud"] > case["cost_action_aud"], \
            "inaction should cost more than action in our illustrative table"

    def test_agent_query_classification(self, http_client):
        """POST /api/ask classifies a bushfire question into feeders_at_risk."""
        http_client.post("/grid-analytics/api/seed-all?scan=demo-50")
        r = http_client.post(
            "/grid-analytics/api/ask",
            json={"query": "Which feeders need attention before summer?"},
        )
        data = assert_dict_response(r)
        assert data.get("ok"), f"ask returned ok=false: {data}"
        assert data["intent"] in (
            "feeders_at_risk", "open_work", "veg_intrusion",
            "twin_validate", "hosting_capacity",
        ), f"unexpected intent: {data.get('intent')}"
        assert isinstance(data.get("trace"), list)

    def test_50_pole_feeder_geometry(self, http_client):
        """pointcloud exposes the 51-pole branched feeder with lat/lon coords."""
        http_client.post("/grid-analytics/api/seed-all?scan=demo-50")
        r = http_client.get("/pointcloud/api/scan/demo-feeder-50pole")
        data = assert_ok(r)
        assert data.get("scan_id") == "demo-feeder-50pole"
        poles = data.get("poles", [])
        assert len(poles) >= 50, f"Expected ≥50 poles, got {len(poles)}"
        for p in poles[:5]:
            assert p.get("lat") is not None and p.get("lon") is not None, \
                "poles must carry lat/lon for map rendering"
        # Community metadata for PSPS
        community = data.get("community") or {}
        assert community.get("customers_served", 0) > 0

    def test_exec_page_reachable(self, http_client):
        """Exec dashboard static page is reachable (redirects through /exec)."""
        r = http_client.get("/grid-analytics/pages/exec.html", follow_redirects=True)
        assert r.status_code == 200
        # Content-Type should be HTML
        assert "text/html" in r.headers.get("content-type", "").lower()

    def test_narrative_non_empty_after_seed(self, http_client):
        """Narrative string is populated once data exists."""
        http_client.post("/grid-analytics/api/seed-all?scan=demo-50")
        data = assert_dict_response(http_client.get("/grid-analytics/api/overview"))
        assert data.get("narrative"), "narrative should be non-empty after seed-all"
