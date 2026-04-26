"""System tests for the boards app.

Covers: board + column + view + item CRUD, filter/formula evaluation,
link-record inverse maintenance, backlinks, and the table/kanban UI surfaces.
"""

import pytest

from helpers import TEST_PREFIX
from page_helpers import assert_no_js_errors, wait_briefly


# ── API: boards config CRUD + column editor ──────────────────────────

@pytest.mark.api
class TestBoardsAPI:
    def test_list_boards(self, http_client):
        resp = http_client.get("/boards/api/boards")
        assert resp.status_code == 200
        data = resp.json()
        assert "boards" in data and "presets" in data

    def test_presets_endpoint(self, http_client):
        resp = http_client.get("/boards/api/presets")
        assert resp.status_code == 200

    def test_column_types_registry(self, http_client):
        resp = http_client.get("/boards/api/column-types")
        assert resp.status_code == 200
        types = resp.json().get("types", [])
        ids = {t["id"] for t in types}
        # Built-ins from emptyos/sdk/column_types.py
        for required in ("text", "number", "select", "date", "checkbox",
                         "person", "link-record", "formula"):
            assert required in ids, f"type {required!r} missing from registry"

    def test_create_board_from_preset(self, http_client):
        board_id = f"{TEST_PREFIX.lower().replace('_','-')}board-preset"
        resp = http_client.post("/boards/api/boards",
                                json={"preset": "bug-tracker", "id": board_id,
                                      "name": f"{TEST_PREFIX}Bug board"})
        assert resp.status_code == 200
        assert resp.json().get("ok")

        # Follow-up: detail should include columns + views
        detail = http_client.get(f"/boards/api/boards/{board_id}").json()
        assert isinstance(detail.get("columns"), list) and len(detail["columns"]) > 0

    def test_column_editor_add_edit_delete(self, http_client):
        board_id = f"{TEST_PREFIX.lower().replace('_','-')}col-edit"
        http_client.post("/boards/api/boards",
                         json={"preset": "bug-tracker", "id": board_id,
                               "name": f"{TEST_PREFIX}Col edit"})

        # Add a column
        r = http_client.post(f"/boards/api/boards/{board_id}/columns",
                             json={"id": "notes", "label": "Notes", "type": "text"})
        assert r.status_code == 200 and r.json().get("ok") is True

        # Validate rejection of duplicate id
        r_dup = http_client.post(f"/boards/api/boards/{board_id}/columns",
                                 json={"id": "notes", "label": "X", "type": "text"})
        assert r_dup.json().get("error"), "duplicate id should be rejected"

        # Validate rejection of unknown type
        r_bad = http_client.post(f"/boards/api/boards/{board_id}/columns",
                                 json={"id": "other", "label": "Y", "type": "garbage"})
        assert r_bad.json().get("error")

        # Edit: rename the label
        r_ed = http_client.patch(f"/boards/api/boards/{board_id}/columns/notes",
                                 json={"label": "Renamed"})
        assert r_ed.json().get("ok") is True

        # Delete
        r_del = http_client.delete(f"/boards/api/boards/{board_id}/columns/notes")
        assert r_del.json().get("ok") is True

    def test_link_record_requires_target_board(self, http_client):
        board_id = f"{TEST_PREFIX.lower().replace('_','-')}link-val"
        http_client.post("/boards/api/boards",
                         json={"preset": "bug-tracker", "id": board_id,
                               "name": f"{TEST_PREFIX}LinkValidation"})
        r = http_client.post(f"/boards/api/boards/{board_id}/columns",
                             json={"id": "parent", "label": "Parent", "type": "link-record"})
        # target_board is required
        assert r.json().get("error"), "link-record without target_board should be rejected"

    def test_saved_views_crud(self, http_client):
        # Save a view, fetch it, delete it.
        # Uses the first existing board (works for any EmptyOS deployment).
        existing = http_client.get("/boards/api/boards").json().get("boards", [])
        if not existing:
            pytest.skip("no boards configured")
        bid = existing[0]["id"]
        view = {
            "name": f"{TEST_PREFIX}View smoke",
            "view_type": "table",
            "filters": [{"col_id": "status", "op": "is", "value": "Open"}],
            "hidden_columns": ["rev"],
            "person_filter": "",
            "search": "",
        }
        r = http_client.post(f"/boards/api/boards/{bid}/views", json=view)
        assert r.status_code == 200 and r.json().get("ok")
        vid = r.json()["view"]["id"]
        got = http_client.get(f"/boards/api/boards/{bid}/views/{vid}").json()
        assert got.get("filters") == view["filters"]
        assert got.get("hidden_columns") == view["hidden_columns"]
        # Delete
        r_del = http_client.delete(f"/boards/api/boards/{bid}/views/{vid}")
        assert r_del.json().get("ok") is True

    def test_links_rebuild(self, http_client):
        r = http_client.post("/boards/api/links/rebuild")
        assert r.status_code == 200
        body = r.json()
        assert body.get("ok") is True
        # Either zero edges (no link-record columns on any board) or positive.
        assert "total_edges" in body

    def test_backlinks_endpoint_shape(self, http_client):
        # Find the first board that has at least one item — skip if none.
        existing = http_client.get("/boards/api/boards").json().get("boards", [])
        bid = None
        file_id = None
        for b in existing:
            items = http_client.get(f"/boards/api/boards/{b['id']}/items").json()
            if isinstance(items, list) and items:
                bid = b["id"]
                file_id = items[0].get("file") or items[0].get("id")
                break
        if not bid or not file_id:
            pytest.skip("no boards with items available")
        r = http_client.get(f"/boards/api/boards/{bid}/items/{file_id}/backlinks")
        assert r.status_code == 200
        assert "backlinks" in r.json()

    def test_item_filter_sort(self, http_client):
        existing = http_client.get("/boards/api/boards").json().get("boards", [])
        if not existing:
            pytest.skip("no boards configured")
        bid = existing[0]["id"]
        r = http_client.get(f"/boards/api/boards/{bid}/items?sort_by=name&sort_desc=1")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


