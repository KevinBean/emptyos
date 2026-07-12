"""System tests for the boards app.

Covers: board + column + view + item CRUD, filter/formula evaluation,
link-record inverse maintenance, backlinks, and the table/kanban UI surfaces.
"""

import uuid

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
                         "person", "link-record", "formula", "rollup"):
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

    def test_preset_declares_summary_chart_pivot_views(self, http_client):
        """crm-pipeline preset carries the new summary/chart/pivot view tabs."""
        board_id = f"{TEST_PREFIX.lower().replace('_','-')}crm-views"
        http_client.post("/boards/api/boards",
                         json={"preset": "crm-pipeline", "id": board_id,
                               "name": f"{TEST_PREFIX}CRM views"})
        detail = http_client.get(f"/boards/api/boards/{board_id}").json()
        types = {v.get("type") for v in detail.get("views", [])}
        assert {"summary", "chart", "pivot"} <= types, f"got {types}"
        pivot = next(v for v in detail["views"] if v.get("type") == "pivot")
        assert pivot.get("rows") and pivot.get("cols")

    def test_places_preset_declares_map_view(self, http_client):
        """places preset carries a map view wired to lat/lng."""
        board_id = f"{TEST_PREFIX.lower().replace('_','-')}places-map"
        http_client.post("/boards/api/boards",
                         json={"preset": "places", "id": board_id,
                               "name": f"{TEST_PREFIX}Places map"})
        detail = http_client.get(f"/boards/api/boards/{board_id}").json()
        mapv = [v for v in detail.get("views", []) if v.get("type") == "map"]
        assert mapv, "places preset should declare a map view"
        assert mapv[0].get("lat_field") == "lat"
        assert mapv[0].get("lng_field") == "lng"

    def test_smart_add_validation(self, http_client):
        """Smart-add rejects empty text + unknown board without hitting the LLM."""
        board_id = f"{TEST_PREFIX.lower().replace('_','-')}sa-val"
        http_client.post("/boards/api/boards",
                         json={"preset": "bug-tracker", "id": board_id,
                               "name": f"{TEST_PREFIX}SA val"})
        r1 = http_client.post(f"/boards/api/boards/{board_id}/items/smart-add", json={"text": ""})
        assert r1.status_code == 200 and r1.json().get("ok") is False
        r2 = http_client.post("/boards/api/boards/does-not-exist-xyz/items/smart-add",
                              json={"text": "hi"})
        assert r2.json().get("ok") is False

    def test_smart_add_parses(self, http_client, require_llm):
        """Smart-add turns an NL line into validated fields (does not create)."""
        board_id = f"{TEST_PREFIX.lower().replace('_','-')}sa-parse"
        http_client.post("/boards/api/boards",
                         json={"preset": "bug-tracker", "id": board_id,
                               "name": f"{TEST_PREFIX}SA parse"})
        before = http_client.get(f"/boards/api/boards/{board_id}/items").json()
        r = http_client.post(f"/boards/api/boards/{board_id}/items/smart-add",
                             json={"text": "login page crashes on submit, critical severity"})
        data = r.json()
        assert data.get("ok") is True
        fields = data.get("fields") or {}
        assert fields.get("title"), "title should be extracted"
        # severity, if extracted, must be a valid option (validator constrains it)
        sev_opts = ["Low", "Medium", "High", "Critical"]
        if "severity" in fields:
            assert fields["severity"] in sev_opts
        # smart-add must NOT create the item
        after = http_client.get(f"/boards/api/boards/{board_id}/items").json()
        assert len(after) == len(before)

    def test_group_change_patch_ok(self, http_client):
        """PATCHing the kanban group-by field (the board:item_moved path) succeeds."""
        board_id = f"{TEST_PREFIX.lower().replace('_','-')}move"
        http_client.post("/boards/api/boards",
                         json={"id": board_id, "name": f"{TEST_PREFIX}Move",
                               "source_tag": board_id,
                               "columns": [{"id": "title", "type": "text", "label": "T"},
                                           {"id": "status", "type": "select", "label": "S",
                                            "options": ["Open", "Done"]}],
                               "views": [{"type": "kanban", "group_by": "status"}],
                               "kanban_group_by": "status"})
        f = http_client.post(f"/boards/api/boards/{board_id}/items",
                             json={"title": f"{TEST_PREFIX}m1", "status": "Open"}).json().get("file")
        r = http_client.patch(f"/boards/api/boards/{board_id}/items/{f}",
                              json={"updates": {"status": "Done"}})
        assert r.json().get("ok") is True

    def test_rollup_aggregates_over_linked_records(self, http_client):
        """A rollup column sums a field across the records a link-record points to."""
        nonce = uuid.uuid4().hex[:6]
        tgt = f"{TEST_PREFIX.lower().replace('_','-')}rollup-tgt-{nonce}"
        src = f"{TEST_PREFIX.lower().replace('_','-')}rollup-src-{nonce}"
        # Target board: items carry an `hours` number + a `due` date.
        http_client.post("/boards/api/boards", json={
            "id": tgt, "name": f"{TEST_PREFIX}RTgt", "source_tag": tgt,
            "columns": [{"id": "title", "type": "text", "label": "T"},
                        {"id": "hours", "type": "number", "label": "H"},
                        {"id": "due", "type": "date", "label": "D"}],
        })
        f1 = http_client.post(f"/boards/api/boards/{tgt}/items",
                              json={"title": f"{TEST_PREFIX}a", "hours": 3, "due": "2026-07-10"}).json().get("file")
        f2 = http_client.post(f"/boards/api/boards/{tgt}/items",
                              json={"title": f"{TEST_PREFIX}b", "hours": 5, "due": "2026-03-02"}).json().get("file")
        assert f1 and f2

        # Source board: a link-record column → target, plus SUM and LATEST rollups.
        http_client.post("/boards/api/boards", json={
            "id": src, "name": f"{TEST_PREFIX}RSrc", "source_tag": src,
            "columns": [
                {"id": "title", "type": "text", "label": "T"},
                {"id": "parts", "type": "link-record", "label": "Parts", "target_board": tgt, "multi": True},
                {"id": "total_hours", "type": "rollup", "label": "Hrs",
                 "source_link": "parts", "target_field": "hours", "agg": "sum"},
                {"id": "n_parts", "type": "rollup", "label": "#", "source_link": "parts", "agg": "count"},
                {"id": "last_due", "type": "rollup", "label": "Latest",
                 "source_link": "parts", "target_field": "due", "agg": "latest"},
            ],
        })
        http_client.post(f"/boards/api/boards/{src}/items",
                         json={"title": f"{TEST_PREFIX}parent", "parts": [f1, f2]})

        items = http_client.get(f"/boards/api/boards/{src}/items").json()
        rows = items if isinstance(items, list) else items.get("items", [])
        parent = next((r for r in rows if f"{TEST_PREFIX}parent" in str(r.get("title", ""))), None)
        assert parent is not None, "parent item missing"
        assert str(parent.get("total_hours")) == "8", parent
        assert str(parent.get("n_parts")) == "2", parent
        assert parent.get("last_due") == "2026-07-10", parent  # LATEST keeps the date

    def test_bulk_archive_via_delete(self, http_client):
        """Two items DELETE-archived (the bulk-archive path) land as Archived."""
        board_id = f"{TEST_PREFIX.lower().replace('_','-')}barch"
        http_client.post("/boards/api/boards",
                         json={"id": board_id, "name": f"{TEST_PREFIX}BArch",
                               "source_tag": board_id,
                               "columns": [{"id": "title", "type": "text", "label": "T"},
                                           {"id": "status", "type": "select", "label": "S",
                                            "options": ["Open", "Archived"]}]})
        files = [http_client.post(f"/boards/api/boards/{board_id}/items",
                                  json={"title": f"{TEST_PREFIX}b{i}"}).json().get("file")
                 for i in range(2)]
        for f in files:
            assert http_client.delete(f"/boards/api/boards/{board_id}/items/{f}").status_code == 200
        items = {i.get("file"): i for i in http_client.get(f"/boards/api/boards/{board_id}/items").json()}
        for f in files:
            assert items.get(f, {}).get("status") == "Archived"


