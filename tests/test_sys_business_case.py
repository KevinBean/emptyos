"""System app tests: Business Case — value-proof ROI one-pager.

The app is a deterministic calculator + PDF render. `/api/compute` is pure
(no writes) so it's exercised exhaustively here; `/api/render` is covered for
its *validation* paths only — the happy path writes a vault note + PDF (no
list/delete endpoint to clean up, and the lower-kebab slug dodges the
TEST_PREFIX leak guard), and is already covered by the unit suite
(`test_unit_business_case.py`) plus manual live verification. So this layer
asserts the HTTP contract + UI, not the destructive write.
"""

import pytest

from helpers import assert_ok, requires_app
from page_helpers import assert_no_js_errors, wait_briefly

# A closed tool (apps/extension/business/): not in the public snapshot, whose
# daemon would 404 every route here.
pytestmark = requires_app("business-case")

_BASE = "/business-case"
_VALID = {"manual_hours": 9, "hourly_rate": 170, "tool_minutes": 40,
          "units_per_month": 6, "pilot_price": 1500}


@pytest.mark.api
class TestBusinessCaseAPI:
    def test_index_loads(self, http_client):
        resp = http_client.get(f"{_BASE}/")
        assert resp.status_code == 200
        assert "Business Case" in resp.text

    def test_compute_ok(self, http_client):
        d = assert_ok(http_client.post(f"{_BASE}/api/compute", json=_VALID))
        assert d["ok"] is True
        c = d["computed"]
        assert c["hours_saved_per_unit"] == pytest.approx(8.33, abs=0.01)
        assert c["pct_saved"] == pytest.approx(92.6, abs=0.1)

    def test_compute_value_fields_present(self, http_client):
        c = assert_ok(http_client.post(f"{_BASE}/api/compute", json=_VALID))["computed"]
        for k in ("monthly_cost_saved", "annual_cost_saved", "payback_units", "roi_multiple"):
            assert k in c, f"computed missing {k}"
        assert c["roi_multiple"] == pytest.approx(5.7, abs=0.1)

    def test_compute_clamps_negatives(self, http_client):
        c = assert_ok(http_client.post(f"{_BASE}/api/compute",
                      json={"manual_hours": -5, "hourly_rate": -10, "tool_minutes": -3}))["computed"]
        assert c["manual_hours"] == 0.0
        assert c["hours_saved_per_unit"] == 0.0

    def test_compute_zero_manual_no_divide(self, http_client):
        c = assert_ok(http_client.post(f"{_BASE}/api/compute",
                      json={"manual_hours": 0, "hourly_rate": 100, "tool_minutes": 10}))["computed"]
        assert c["pct_saved"] == 0.0

    def test_compute_no_pilot_nulls_payback(self, http_client):
        c = assert_ok(http_client.post(f"{_BASE}/api/compute",
                      json={"manual_hours": 8, "hourly_rate": 150, "tool_minutes": 30}))["computed"]
        assert c["payback_units"] is None
        assert c["roi_multiple"] is None

    def test_render_missing_task_errors(self, http_client):
        d = assert_ok(http_client.post(f"{_BASE}/api/render", json=_VALID))  # no "task"
        assert d["ok"] is False
        assert "task" in d["error"].lower()

    def test_render_no_saving_errors(self, http_client):
        d = assert_ok(http_client.post(f"{_BASE}/api/render",
                      json={"task": "X", "manual_hours": 0.5, "hourly_rate": 100, "tool_minutes": 60}))
        assert d["ok"] is False
        assert "saved" in d["error"].lower()


@pytest.mark.interactive
class TestBusinessCaseUI:
    def test_ui_loads(self, app_page, page_errors):
        page = app_page("business-case")
        wait_briefly(page, 1500)
        assert "Business Case" in page.content()
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_form_and_generate_present(self, app_page, page_errors):
        page = app_page("business-case")
        wait_briefly(page, 1500)
        assert page.locator("#bc-task").is_visible()
        assert page.locator("#bc-manual-hours").is_visible()
        assert page.get_by_text("Generate one-pager").is_visible()
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])
