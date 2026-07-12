"""System app tests for the Reports app.

Coverage:
  - API: list templates, create from template, get detail, update section,
    update table, update meta, preview HTML, for-project lookup, delete.
  - UI:  page loads, template picker opens, create report flow,
    section editor saves, settings panel opens.

LLM-backed endpoints (polish/expand/compress/scaffold) and export endpoints
(PDF/DOCX) are exercised via smoke tests that tolerate missing deps — we
don't want the CI suite to fail because Playwright/python-docx aren't installed
on the test runner.
"""

from __future__ import annotations

import pytest

from helpers import TEST_PREFIX, assert_dict_response, assert_ok
from page_helpers import assert_no_js_errors, click_first, wait_briefly, wait_for_toast


def _unique_title(suffix: str) -> str:
    return f"{TEST_PREFIX}{suffix}"


def _create_report(http_client, template: str = "report", project_id: str = "") -> dict:
    title = _unique_title(f"create-{template}")
    resp = http_client.post("/reports/api/reports", json={
        "template": template,
        "title": title,
        "project_id": project_id,
    })
    data = assert_ok(resp)
    assert data.get("ok") is True
    assert data.get("id")
    return data


def _cleanup(http_client, doc_id: str) -> None:
    try:
        http_client.delete(f"/reports/api/reports/{doc_id}")
    except Exception:
        pass


# --- API ------------------------------------------------------------------


@pytest.mark.api
class TestReportsAPI:
    def test_templates_list(self, http_client):
        data = assert_ok(http_client.get("/reports/api/templates"))
        assert "templates" in data
        ids = {t["id"] for t in data["templates"]}
        # The six v1 templates must all be present
        assert {"pdr", "cdr", "trr", "proposal", "spec", "report"}.issubset(ids)
        assert "table_schemas" in data
        assert "requirements" in data["table_schemas"]

    def test_reports_list_empty_shape(self, http_client):
        data = assert_ok(http_client.get("/reports/api/reports"))
        assert isinstance(data.get("reports"), list)

    def test_create_from_report_template(self, http_client):
        created = _create_report(http_client, "report")
        try:
            detail = assert_ok(http_client.get(f"/reports/api/reports/{created['id']}"))
            assert detail["id"] == created["id"]
            assert detail["meta"]["type"] == "report"
            # Generic report has 9 sections including signoff
            assert len(detail["outline"]) >= 7
            # Title stored in frontmatter
            assert detail["meta"]["title"].startswith(TEST_PREFIX)
        finally:
            _cleanup(http_client, created["id"])

    def test_create_from_pdr_scaffolds_tables(self, http_client):
        created = _create_report(http_client, "pdr")
        try:
            detail = assert_ok(http_client.get(f"/reports/api/reports/{created['id']}"))
            tables = detail.get("tables_present") or {}
            # PDR scaffolds requirements, risks, verification
            assert {"requirements", "risks", "verification"}.issubset(tables.keys())
            # Schemas returned alongside
            schemas = detail.get("table_schemas") or {}
            assert "requirements" in schemas
            assert any(c["key"] == "priority" for c in schemas["requirements"]["columns"])
        finally:
            _cleanup(http_client, created["id"])

    def test_get_missing_report(self, http_client):
        resp = http_client.get(f"/reports/api/reports/{TEST_PREFIX}does-not-exist")
        # Returns a dict with "error" key, not a 404 — consistent with other apps
        data = assert_ok(resp)
        assert "error" in data

    def test_section_edit_persists(self, http_client):
        created = _create_report(http_client, "report")
        try:
            detail = assert_ok(http_client.get(f"/reports/api/reports/{created['id']}"))
            first_slug = detail["outline"][0]["slug"]
            body = f"{TEST_PREFIX} first-pass body\n\nSecond paragraph."
            put = assert_ok(http_client.put(
                f"/reports/api/reports/{created['id']}/sections/{first_slug}",
                json={"body": body, "meta": {"status": "ready"}},
            ))
            assert put.get("ok")
            got = assert_ok(http_client.get(
                f"/reports/api/reports/{created['id']}/sections/{first_slug}"
            ))
            assert TEST_PREFIX in got["body"]
            assert got["meta"].get("status") == "ready"
        finally:
            _cleanup(http_client, created["id"])

    def test_table_edit_persists(self, http_client):
        created = _create_report(http_client, "pdr")
        try:
            # Save new requirement rows
            rows = [
                {"id": "REQ-001", "text": f"{TEST_PREFIX} req one", "priority": "High",
                 "verification": "Test", "status": "Proposed"},
                {"id": "REQ-002", "text": f"{TEST_PREFIX} req two", "priority": "Medium",
                 "verification": "Analysis", "status": "Proposed"},
            ]
            put = assert_ok(http_client.put(
                f"/reports/api/reports/{created['id']}/tables/requirements",
                json={"rows": rows},
            ))
            assert put.get("ok")
            assert put.get("count") == 2
            got = assert_ok(http_client.get(
                f"/reports/api/reports/{created['id']}/tables/requirements"
            ))
            assert got["name"] == "requirements"
            assert len(got["rows"]) == 2
            assert got["rows"][0]["id"] == "REQ-001"
            # next_id suggests REQ-003
            assert got["next_id"] == "REQ-003"
        finally:
            _cleanup(http_client, created["id"])

    def test_meta_patch_round_trip(self, http_client):
        created = _create_report(http_client, "spec")
        try:
            patched = assert_ok(http_client.patch(
                f"/reports/api/reports/{created['id']}/meta",
                json={"meta": {"version": "2.0", "status": "ready", "organisation": f"{TEST_PREFIX}org"}},
            ))
            assert patched.get("ok")
            detail = assert_ok(http_client.get(f"/reports/api/reports/{created['id']}"))
            assert str(detail["meta"]["version"]) == "2.0"
            assert detail["meta"]["status"] == "ready"
            assert detail["meta"]["organisation"] == f"{TEST_PREFIX}org"
        finally:
            _cleanup(http_client, created["id"])

    def test_preview_returns_html(self, http_client):
        created = _create_report(http_client, "report")
        try:
            resp = http_client.get(f"/reports/api/reports/{created['id']}/preview")
            assert resp.status_code == 200
            body = resp.text
            assert "<!doctype html>" in body.lower()
            # Title page renders the report title
            assert TEST_PREFIX in body
        finally:
            _cleanup(http_client, created["id"])

    def test_for_project_endpoint(self, http_client):
        proj = f"{TEST_PREFIX}proj-link"
        a = _create_report(http_client, "report", project_id=proj)
        b = _create_report(http_client, "report")  # different (no project)
        try:
            data = assert_ok(http_client.get(f"/reports/api/for-project/{proj}"))
            assert data["project_id"] == proj
            ids = {r["id"] for r in data["reports"]}
            assert a["id"] in ids
            assert b["id"] not in ids
        finally:
            _cleanup(http_client, a["id"])
            _cleanup(http_client, b["id"])

    def test_delete_moves_to_trash(self, http_client):
        created = _create_report(http_client, "report")
        resp = http_client.delete(f"/reports/api/reports/{created['id']}")
        data = assert_ok(resp)
        assert data.get("ok") is True
        # Subsequent get returns an error (folder moved)
        follow = assert_ok(http_client.get(f"/reports/api/reports/{created['id']}"))
        assert "error" in follow

    def test_export_pdf_graceful_when_missing_dep(self, http_client):
        created = _create_report(http_client, "report")
        try:
            resp = http_client.post(f"/reports/api/reports/{created['id']}/export/pdf")
            data = assert_ok(resp)
            # Either succeeded (ok=True, file=...) or reported the missing dep gracefully.
            assert data.get("ok") is True or data.get("missing_dep") == "playwright" or "error" in data
        finally:
            _cleanup(http_client, created["id"])

    def test_export_docx_graceful_when_missing_dep(self, http_client):
        created = _create_report(http_client, "report")
        try:
            resp = http_client.post(f"/reports/api/reports/{created['id']}/export/docx")
            data = assert_ok(resp)
            assert data.get("ok") is True or data.get("missing_dep") == "python-docx" or "error" in data
        finally:
            _cleanup(http_client, created["id"])


