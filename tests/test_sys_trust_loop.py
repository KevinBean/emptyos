"""System app tests: Trust Loop — 12 use cases.

Acceptance criteria → tests (spec decided in-session, 2026-08-08; the app
demonstrates the trustworthy-engineering loop from the article draft):
  AC1 POST /trust-loop/api/calc reproduces the IEEE 80 published case
      (188.65 V touch / 262.48 V step)                → test_anchor_case_touch_step
  AC2 Every result carries compute provenance (method + version, no model)
                                                      → test_calc_provenance
  AC3 Surface layer derates via C_s < 1               → test_surface_layer_derates
  AC4 Out-of-window inputs are refused, not guessed   → test_invalid_duration_refused
  AC5 GET /trust-loop/api/methods lists the one registered method
                                                      → test_methods_listing
  AC6 GET /trust-loop/api/conformance lists the declared case
                                                      → test_conformance_listing
  AC7 POST /trust-loop/api/conformance/run passes the gate
                                                      → test_conformance_gate_passes
  AC8 GET /trust-loop/ renders without JS errors      → test_page_loads
  AC9 "Load the published case" fills the form and shows the anchor numbers
                                                      → test_anchor_button_flow
  AC10 Editing an input live-recomputes the result    → test_live_recompute
Edge/regression (no AC): test_unknown_method_refused, test_empty_body_is_error
"""

import pytest
from helpers import assert_dict_response
from page_helpers import assert_no_js_errors


@pytest.mark.api
class TestTrustLoopAPI:
    def test_anchor_case_touch_step(self, http_client):
        r = http_client.post(
            "/trust-loop/api/calc", json={"rho": 100, "t_s": 0.5, "body_kg": 50}
        )
        data = assert_dict_response(r)
        assert data["result"]["e_touch_v"] == pytest.approx(188.65, rel=0.005)
        assert data["result"]["e_step_v"] == pytest.approx(262.48, rel=0.005)

    def test_calc_provenance(self, http_client):
        r = http_client.post(
            "/trust-loop/api/calc", json={"rho": 100, "t_s": 0.5, "body_kg": 50}
        )
        data = assert_dict_response(r)
        assert data["provenance"]["method"] == "ieee80_closed_form"
        assert data["provenance"].get("method_version")

    def test_surface_layer_derates(self, http_client):
        r = http_client.post(
            "/trust-loop/api/calc",
            json={"rho": 100, "t_s": 0.5, "body_kg": 50, "rho_s": 2500, "h_s": 0.1},
        )
        data = assert_dict_response(r)
        assert 0 < data["result"]["c_s"] < 1
        assert data["result"]["e_touch_v"] > 188.66  # layer raises the tolerable limit

    def test_invalid_duration_refused(self, http_client):
        r = http_client.post(
            "/trust-loop/api/calc", json={"rho": 100, "t_s": 99, "body_kg": 50}
        )
        data = assert_dict_response(r)
        assert "error" in data

    def test_unknown_method_refused(self, http_client):
        r = http_client.post(
            "/trust-loop/api/calc",
            json={"rho": 100, "t_s": 0.5, "method": "definitely-not-a-method"},
        )
        data = assert_dict_response(r)
        assert "error" in data

    def test_empty_body_is_error(self, http_client):
        r = http_client.post("/trust-loop/api/calc", json={})
        data = assert_dict_response(r)
        assert "error" in data

    def test_methods_listing(self, http_client):
        data = assert_dict_response(http_client.get("/trust-loop/api/methods"))
        ids = [m["id"] for m in data["tolerable"]]
        assert ids == ["ieee80_closed_form"]

    def test_conformance_listing(self, http_client):
        data = assert_dict_response(http_client.get("/trust-loop/api/conformance"))
        case_ids = [c["case_id"] for c in data["cases"]]
        assert "ieee80-50kg-rho100-t05" in case_ids

    def test_conformance_gate_passes(self, http_client):
        r = http_client.post("/trust-loop/api/conformance/run", json={})
        data = assert_dict_response(r)
        assert data["results"], "gate ran no cases"
        assert all(res["passed"] for res in data["results"])


@pytest.mark.interactive
class TestTrustLoopUI:
    def test_page_loads(self, page, base_url, page_errors):
        page.goto(base_url + "/trust-loop/")
        page.wait_for_selector("#loop-strip .tl-stage")
        assert_no_js_errors(page_errors)

    def test_anchor_button_flow(self, page, base_url, page_errors):
        page.goto(base_url + "/trust-loop/")
        page.click("#btn-anchor")
        page.wait_for_function(
            "document.getElementById('out-touch').textContent.startsWith('188.6')"
        )
        assert page.locator("#out-step").text_content().startswith("262.4")
        assert_no_js_errors(page_errors)

    def test_live_recompute(self, page, base_url, page_errors):
        page.goto(base_url + "/trust-loop/")
        page.click("#btn-anchor")
        page.wait_for_function(
            "document.getElementById('out-touch').textContent.startsWith('188.6')"
        )
        page.fill("#rho", "1000")
        page.wait_for_function(
            "!document.getElementById('out-touch').textContent.startsWith('188.6')"
        )
        assert_no_js_errors(page_errors)
