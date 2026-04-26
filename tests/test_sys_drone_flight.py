"""System tests: drone-flight planner — 5 cases."""

import pytest

from helpers import assert_dict_response, assert_ok


@pytest.mark.api
class TestDroneFlightAPI:

    def test_plan_route_returns_waypoints(self, http_client):
        """POST /drone-flight/api/plan produces a route with waypoints + timing."""
        http_client.post("/grid-analytics/api/seed-all?scan=demo-50")
        r = http_client.post(
            "/drone-flight/api/plan",
            json={"scan_id": "demo-feeder-50pole"},
        )
        data = assert_dict_response(r)
        assert data.get("ok"), f"plan returned ok=false: {data}"
        route = data["route"]
        for key in ("route_id", "waypoints", "distance_m",
                    "flight_time_min", "envelope", "warnings"):
            assert key in route, f"route missing key `{key}`: {list(route.keys())}"
        assert len(route["waypoints"]) >= 1
        assert route["distance_m"] >= 0
        assert route["flight_time_min"] > 0

    def test_waypoint_envelope_enforced(self, http_client):
        """Every waypoint z is >= AGL_MIN (20 m) and <= ceiling (120 m)."""
        http_client.post("/grid-analytics/api/seed-all?scan=demo-50")
        r = http_client.post(
            "/drone-flight/api/plan",
            json={"scan_id": "demo-feeder-50pole"},
        )
        data = assert_dict_response(r)
        route = data["route"]
        for wp in route["waypoints"]:
            assert wp["z"] >= 20.0 - 0.01, f"waypoint below AGL_MIN: {wp}"
            assert wp["z"] <= 120.0 + 0.01, f"waypoint above ceiling: {wp}"

    def test_route_list(self, http_client):
        """Routes persisted across /api/routes."""
        http_client.post("/grid-analytics/api/seed-all?scan=demo-50")
        http_client.post(
            "/drone-flight/api/plan",
            json={"scan_id": "demo-feeder-50pole"},
        )
        data = assert_dict_response(
            http_client.get("/drone-flight/api/routes"),
            required_keys=["routes"],
        )
        assert len(data["routes"]) >= 1

    def test_kml_export(self, http_client):
        """Per-route KML export returns text/xml compatible response."""
        http_client.post("/grid-analytics/api/seed-all?scan=demo-50")
        plan = http_client.post(
            "/drone-flight/api/plan",
            json={"scan_id": "demo-feeder-50pole"},
        )
        route_id = plan.json()["route"]["route_id"]
        r = http_client.get(f"/drone-flight/api/route/{route_id}/kml")
        assert r.status_code == 200
        body = r.text
        assert "<kml" in body and "<LineString" in body, \
            "KML should contain kml + LineString elements"
        assert "<Placemark>" in body

    def test_missing_scan_errors_cleanly(self, http_client):
        """Plan on missing scan returns ok=false, not a 500."""
        r = http_client.post(
            "/drone-flight/api/plan",
            json={"scan_id": "does-not-exist"},
        )
        data = assert_dict_response(r)
        assert data.get("ok") is False
        assert data.get("error")
