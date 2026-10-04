"""System tests for the HV Clearance & Layout app (AS 2067:2016).

API tests hit the live daemon via http_client; UI tests drive the page via
app_page. Pure-compute app — no vault writes, so no TEST_PREFIX cleanup needed.
"""

from __future__ import annotations

import pytest

from helpers import assert_dict_response
from page_helpers import assert_no_js_errors

PREFIX = "/hv-clearance"


@pytest.mark.api
class TestHvClearanceAPI:
    def test_voltages_lists_common_levels(self, http_client):
        data = assert_dict_response(http_client.get(f"{PREFIX}/api/voltages"))
        vs = [v["voltage_kv"] for v in data["voltages"]]
        for v in (11, 33, 66, 132, 275):
            assert v in vs
        assert any(c["id"] == "phase_opposition" for c in data["special_conditions"])
        # designer needs section_s_m per voltage to draw envelopes on placement
        row132 = next(v for v in data["voltages"] if v["voltage_kv"] == 132)
        assert row132["section_s_m"] == 3.65

    def test_voltages_conductor_geometry(self, http_client):
        data = assert_dict_response(http_client.get(f"{PREFIX}/api/voltages?geometry=conductor"))
        assert data["geometry"] == "conductor"
        assert all(v["voltage_kv"] >= 275 for v in data["voltages"])

    def test_calc_33kv_matches_table(self, http_client):
        d = assert_dict_response(http_client.post(f"{PREFIX}/api/calc", json={"voltage_kv": 33}))
        assert d["phase_earth_mm"] == 320
        assert d["phase_phase_mm"] == 370
        assert d["non_flashover_n_mm"] == 350
        assert d["section_safety_s_mm"] == 350 + 2440
        assert d["horizontal_work_h_mm"] == 350 + 1900
        assert d["vertical_work_v_mm"] == 350 + 1340
        assert d["transport"]["s_walkway_mm"] == 350 + 2440

    def test_calc_higher_bil_via_bil_kv(self, http_client):
        d = assert_dict_response(http_client.post(f"{PREFIX}/api/calc", json={"voltage_kv": 11, "bil_kv": 95}))
        assert d["higher_bil"] is True
        assert d["phase_earth_mm"] == 160

    def test_calc_altitude_correction(self, http_client):
        base = assert_dict_response(http_client.post(f"{PREFIX}/api/calc", json={"voltage_kv": 132}))
        high = assert_dict_response(
            http_client.post(f"{PREFIX}/api/calc", json={"voltage_kv": 132, "altitude_m": 2000})
        )
        assert high["phase_earth_mm"] > base["phase_earth_mm"]
        assert high["altitude_factor"] > 1.0

    def test_calc_special_condition_multiplier(self, http_client):
        base = assert_dict_response(http_client.post(f"{PREFIX}/api/calc", json={"voltage_kv": 66}))
        po = assert_dict_response(
            http_client.post(f"{PREFIX}/api/calc", json={"voltage_kv": 66, "special_condition": "phase_opposition"})
        )
        assert abs(po["phase_earth_mm"] - base["phase_earth_mm"] * 1.20) < 0.5

    def test_calc_unknown_voltage_errors(self, http_client):
        d = assert_dict_response(http_client.post(f"{PREFIX}/api/calc", json={"voltage_kv": 13.8}))
        assert "error" in d

    def test_calc_missing_voltage_errors(self, http_client):
        d = assert_dict_response(http_client.post(f"{PREFIX}/api/calc", json={}))
        assert "error" in d

    def test_designer_check_clear_layout(self, http_client):
        # one 11 kV item (S = 130+2440 = 2570 mm = 2.57 m); path 5 m away → clear
        body = {
            "equipment": [{"id": "e1", "label": "11 kV", "voltage_kv": 11, "x": 0, "y": 0}],
            "paths": [{"id": "p1", "label": "road", "kind": "road", "points": [[5, -5], [5, 5]]}],
        }
        d = assert_dict_response(http_client.post(f"{PREFIX}/api/designer/check", json=body))
        assert d["ok"] is True
        assert d["infringements"] == []
        assert d["items"][0]["section_s_m"] == 2.57

    def test_designer_check_infringement(self, http_client):
        # path 1 m from a 132 kV item (S = 3.65 m) → infringes
        body = {
            "equipment": [{"id": "e1", "label": "132 kV", "voltage_kv": 132, "x": 0, "y": 0}],
            "paths": [{"id": "p1", "label": "walkway", "kind": "walkway", "points": [[1, -3], [1, 3]]}],
        }
        d = assert_dict_response(http_client.post(f"{PREFIX}/api/designer/check", json=body))
        assert d["ok"] is False
        assert len(d["infringements"]) == 1
        inf = d["infringements"][0]
        assert inf["equipment_id"] == "e1" and inf["path_id"] == "p1"
        assert inf["actual_m"] == 1.0
        assert inf["shortfall_m"] > 0


@pytest.mark.interactive
class TestHvClearanceUI:
    def test_page_loads_no_js_errors(self, app_page, page_errors):
        page = app_page("hv-clearance")
        page.wait_for_selector("#c-results .hv-cltable")
        assert_no_js_errors(page_errors)

    def test_calculator_renders_results(self, app_page):
        page = app_page("hv-clearance")
        page.wait_for_selector("#c-results .hv-cltable")
        assert "Section safety" in page.inner_text("#c-results")

    def test_designer_tab_switches(self, app_page):
        page = app_page("hv-clearance")
        page.click('.hv-tab[data-tab="design"]')
        page.wait_for_selector("#hv-svg", state="visible")
        assert page.is_visible("#tab-design")

    def test_shared_escape_preserves_numeric_zero(self, app_page):
        """An exact clash is 0 m, not an empty value in the infringement text."""
        page = app_page("hv-clearance")
        assert page.evaluate("window.esc(0)") == "0"

    def test_calculator_hands_voltage_to_substation_design(self, app_page):
        page = app_page("hv-clearance")
        page.select_option("#c-voltage", "132")
        href = page.get_attribute("#c-substation", "href")
        assert href is not None
        assert "layout=substation-design" in href
        assert "voltage_kv=132" in href
