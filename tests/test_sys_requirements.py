"""System app tests: Requirements — 11 use cases.

Acceptance criteria → tests (from this session's approved requirements-app plan):
  AC1 GET /requirements/api/items returns {items, projects, statuses}
        → test_items_structure
  AC2 POST /requirements/api/items creates a requirement with a REQ-nnn id
        → test_add_flow, test_add_returns_reqid
  AC3 set_field changes a settable field; rejects a non-settable one
        → test_set_status, test_set_field_rejects_unknown
  AC4 detail returns statement + resolved_references (kb fail-soft)
        → test_detail_structure
  AC5 verify with no conformance link returns mode=manual (never errors)
        → test_verify_manual
  AC6 impact returns a requirements list for a reference
        → test_impact_shape
  AC7 GET /requirements/ renders the page
        → test_page_loads
Edge/regression (no AC): test_add_requires_statement, test_unicode_statement
"""

import pytest
from helpers import TEST_PREFIX, assert_dict_response, assert_ok

REQ_PROJECT = TEST_PREFIX + "reqproj"


def _add(http_client, statement, **kw):
    body = {"statement": statement, "project": REQ_PROJECT}
    body.update(kw)
    return http_client.post("/requirements/api/items", json=body)


@pytest.mark.api
class TestRequirementsAPI:
    def test_items_structure(self, http_client):
        data = assert_dict_response(http_client.get("/requirements/api/items"))
        assert "items" in data and isinstance(data["items"], list)
        assert "projects" in data and "statuses" in data
        assert "proposed" in data["statuses"] and "verified" in data["statuses"]

    def test_add_flow(self, http_client):
        data = assert_dict_response(_add(http_client, TEST_PREFIX + "the system shall log in"))
        assert data.get("project") == REQ_PROJECT
        assert data.get("status") == "proposed"

    def test_add_returns_reqid(self, http_client):
        data = assert_dict_response(_add(http_client, TEST_PREFIX + "shall persist state"))
        assert data.get("req_id", "").startswith("REQ-")
        assert "~" in data.get("id", "")  # {project}~{req_id}

    def test_add_requires_statement(self, http_client):
        data = assert_dict_response(_add(http_client, "  "))
        assert "error" in data

    def test_set_status(self, http_client):
        created = assert_dict_response(_add(http_client, TEST_PREFIX + "shall approve"))
        rid = created["id"]
        r = http_client.post(f"/requirements/api/requirements/{rid}/field",
                             json={"field": "status", "value": "approved"})
        assert_ok(r)
        detail = assert_dict_response(http_client.get(f"/requirements/api/requirements/{rid}"))
        assert detail.get("status") == "approved"

    def test_set_field_rejects_unknown(self, http_client):
        created = assert_dict_response(_add(http_client, TEST_PREFIX + "shall guard fields"))
        r = http_client.post(f"/requirements/api/requirements/{created['id']}/field",
                             json={"field": "created", "value": "hacked"})
        assert "error" in assert_dict_response(r)

    def test_detail_structure(self, http_client):
        created = assert_dict_response(
            _add(http_client, TEST_PREFIX + "shall trace", references="IEC 60287-1-1 §2"))
        detail = assert_dict_response(
            http_client.get(f"/requirements/api/requirements/{created['id']}"))
        assert TEST_PREFIX in detail.get("statement", "")
        assert isinstance(detail.get("resolved_references"), list)
        assert detail["resolved_references"][0]["reference"] == "IEC 60287-1-1 §2"

    def test_verify_manual(self, http_client):
        created = assert_dict_response(_add(http_client, TEST_PREFIX + "shall verify manually"))
        data = assert_dict_response(
            http_client.post(f"/requirements/api/requirements/{created['id']}/verify", json={}))
        assert data.get("mode") == "manual"  # no conformance link → manual, never errors

    def test_impact_shape(self, http_client):
        _add(http_client, TEST_PREFIX + "shall cite a clause", references="AS/NZS 3008")
        data = assert_dict_response(
            http_client.get("/requirements/api/impact", params={"reference": "AS/NZS 3008"}))
        assert "requirements" in data and isinstance(data["requirements"], list)

    def test_unicode_statement(self, http_client):
        data = assert_dict_response(_add(http_client, TEST_PREFIX + "需求：系统应记录日志 §3"))
        assert data.get("req_id", "").startswith("REQ-")


@pytest.mark.interactive
class TestRequirementsUI:
    def test_page_loads(self, app_page, page_errors):
        from page_helpers import assert_no_js_errors
        page = app_page("requirements")
        page.wait_for_selector(".req-toolbar")
        assert_no_js_errors(page_errors)

    def test_row_click_opens_detail(self, app_page, page_errors):
        # Exercises the delegated row-click → detail modal path (the XSS-safe
        # refactor: rows carry data-rid, handlers read _currentDetail).
        from page_helpers import assert_no_js_errors
        page = app_page("requirements")
        page.evaluate(
            "() => window.EOS.apiSafe('/requirements/api/items', {method:'POST',"
            " body: JSON.stringify({statement:'" + TEST_PREFIX + "clickrow',"
            " project:'" + REQ_PROJECT + "'})})"
        )
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("tr.req-row", timeout=8000)
        page.click("tr.req-row")
        page.wait_for_selector(".req-detail-field", timeout=5000)  # modal body rendered
        assert_no_js_errors(page_errors)
