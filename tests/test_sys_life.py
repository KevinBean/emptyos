"""System app tests: Life — the Life-suite surface app (dark-by-default pilot).

Contract under test (docs/suites/life-cohesion.md):
- flag off (default): /life/api/timeline answers {enabled: false, items: []}
  and the page renders a disabled note — byte-identical members either way.
- flag on: items merge from every [[contributes.life.timeline]] member,
  ts-descending, each carrying ts/title/kind/href; sources name contributors.
"""

import pytest

from helpers import assert_dict_response
from page_helpers import assert_no_js_errors, wait_briefly


@pytest.mark.api
class TestLifeAPI:
    def test_timeline_dark_or_contract(self, http_client):
        data = assert_dict_response(http_client.get("/life/api/timeline"))
        assert "enabled" in data
        if not data["enabled"]:
            assert data.get("items") == []
            return
        items = data.get("items")
        assert isinstance(items, list)
        for it in items[:20]:
            assert {"ts", "title", "kind", "href"} <= set(it), it
        ts_list = [it["ts"] for it in items]
        assert ts_list == sorted(ts_list, reverse=True), "items not ts-descending"
        assert isinstance(data.get("sources"), list)

    def test_timeline_days_param_clamped(self, http_client):
        """Bad/huge days values never error — clamped server-side."""
        for q in ("?days=nonsense", "?days=99999", "?days=-3"):
            data = assert_dict_response(http_client.get(f"/life/api/timeline{q}"))
            assert "enabled" in data

    def test_surface_owns_no_write_routes(self, http_client):
        """The surface is read-only composition — no POST surface exists."""
        r = http_client.post("/life/api/timeline", json={})
        assert r.status_code in (404, 405)


@pytest.mark.interactive
class TestLifeUI:
    def test_page_loads_without_js_errors(self, app_page, page, page_errors):
        app_page("life")
        wait_briefly(page)
        assert page.locator("#timeline").count() == 1
        assert_no_js_errors(page_errors)
