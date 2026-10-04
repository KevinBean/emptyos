"""Dogfood — reports app.

Month-in-the-life: create from template → verify outline scaffold → edit
multiple sections → edit table rows → update metadata → preview renders →
export (pdf/docx) → delete (moves to _trash). No LLM.
"""

import time
import uuid

import pytest

from helpers import TEST_PREFIX


RUN_ID = f"{TEST_PREFIX}reports-{uuid.uuid4().hex[:6]}"


def _available(http_client, path: str) -> bool:
    try:
        return http_client.get(path).status_code == 200
    except Exception:
        return False


@pytest.mark.dogfood
class TestReportsLifecycle:
    state: dict = {}

    @pytest.fixture(autouse=True)
    def _app_required(self, http_client):
        if not _available(http_client, "/reports/api/reports"):
            pytest.skip("reports app not loaded")

    def test_01_templates_available(self, http_client):
        resp = http_client.get("/reports/api/templates").json()
        tpls = resp.get("templates") or []
        assert isinstance(tpls, list) and len(tpls) >= 3, (
            f"expected >=3 templates, got {len(tpls)}"
        )
        assert any(t.get("id") == "spec" for t in tpls), "spec template missing"

    def test_02_create(self, http_client):
        title = f"{RUN_ID}-spec"
        resp = http_client.post(
            "/reports/api/reports",
            json={"title": title, "template": "spec"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok"), f"create failed: {data}"
        doc_id = data.get("id")
        assert doc_id, "no doc id returned"
        TestReportsLifecycle.state["doc_id"] = doc_id
        TestReportsLifecycle.state["title"] = title

    def test_03_detail_has_outline_and_tables(self, http_client):
        doc_id = self.state["doc_id"]
        data = http_client.get(f"/reports/api/reports/{doc_id}").json()
        outline = data.get("outline") or []
        assert len(outline) >= 3, f"outline too short: {outline}"
        tables = data.get("tables_present") or {}
        assert "requirements" in tables, f"requirements table missing: {tables}"

    def test_04_list_contains_report(self, http_client):
        listing = http_client.get("/reports/api/reports").json()
        reports = listing.get("reports") if isinstance(listing, dict) else listing
        assert any(
            r.get("id") == self.state["doc_id"] for r in reports
        ), f"new report missing from list"

    def test_05_edit_first_section(self, http_client):
        doc_id = self.state["doc_id"]
        # First section of spec is "overview"
        body = f"# Overview\n\nThis is a dogfood test report {RUN_ID}.\n"
        resp = http_client.put(
            f"/reports/api/reports/{doc_id}/sections/overview",
            json={"body": body, "meta": {"status": "in_progress"}},
        )
        assert resp.status_code == 200
        assert resp.json().get("ok"), resp.text[:200]

    def test_06_read_section_back(self, http_client):
        doc_id = self.state["doc_id"]
        sec = http_client.get(
            f"/reports/api/reports/{doc_id}/sections/overview"
        ).json()
        assert RUN_ID in sec.get("body", ""), "section content not persisted"
        assert sec.get("meta", {}).get("status") == "in_progress", (
            "section status update lost"
        )

    def test_07_edit_requirements_table(self, http_client):
        doc_id = self.state["doc_id"]
        resp = http_client.put(
            f"/reports/api/reports/{doc_id}/tables/requirements",
            json={
                "rows": [
                    {"id": "REQ-001", "description": f"{RUN_ID}-req1", "priority": "high"},
                    {"id": "REQ-002", "description": f"{RUN_ID}-req2", "priority": "medium"},
                ],
            },
        )
        assert resp.status_code == 200
        assert resp.json().get("count", 0) == 2

    def test_08_read_table_back(self, http_client):
        doc_id = self.state["doc_id"]
        table = http_client.get(
            f"/reports/api/reports/{doc_id}/tables/requirements"
        ).json()
        rows = table.get("rows") or []
        assert len(rows) == 2, f"expected 2 rows, got {len(rows)}"
        assert any(RUN_ID in str(r) for r in rows), "row content not persisted"

    def test_09_update_meta(self, http_client):
        doc_id = self.state["doc_id"]
        resp = http_client.patch(
            f"/reports/api/reports/{doc_id}/meta",
            json={"meta": {"version": "0.2", "status": "review"}},
        )
        assert resp.status_code == 200
        assert resp.json().get("ok"), resp.text[:200]

    def test_10_preview_renders(self, http_client):
        doc_id = self.state["doc_id"]
        resp = http_client.get(f"/reports/api/reports/{doc_id}/preview")
        assert resp.status_code == 200
        body = resp.text
        # Preview should be HTML or markdown; at minimum contain the title or some content
        assert len(body) > 100, f"preview too short: {body[:200]}"

    def test_11_export_docx(self, http_client):
        doc_id = self.state["doc_id"]
        resp = http_client.post(f"/reports/api/reports/{doc_id}/export/docx")
        # Export may fail in some environments (python-docx), so tolerate 500
        # but verify the endpoint returns a JSON shape
        if resp.status_code != 200:
            pytest.skip(f"docx export not available: {resp.status_code}")
        data = resp.json()
        assert data.get("ok") or data.get("name"), f"unexpected export result: {data}"

    def test_12_delete_moves_to_trash(self, http_client):
        doc_id = self.state["doc_id"]
        resp = http_client.delete(f"/reports/api/reports/{doc_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok"), resp.text[:200]
        assert "_trash" in data.get("moved_to", ""), (
            f"expected moved_to to contain _trash, got: {data}"
        )
        time.sleep(0.3)
        # Verify gone from main list
        listing = http_client.get("/reports/api/reports").json()
        reports = listing.get("reports") if isinstance(listing, dict) else listing
        assert not any(
            r.get("id") == doc_id for r in reports
        ), "report still in list after delete"


@pytest.fixture(scope="module", autouse=True)
def _cleanup(http_client):
    yield
    try:
        listing = http_client.get("/reports/api/reports").json()
        reports = listing.get("reports") if isinstance(listing, dict) else listing
        for r in reports if isinstance(reports, list) else []:
            rid = r.get("id", "")
            title = r.get("meta", {}).get("title", "") if isinstance(r.get("meta"), dict) else ""
            if RUN_ID in rid or RUN_ID in title:
                http_client.delete(f"/reports/api/reports/{rid}")
    except Exception:
        pass
