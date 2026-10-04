"""System app tests: Library — 13 use cases.

Acceptance criteria → tests (derived from the approved plan
we-want-a-zotero-resilient-galaxy.md,
since no formal grill spec note was written for this scaffold):
  AC1  GET /library/api/papers returns a list                    → test_list_structure
  AC2  POST /library/api/papers creates a paper w/ citekey         → test_add_paper_manual
  AC3  GET /library/api/papers/{citekey} returns full detail       → test_detail_roundtrip
  AC4  PUT /library/api/papers/{citekey} updates a field           → test_update_status
  AC5  DELETE /library/api/papers/{citekey} removes it              → test_delete_flow
  AC6  POST .../highlight appends a `## Highlights` bullet          → test_add_highlight
  AC7  GET /library/api/export?format=bibtex is valid BibTeX        → test_export_bibtex
  AC8  GET /library/api/export?format=csl-json is valid JSON        → test_export_csl_json
  AC9  POST .../send-to-kb files a reviewable pending action        → test_send_to_kb
  AC10 GET /library/ renders with no JS console errors              → test_page_loads
  AC11 "+ Add paper" opens the import modal                         → test_import_modal_opens
  AC12 Settings panel opens ([provides.settings] declared)          → test_settings_panel_opens
Edge/regression (no AC): test_search_filters, test_delete_unknown_404
"""

import pytest

from helpers import TEST_PREFIX, assert_dict_response, assert_list_response, assert_ok
from page_helpers import assert_no_js_errors, wait_briefly


@pytest.mark.api
class TestLibraryAPI:
    def _make_paper(self, http_client, suffix="basic"):
        r = http_client.post(
            "/library/api/papers",
            json={
                "title": f"{TEST_PREFIX}Paper {suffix}",
                "authors": ["Ada Lovelace", "Alan Turing"],
                "year": "2024",
                "journal": "Journal of Test Fixtures",
                "abstract": "A test abstract for the library app.",
                "topics": ["testing", "fixtures"],
                "source": "manual",
            },
        )
        data = assert_dict_response(r)
        assert data.get("ok"), f"create failed: {data}"
        assert data.get("citekey"), f"no citekey returned: {data}"
        return data["citekey"]

    def test_list_structure(self, http_client):
        data = assert_list_response(http_client.get("/library/api/papers"))
        if data:
            p = data[0]
            for key in ("title", "citekey"):
                assert key in p, f"Paper missing key {key}: {p}"

    def test_add_paper_manual(self, http_client):
        citekey = self._make_paper(http_client, "manual")
        assert citekey, "expected a generated citekey"

    def test_add_paper_requires_title(self, http_client):
        r = http_client.post("/library/api/papers", json={"authors": ["No One"]})
        data = assert_dict_response(r)
        assert data.get("error"), "expected an error when title is missing"

    def test_detail_roundtrip(self, http_client):
        citekey = self._make_paper(http_client, "detail")
        data = assert_dict_response(http_client.get(f"/library/api/papers/{citekey}"))
        assert data.get("citekey") == citekey
        assert "Lovelace" in " ".join(data.get("authors") or [])
        assert data.get("year") == "2024"

    def test_update_status(self, http_client):
        citekey = self._make_paper(http_client, "update")
        r = http_client.put(f"/library/api/papers/{citekey}", json={"status": "reading"})
        assert_ok(r)
        data = assert_dict_response(http_client.get(f"/library/api/papers/{citekey}"))
        assert data.get("status") == "reading"

    def test_add_highlight(self, http_client):
        citekey = self._make_paper(http_client, "highlight")
        r = http_client.post(
            f"/library/api/papers/{citekey}/highlight",
            json={"page": "12", "quote": "a quoted passage", "note": "worth remembering"},
        )
        data = assert_dict_response(r)
        assert data.get("ok"), f"highlight failed: {data}"
        detail = assert_dict_response(http_client.get(f"/library/api/papers/{citekey}"))
        assert "a quoted passage" in (detail.get("body") or "")

    def test_add_highlight_requires_content(self, http_client):
        citekey = self._make_paper(http_client, "highlight-empty")
        r = http_client.post(f"/library/api/papers/{citekey}/highlight", json={"page": "1"})
        data = assert_dict_response(r)
        assert data.get("error"), "expected an error when quote and note are both empty"

    def test_export_bibtex(self, http_client):
        citekey = self._make_paper(http_client, "bibtex")
        r = http_client.get(f"/library/api/export?format=bibtex&citekey={citekey}")
        assert r.status_code == 200
        body = r.text
        assert citekey in body
        assert body.strip().startswith("@")
        assert "Lovelace" in body

    def test_export_csl_json(self, http_client):
        citekey = self._make_paper(http_client, "csl")
        r = http_client.get(f"/library/api/export?format=csl-json&citekey={citekey}")
        assert r.status_code == 200
        import json
        items = json.loads(r.text)
        assert isinstance(items, list) and len(items) == 1
        assert items[0]["id"] == citekey

    def test_send_to_kb(self, http_client):
        citekey = self._make_paper(http_client, "sendkb")
        r = http_client.post(f"/library/api/papers/{citekey}/send-to-kb")
        data = assert_dict_response(r)
        assert not data.get("error"), f"send-to-kb failed: {data}"

    def test_search_filters(self, http_client):
        citekey = self._make_paper(http_client, "searchable-topic")
        data = assert_list_response(http_client.get("/library/api/papers?q=Lovelace"))
        assert any(p.get("citekey") == citekey for p in data), "search should surface the new paper"

    def test_delete_flow(self, http_client):
        citekey = self._make_paper(http_client, "delete")
        r = http_client.delete(f"/library/api/papers/{citekey}")
        assert_ok(r)
        data = assert_dict_response(http_client.get(f"/library/api/papers/{citekey}"))
        assert data.get("error"), "deleted paper should 404/error on re-fetch"

    def test_delete_unknown_404(self, http_client):
        r = http_client.delete(f"/library/api/papers/{TEST_PREFIX}does-not-exist")
        data = assert_dict_response(r)
        assert data.get("error")


@pytest.mark.interactive
class TestLibraryUI:
    def test_page_loads(self, app_page, page_errors):
        page = app_page("library")
        wait_briefly(page)
        assert_no_js_errors(page_errors)

    def test_import_modal_opens(self, app_page, page_errors):
        page = app_page("library")
        wait_briefly(page)
        page.click("text=+ Add paper")
        page.wait_for_selector("#eos-modal-overlay", timeout=5000)
        assert page.is_visible("#imp-doi")
        assert_no_js_errors(page_errors)

    def test_settings_panel_opens(self, app_page, page_errors):
        page = app_page("library")
        wait_briefly(page)
        page.click(".btn-settings")
        page.wait_for_selector(".eos-settings-panel.open", timeout=5000)
        assert_no_js_errors(page_errors)