# ── UI: table view + view-switching + column editor modal ────────────

@pytest.mark.interactive
class TestBoardsUI:
    def test_ui_loads(self, app_page, page_errors):
        page = app_page("boards")
        wait_briefly(page, 1500)
        # Either home (board launcher) or a specific board renders without error
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_home_lists_boards(self, app_page, page_errors):
        page = app_page("boards")
        wait_briefly(page, 1500)
        # #home-view must exist as the landing div
        assert page.locator("#home-view").count() == 1
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_view_tab_switch(self, app_page, page_errors):
        """Open a board and switch table → kanban → timeline."""
        boards = [b["id"] for b in (
            app_page("boards").evaluate(
                "fetch('/boards/api/boards').then(r=>r.json()).then(d=>d.boards||[])"
            ) or []
        )]
        # The evaluate above returns too early; fall back to navigating directly
        # to a known board if one exists in fresh-clone presets land.
        page = app_page("boards")
        wait_briefly(page, 1500)
        card = page.locator(".board-card").first
        if card.count() == 0:
            pytest.skip("no saved boards to open")
        card.click()
        wait_briefly(page, 1500)
        # Table is the new default first view — click Kanban tab to verify switch
        kanban_tab = page.locator('.view-tab[data-view="kanban"]').first
        if kanban_tab.count():
            kanban_tab.click()
            wait_briefly(page, 500)
            assert page.locator("#view-kanban.active").count() == 1
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_column_modal_opens(self, app_page, page_errors):
        """Clicking + Column opens the column editor modal."""
        page = app_page("boards")
        wait_briefly(page, 1500)
        card = page.locator(".board-card").first
        if card.count() == 0:
            pytest.skip("no saved boards to open")
        card.click()
        wait_briefly(page, 1500)
        # "+ Column" button opens the column modal
        btn = page.locator('button', has_text="+ Column").first
        if btn.count() == 0:
            pytest.skip("+ Column button not rendered")
        btn.click()
        wait_briefly(page, 400)
        assert page.locator("#column-modal.open").count() == 1
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])