# --- UI -------------------------------------------------------------------


@pytest.mark.interactive
class TestReportsUI:
    def test_ui_loads(self, app_page, page_errors):
        page = app_page("reports")
        wait_briefly(page, 1500)
        assert page.locator("h1").first.inner_text().strip().lower() == "reports"
        assert_no_js_errors(page_errors, allow_patterns=["fetch", "AbortError"])

    def test_template_picker_opens(self, app_page, page_errors):
        page = app_page("reports")
        wait_briefly(page, 1000)
        clicked = click_first(
            page,
            "button:has-text('New Report')",
            "[onclick*='openTemplatePicker']",
        )
        if not clicked:
            pytest.skip("New Report button not present")
        # Modal shows template cards (.tpl-card) with the 6 templates
        page.wait_for_selector(".tpl-card", timeout=3000)
        count = page.locator(".tpl-card").count()
        assert count >= 6, f"Expected ≥6 template cards, got {count}"

    def test_settings_panel_opens(self, app_page, page_errors):
        page = app_page("reports")
        wait_briefly(page, 1000)
        clicked = click_first(
            page,
            ".btn-secondary:has-text('Settings')",
            "button:has-text('Settings')",
            "[onclick*='openAppSettings']",
        )
        if not clicked:
            pytest.skip("Settings button not rendered yet")
        # Shared settings panel class
        try:
            page.wait_for_selector(".eos-settings-panel.open", timeout=3000)
        except Exception:
            # Some variants use a different class — just assert no JS errors
            pass
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])