# ── API: PM-features round — comments, activity, attachments, checklist ──

def _mk_pm_board(http_client, suffix: str) -> tuple[str, str]:
    """Create a writable vault_tag board + one item; return (board_id, file).

    Board ids carry a per-run nonce — the daemon under test may keep state
    across runs (sandbox pool members persist their vault)."""
    import uuid
    suffix = f"{suffix}-{uuid.uuid4().hex[:6]}"
    board_id = f"{TEST_PREFIX.lower().replace('_','-')}pm-{suffix}"
    http_client.post("/boards/api/boards",
                     json={"id": board_id, "name": f"{TEST_PREFIX}PM {suffix}",
                           "source_tag": board_id})
    r = http_client.post(f"/boards/api/boards/{board_id}/items",
                         json={"name": f"{TEST_PREFIX}item-{suffix}"})
    return board_id, r.json().get("file")


@pytest.mark.api
class TestBoardsCollabAPI:
    """Comments, durable activity, attachments, checklist (PM-features round)."""

    def test_comments_crud(self, http_client):
        bid, file = _mk_pm_board(http_client, "comments")
        base = f"/boards/api/boards/{bid}/items/{file}/comments"
        # Empty thread
        assert http_client.get(base).json() == {"comments": []}
        # Add
        r = http_client.post(base, json={"text": "First comment", "author": "Tester"})
        c = r.json()["comment"]
        assert r.json().get("ok") and c["author"] == "Tester" and c["text"] == "First comment"
        # Edit
        r_ed = http_client.patch(f"{base}/{c['id']}", json={"text": "Edited"})
        assert r_ed.json()["comment"]["text"] == "Edited"
        assert r_ed.json()["comment"]["edited"]
        # Listed
        assert len(http_client.get(base).json()["comments"]) == 1
        # Delete
        assert http_client.delete(f"{base}/{c['id']}").json().get("ok") is True
        assert http_client.get(base).json() == {"comments": []}
        # Guards
        assert http_client.post(base, json={"text": "  "}).json().get("error")
        assert http_client.delete(f"{base}/c-nope").json().get("error")

    def test_activity_durable_old_new(self, http_client):
        bid, file = _mk_pm_board(http_client, "activity")
        r = http_client.patch(f"/boards/api/boards/{bid}/items/{file}",
                              json={"updates": {"status": "Done"}})
        assert r.json().get("ok")
        events = http_client.get(
            f"/boards/api/boards/{bid}/items/{file}/activity").json()["events"]
        durable = [e for e in events
                   if e.get("durable") and e["type"] == "board:item_updated"]
        assert durable, f"no durable update entry in {events[:3]}"
        upd = durable[0]["updates"].get("status") or {}
        assert upd.get("new") == "Done"

    def test_checklist_roundtrip(self, http_client):
        bid, file = _mk_pm_board(http_client, "checklist")
        http_client.post(f"/boards/api/boards/{bid}/columns",
                         json={"id": "checklist", "label": "Checklist", "type": "checklist"})
        items = [{"text": 'Step "one": draft', "done": False},
                 {"text": "Step two", "done": True}]
        r = http_client.patch(f"/boards/api/boards/{bid}/items/{file}",
                              json={"updates": {"checklist": items}})
        assert r.json().get("ok"), r.json()
        got = http_client.get(f"/boards/api/boards/{bid}/items/{file}").json()
        import json as _json
        val = got.get("checklist")
        parsed = _json.loads(val) if isinstance(val, str) else val
        assert parsed == items  # embedded quotes must survive (VaultLibrary fix)

    def test_attachments_lifecycle(self, http_client):
        bid, file = _mk_pm_board(http_client, "attach")
        base = f"/boards/api/boards/{bid}/items/{file}/attachments"
        payload = b"hello attachment bytes \x00\x01"
        r = http_client.post(base, files={"file": ("report v1.txt", payload)})
        body = r.json()
        assert body.get("ok"), body
        name = body["name"]
        assert "/" not in name and "\\" not in name
        # List
        listed = http_client.get(base).json()["attachments"]
        assert any(a["name"] == name for a in listed)
        # Download byte-equality
        dl = http_client.get(f"{base}/{name}")
        assert dl.status_code == 200 and dl.content == payload
        # Traversal-shaped filename is rejected outright (dot-leading after
        # sanitization), never written anywhere
        r_tr = http_client.post(base, files={"file": ("../../evil.txt", b"x")})
        assert r_tr.json().get("error")
        # Count index
        counts = http_client.get(
            f"/boards/api/boards/{bid}/attachments-index").json()["counts"]
        assert counts.get(file, 0) >= 1
        # Delete
        assert http_client.delete(f"{base}/{name}").json().get("ok") is True
        assert http_client.get(base).json()["attachments"] == []

    def test_saved_view_filter_conjunction(self, http_client):
        bid, _file = _mk_pm_board(http_client, "conj")
        view = {"name": f"{TEST_PREFIX}Conj view", "view_type": "table",
                "filters": [{"col_id": "status", "op": "is", "value": "Done"}],
                "filter_conjunction": "or"}
        r = http_client.post(f"/boards/api/boards/{bid}/views", json=view)
        vid = r.json()["view"]["id"]
        got = http_client.get(f"/boards/api/boards/{bid}/views/{vid}").json()
        assert got.get("filter_conjunction") == "or"

    def test_me_endpoint(self, http_client):
        d = http_client.get("/boards/api/me").json()
        assert "me" in d and "me_person" in d


