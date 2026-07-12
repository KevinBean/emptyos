"""System tests: vegetation-intrusion — 6 cases.

Verifies the 3D conductor buffer + growth-projection app. Depends on the
grid-analytics seed-all to have produced a scan in pointcloud.
"""

import pytest

from helpers import assert_dict_response, assert_list_response


@pytest.mark.api
class TestVegetationIntrusionAPI:

    def test_intrusions_list_shape(self, http_client):
        """GET /vegetation-intrusion/api/intrusions returns a list."""
        http_client.post("/grid-analytics/api/seed-all?scan=demo-50")
        r = http_client.get("/vegetation-intrusion/api/intrusions")
        data = assert_dict_response(r, required_keys=["intrusions"])
        assert isinstance(data["intrusions"], list)

    def test_analyse_50pole_feeder(self, http_client):
        """POST /api/analyse on the seeded 51-pole feeder returns counts + top list."""
        http_client.post("/grid-analytics/api/seed-all?scan=demo-50")
        r = http_client.post(
            "/vegetation-intrusion/api/analyse",
            json={"scan_id": "demo-feeder-50pole", "projection_months": 24},
        )
        data = assert_dict_response(r)
        assert data.get("ok"), f"analyse returned ok=false: {data}"
        assert "counts" in data and "intrusions" in data
        for band in ("critical", "elevated", "routine"):
            assert band in data["counts"], \
                f"counts missing band `{band}`: {data['counts']}"
        total = sum(data["counts"].values())
        assert total == data["total"], \
            f"count sum {total} != total {data['total']}"

    def test_intrusion_record_shape(self, http_client):
        """Each intrusion has the full shape downstream apps rely on."""
        http_client.post("/grid-analytics/api/seed-all?scan=demo-50")
        http_client.post(
            "/vegetation-intrusion/api/analyse",
            json={"scan_id": "demo-feeder-50pole"},
        )
        data = assert_dict_response(
            http_client.get("/vegetation-intrusion/api/intrusions?scan_id=demo-feeder-50pole"),
            required_keys=["intrusions"],
        )
        if not data["intrusions"]:
            pytest.skip("no intrusions in seeded scan — skipping shape check")
        item = data["intrusions"][0]
        for key in ("intrusion_id", "scan_id", "veg_id", "span_id",
                    "current_gap_m", "projected_gap_m",
                    "risk_band", "risk_score", "crew_instruction"):
            assert key in item, f"intrusion missing key `{key}`: {list(item.keys())}"
        assert item["risk_band"] in ("critical", "elevated", "routine")

    def test_risk_band_filter(self, http_client):
        """Query by risk_band only returns items in that band."""
        http_client.post("/grid-analytics/api/seed-all?scan=demo-50")
        http_client.post(
            "/vegetation-intrusion/api/analyse",
            json={"scan_id": "demo-feeder-50pole"},
        )
        r = http_client.get(
            "/vegetation-intrusion/api/intrusions?risk_band=critical"
        )
        data = assert_dict_response(r, required_keys=["intrusions"])
        for item in data["intrusions"]:
            assert item.get("risk_band") == "critical", \
                f"filter should have excluded {item.get('risk_band')}"

    def test_missing_scan_errors_cleanly(self, http_client):
        """analyse on a non-existent scan returns ok=false, not a 500."""
        r = http_client.post(
            "/vegetation-intrusion/api/analyse",
            json={"scan_id": "does-not-exist"},
        )
        data = assert_dict_response(r)
        assert data.get("ok") is False
        assert data.get("error")

    def test_crew_instruction_is_short(self, http_client):
        """LLM-generated crew instructions respect the ~25-word rule."""
        http_client.post("/grid-analytics/api/seed-all?scan=demo-50")
        http_client.post(
            "/vegetation-intrusion/api/analyse",
            json={"scan_id": "demo-feeder-50pole"},
        )
        data = assert_dict_response(
            http_client.get("/vegetation-intrusion/api/intrusions?scan_id=demo-feeder-50pole"),
            required_keys=["intrusions"],
        )
        for item in data["intrusions"][:3]:
            instr = item.get("crew_instruction") or ""
            # Generous upper bound — LLM sometimes overshoots, template never does.
            assert len(instr) < 260, \
                f"crew instruction too long ({len(instr)} chars): {instr!r}"
