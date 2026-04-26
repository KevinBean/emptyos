"""System tests: cer-hosting — 5 cases.

Covers the feeder hosting-capacity analyser and the LLM-backed customer
connection response.
"""

import pytest

from helpers import assert_dict_response


@pytest.mark.api
class TestCERHostingAPI:

    def test_analyse_feeder(self, http_client):
        """POST /cer-hosting/api/analyse produces per-pole and feeder totals."""
        http_client.post("/grid-analytics/api/seed-all?scan=demo-50")
        r = http_client.post(
            "/cer-hosting/api/analyse",
            json={"scan_id": "demo-feeder-50pole"},
        )
        data = assert_dict_response(r)
        assert data.get("ok"), f"analyse returned ok=false: {data}"
        for key in ("per_pole", "feeder_total_kw", "hv_total_kw",
                    "lv_total_kw", "pole_count"):
            assert key in data, f"analysis missing `{key}`: {list(data.keys())}"
        assert data["pole_count"] >= 50
        assert data["feeder_total_kw"] > 0

    def test_per_pole_row_shape(self, http_client):
        """Each per-pole record has distance, voltage class, limit factor."""
        http_client.post("/grid-analytics/api/seed-all?scan=demo-50")
        r = http_client.post(
            "/cer-hosting/api/analyse",
            json={"scan_id": "demo-feeder-50pole"},
        )
        data = assert_dict_response(r)
        row = data["per_pole"][0]
        for k in ("pole_id", "distance_km", "voltage_class_kv", "max_kw", "limit"):
            assert k in row, f"per-pole row missing `{k}`: {row}"
        assert row["max_kw"] > 0
        # Limit factor must be one of the named strings from the app
        assert any(kw in row["limit"] for kw in ("voltage", "thermal", "cap")), \
            f"limit factor unexpected: {row['limit']!r}"

    def test_get_cached_analysis(self, http_client):
        """GET /api/analysis/{scan_id} returns prior analysis."""
        http_client.post("/grid-analytics/api/seed-all?scan=demo-50")
        http_client.post(
            "/cer-hosting/api/analyse",
            json={"scan_id": "demo-feeder-50pole"},
        )
        r = http_client.get("/cer-hosting/api/analysis/demo-feeder-50pole")
        data = assert_dict_response(r)
        assert data.get("pole_count", 0) >= 50

    def test_customer_response_verdict(self, http_client):
        """POST /api/customer-response returns verdict + LLM-written paragraph."""
        http_client.post("/grid-analytics/api/seed-all?scan=demo-50")
        http_client.post(
            "/cer-hosting/api/analyse",
            json={"scan_id": "demo-feeder-50pole"},
        )
        # Pick a pole from the analysis
        analysis = http_client.get("/cer-hosting/api/analysis/demo-feeder-50pole").json()
        pole_id = analysis["per_pole"][0]["pole_id"]
        r = http_client.post(
            "/cer-hosting/api/customer-response",
            json={"scan_id": "demo-feeder-50pole",
                  "pole_id": pole_id,
                  "requested_kw": 10.0,
                  "customer_name": "TestCustomer"},
        )
        data = assert_dict_response(r)
        assert data.get("ok"), f"customer-response returned ok=false: {data}"
        req = data["request"]
        assert req["verdict"] in ("approved", "exceeds")
        assert req["response"]  # LLM or template fallback

    def test_invalid_pole_errors(self, http_client):
        """Customer response with unknown pole_id errors cleanly."""
        http_client.post("/grid-analytics/api/seed-all?scan=demo-50")
        http_client.post(
            "/cer-hosting/api/analyse",
            json={"scan_id": "demo-feeder-50pole"},
        )
        r = http_client.post(
            "/cer-hosting/api/customer-response",
            json={"scan_id": "demo-feeder-50pole",
                  "pole_id": "NOT-A-POLE",
                  "requested_kw": 5.0},
        )
        data = assert_dict_response(r)
        assert data.get("ok") is False
        assert data.get("error")