# ── API: Planner .xlsx import (canonical apply route) ─────────────────

@pytest.mark.api
class TestBoardsPlannerApply:
    RECORDS = [
        {"planner_id": "t-001", "name": f"{TEST_PREFIX}Planner task A",
         "bucket": "Content", "status": "In progress", "priority": "Important",
         "assigned": [], "due_date": "2026-08-01",
         "checklist": [{"text": "outline", "done": True}, {"text": "draft", "done": False}],
         "labels": ["Copy"], "body": "imported description"},
        {"planner_id": "t-002", "name": f"{TEST_PREFIX}Planner task B",
         "bucket": "Infra", "status": "Completed", "priority": "Low",
         "assigned": [], "due_date": "", "checklist": [], "labels": []},
    ]
    MAPPING = {"planner_id": 0, "task_name": 1, "bucket": 2, "progress": 3,
               "priority": 4, "assigned": 5, "due_date": 9,
               "checklist": 14, "labels": 15, "description": 16}

    def _apply(self, http_client, bid, records):
        return http_client.post(
            f"/boards/api/boards/{bid}/planner/apply",
            json={"records": records, "mapping": self.MAPPING,
                  "options": {"create_board": True,
                              "board_name": f"{TEST_PREFIX}Planner board",
                              "plan_name": f"{TEST_PREFIX}Plan",
                              "create_people": False}},
        ).json()

    def test_apply_creates_then_upserts(self, http_client):
        import uuid
        bid = f"{TEST_PREFIX.lower().replace('_','-')}planner-{uuid.uuid4().hex[:6]}"
        r1 = self._apply(http_client, bid, self.RECORDS)
        assert r1.get("ok"), r1
        assert r1["created"] == 2 and r1["updated"] == 0

        # Board exists with the mapped columns + kanban by bucket
        cfg = http_client.get(f"/boards/api/boards/{bid}").json()
        col_ids = {c["id"] for c in cfg.get("columns", [])}
        for expected in ("planner_id", "name", "bucket", "status", "checklist", "labels"):
            assert expected in col_ids, f"missing column {expected}"
        assert cfg.get("kanban_group_by") == "bucket"
        assert cfg.get("planner", {}).get("mapping") == self.MAPPING
        # Bucket options merged from the records
        bucket_col = next(c for c in cfg["columns"] if c["id"] == "bucket")
        assert {"Content", "Infra"} <= set(bucket_col.get("options", []))

        # Idempotence: same records → nothing new, nothing updated
        r2 = self._apply(http_client, bid, self.RECORDS)
        assert r2["created"] == 0 and r2["updated"] == 0 and r2["unchanged"] == 2, r2

        # Upsert by planner_id: changed status updates in place. The read-back
        # goes through VaultIndex, which refreshes asynchronously — retry
        # briefly before asserting.
        import time
        changed = [dict(self.RECORDS[0], status="Completed")]
        r3 = self._apply(http_client, bid, changed)
        assert r3["created"] == 0 and r3["updated"] == 1, r3
        status = None
        for _ in range(30):   # vault-watcher debounce can exceed 5s
            items = http_client.get(f"/boards/api/boards/{bid}/items").json()
            t1 = next((i for i in items if i.get("planner_id") == "t-001"), {})
            status = t1.get("status")
            if status == "Completed":
                break
            time.sleep(0.5)
        assert status == "Completed"

    def test_apply_rejects_empty(self, http_client):
        bid = f"{TEST_PREFIX.lower().replace('_','-')}planner-empty"
        r = http_client.post(f"/boards/api/boards/{bid}/planner/apply",
                             json={"records": [], "mapping": {}, "options": {}})
        assert r.json().get("error")


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

    def test_ui_column_modal_opens(self, app_page, page_errors, http_client, base_url):
        """Clicking + Column opens the column editor modal.

        Uses a freshly-created editable board and deep-links to it via ?id=
        rather than clicking the first .board-card — the first card is now
        often a read-only system view (app-sourced board), which hides
        .board-edit-only buttons (`body.board-readonly` CSS), so "+ Column"
        would exist-but-be-invisible and the click would time out.
        """
        board_id = f"{TEST_PREFIX.lower().replace('_', '-')}colmodal"
        http_client.post("/boards/api/boards",
                         json={"preset": "bug-tracker", "id": board_id,
                               "name": f"{TEST_PREFIX}Col modal"})
        page = app_page("boards")
        page.goto(f"{base_url}/boards/?id={board_id}", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 1500)
        # "+ Column" button opens the column modal
        btn = page.locator('button', has_text="+ Column").first
        if btn.count() == 0:
            pytest.skip("+ Column button not rendered")
        btn.click()
        wait_briefly(page, 400)
        assert page.locator("#column-modal.open").count() == 1

    def test_ui_board_agg_helper(self, app_page, page_errors):
        """The shared boardAgg() powering chart/summary/pivot aggregates correctly."""
        page = app_page("boards")
        wait_briefly(page, 800)
        summed = page.evaluate(
            "boardAgg([{s:'a',v:2},{s:'a',v:3},{s:'b',v:4}],'s','v','sum')"
        )
        assert summed == {"a": 5, "b": 4}
        counted = page.evaluate("boardAgg([{s:'a'},{s:'a'},{s:'b'}],'s','','count')")
        assert counted == {"a": 2, "b": 1}
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_summary_chart_pivot_tabs(self, app_page, page_errors, http_client, base_url):
        """Open a crm board and switch through the summary/chart/pivot tabs."""
        board_id = f"{TEST_PREFIX.lower().replace('_','-')}crm-ui"
        http_client.post("/boards/api/boards",
                         json={"preset": "crm-pipeline", "id": board_id,
                               "name": f"{TEST_PREFIX}CRM UI"})
        page = app_page("boards")
        page.goto(f"{base_url}/boards/?id={board_id}", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 1500)
        for view in ("summary", "chart", "pivot"):
            tab = page.locator(f'.eos-view-tab[data-view="{view}"]').first
            if tab.count() == 0:
                pytest.skip(f"{view} tab not rendered")
            tab.click()
            wait_briefly(page, 500)
            assert page.locator(f"#view-{view}.active").count() == 1, f"{view} panel not active"
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_map_tab_renders(self, app_page, page_errors, http_client, base_url):
        """places board exposes a Map tab that renders without a fatal JS error."""
        board_id = f"{TEST_PREFIX.lower().replace('_','-')}places-ui"
        http_client.post("/boards/api/boards",
                         json={"preset": "places", "id": board_id,
                               "name": f"{TEST_PREFIX}Places UI"})
        page = app_page("boards")
        page.goto(f"{base_url}/boards/?id={board_id}", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 1500)
        tab = page.locator('.eos-view-tab[data-view="map"]').first
        if tab.count() == 0:
            pytest.skip("map tab not rendered")
        tab.click()
        wait_briefly(page, 1500)
        assert page.locator("#view-map.active").count() == 1
        # Markers or an empty-state note are both fine; Leaflet CDN may be offline in CI.
        assert_no_js_errors(page_errors, allow_patterns=["fetch", "leaflet", "unpkg", "tile"])

    def test_ui_calendar_month_nav(self, app_page, page_errors, http_client, base_url):
        """Calendar view has working ‹ / › month navigation."""
        board_id = f"{TEST_PREFIX.lower().replace('_','-')}cal-ui"
        http_client.post("/boards/api/boards",
                         json={"preset": "content-calendar", "id": board_id,
                               "name": f"{TEST_PREFIX}Cal UI"})
        page = app_page("boards")
        page.goto(f"{base_url}/boards/?id={board_id}", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 1500)
        tab = page.locator('.eos-view-tab[data-view="calendar"]').first
        if tab.count() == 0:
            pytest.skip("calendar tab not rendered")
        tab.click()
        wait_briefly(page, 500)
        label = page.locator(".calendar-month-label")
        assert label.count() == 1
        before = label.inner_text()
        page.locator(".calendar-nav button[title='Previous month']").first.click()
        wait_briefly(page, 400)
        assert label.inner_text() != before, "month label should change on ‹"
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_kanban_quickadd_and_smart_add(self, app_page, page_errors, http_client, base_url):
        """Vault-tag board shows the kanban quick-add footer + the ✨ smart-add row."""
        board_id = f"{TEST_PREFIX.lower().replace('_','-')}qa-ui"
        http_client.post("/boards/api/boards",
                         json={"preset": "bug-tracker", "id": board_id,
                               "name": f"{TEST_PREFIX}QA UI"})
        page = app_page("boards")
        page.goto(f"{base_url}/boards/?id={board_id}", wait_until="domcontentloaded", timeout=15000)
        try:
            page.wait_for_selector('.eos-view-tab[data-view="kanban"]', timeout=8000)
        except Exception:
            pytest.skip("kanban tab not rendered")
        ktab = page.locator('.eos-view-tab[data-view="kanban"]').first
        ktab.click()
        wait_briefly(page, 600)
        qa = page.locator(".eos-kanban-quickadd-btn").first
        assert qa.count() >= 1, "quick-add footer should render on a vault-tag board"
        qa.click()
        wait_briefly(page, 200)
        assert page.locator(".eos-kanban-quickadd-input:visible").count() >= 1
        # Smart-add row appears on the add modal.
        page.locator("button", has_text="+ Add Item").first.click()
        wait_briefly(page, 300)
        assert page.locator("#smart-add-row:visible").count() == 1
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])
