"""System app tests: Canvas — board CRUD, node/edge persistence, UI smoke."""

import pytest

from helpers import TEST_PREFIX
from page_helpers import assert_no_js_errors, wait_briefly


def _board_id(suffix: str) -> str:
    # Canvas sanitises to alnum + '-' + '_'; TEST_PREFIX already matches that.
    return f"{TEST_PREFIX}canvas-{suffix}"


@pytest.mark.api
class TestCanvasStorageShape:
    """The on-disk shape: scalar frontmatter + `## _meta` fenced JSON + `## n<id>` sections."""

    def _board_file(self, http_client, bid: str) -> str:
        """Save a board and read the raw .md back through the filesystem."""
        nodes = [
            {"id": "n1", "x": 0, "y": 0, "width": 250, "height": 200, "text": "alpha", "color": "default"},
            {"id": "n2", "x": 400, "y": 0, "width": 250, "height": 200, "text": "beta", "color": "blue"},
        ]
        edges = [{"id": "e1", "sourceId": "n1", "sourceSide": "right", "targetId": "n2", "targetSide": "left"}]
        r = http_client.post("/canvas/api/board", json={"board_id": bid, "nodes": nodes, "edges": edges})
        assert r.status_code == 200
        path = r.json().get("path")
        assert path, "save_board must return the written path"
        from pathlib import Path
        return Path(path).read_text(encoding="utf-8")

    def test_uses_section_based_body(self, http_client):
        raw = self._board_file(http_client, _board_id("shape"))
        # Scalar frontmatter fields still indexed
        assert "type: canvas" in raw
        assert "- canvas" in raw  # block-style tag
        # Structured data in a single `## _meta` fenced JSON block
        assert "## _meta" in raw
        assert "```json" in raw
        # Per-node markdown sections with human-readable text
        assert "## nn1" in raw and "alpha" in raw
        assert "## nn2" in raw and "beta" in raw

    def test_load_legacy_json_body(self, http_client, tmp_path):
        """Pre-migration boards (frontmatter + JSON body) must still round-trip."""
        # Discover the boards dir by saving a sentinel board and reading its parent
        bid_sentinel = _board_id("sentinel")
        r = http_client.post("/canvas/api/board", json={"board_id": bid_sentinel, "nodes": [], "edges": []})
        from pathlib import Path
        boards_dir = Path(r.json()["path"]).parent

        legacy_id = _board_id("legacy")
        legacy_path = boards_dir / f"{legacy_id}.md"
        legacy_path.write_text(
            "---\n"
            "type: canvas\n"
            "tags:\n  - canvas\n"
            f"board_id: {legacy_id}\n"
            "updated: 2024-01-01T00:00:00Z\n"
            "---\n\n"
            "{\n"
            f'  "board_id": "{legacy_id}",\n'
            '  "nodes": [{"id": "old1", "x": 10, "y": 20, "width": 250, "height": 200, "text": "legacy text", "color": "default"}],\n'
            '  "edges": []\n'
            "}\n",
            encoding="utf-8",
        )
        back = http_client.get(f"/canvas/api/board?board={legacy_id}").json()
        assert len(back["nodes"]) == 1
        assert back["nodes"][0]["text"] == "legacy text"
        assert back["nodes"][0]["id"] == "old1"

    def test_provenance_persists_in_layout(self, http_client):
        bid = _board_id("prov")
        nodes = [{
            "id": "p1", "x": 0, "y": 0, "width": 250, "height": 200,
            "text": "generated idea", "color": "purple",
            "provenance": {"mode": "local", "provider": "ollama", "model": "qwen3"},
        }]
        http_client.post("/canvas/api/board", json={"board_id": bid, "nodes": nodes, "edges": []})
        back = http_client.get(f"/canvas/api/board?board={bid}").json()
        prov = (back["nodes"][0] or {}).get("provenance") or {}
        assert prov.get("provider") == "ollama"
        assert prov.get("model") == "qwen3"


@pytest.mark.api
class TestCanvasAPI:
    def test_list_boards(self, http_client):
        resp = http_client.get("/canvas/api/boards")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data.get("boards"), list)

    def test_load_missing_board_returns_empty(self, http_client):
        resp = http_client.get(f"/canvas/api/board?board={_board_id('nope')}")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("nodes") == []
        assert data.get("edges") == []

    def test_save_and_load_roundtrip(self, http_client):
        bid = _board_id("rt")
        nodes = [
            {"id": "n1", "x": 0, "y": 0, "width": 250, "height": 200, "text": "alpha", "color": "default"},
            {"id": "n2", "x": 400, "y": 0, "width": 250, "height": 200, "text": "beta", "color": "blue"},
        ]
        edges = [{"id": "e1", "sourceId": "n1", "sourceSide": "right", "targetId": "n2", "targetSide": "left"}]
        r = http_client.post("/canvas/api/board", json={"board_id": bid, "nodes": nodes, "edges": edges})
        assert r.status_code == 200
        assert r.json().get("ok") is True

        back = http_client.get(f"/canvas/api/board?board={bid}").json()
        assert len(back.get("nodes", [])) == 2
        assert len(back.get("edges", [])) == 1
        assert back["nodes"][0]["text"] == "alpha"

    def test_save_shows_up_in_list(self, http_client):
        bid = _board_id("listed")
        http_client.post("/canvas/api/board", json={"board_id": bid, "nodes": [], "edges": []})
        resp = http_client.get("/canvas/api/boards")
        ids = [b["board_id"] for b in resp.json().get("boards", [])]
        assert bid in ids

    def test_board_list_has_counts(self, http_client):
        bid = _board_id("counts")
        nodes = [{"id": f"n{i}", "x": i * 50, "y": 0, "width": 200, "height": 150, "text": f"#{i}"} for i in range(3)]
        edges = [{"id": "e1", "sourceId": "n0", "sourceSide": "right", "targetId": "n1", "targetSide": "left"}]
        http_client.post("/canvas/api/board", json={"board_id": bid, "nodes": nodes, "edges": edges})
        listed = [b for b in http_client.get("/canvas/api/boards").json()["boards"] if b["board_id"] == bid]
        assert listed and listed[0]["node_count"] == 3 and listed[0]["edge_count"] == 1

    def test_delete_board(self, http_client):
        bid = _board_id("del")
        http_client.post("/canvas/api/board", json={"board_id": bid, "nodes": [], "edges": []})
        r = http_client.post("/canvas/api/board/delete", json={"board_id": bid})
        assert r.status_code == 200 and r.json().get("ok") is True
        ids = [b["board_id"] for b in http_client.get("/canvas/api/boards").json().get("boards", [])]
        assert bid not in ids

    def test_delete_inbox_refused(self, http_client):
        r = http_client.post("/canvas/api/board/delete", json={"board_id": "inbox"})
        # Endpoint returns 200 with ok=False per our contract
        assert r.status_code == 200
        assert r.json().get("ok") is False

    def test_board_id_sanitised_on_save(self, http_client):
        # Path-style input should be collapsed to a safe id; API accepts but stores
        # under the sanitised stem. Verify it doesn't blow up and round-trips.
        bid = _board_id("safe_name-1")
        r = http_client.post("/canvas/api/board", json={"board_id": bid, "nodes": [], "edges": []})
        assert r.status_code == 200

    def test_add_node_appends(self, http_client):
        bid = _board_id("addnode")
        # Start with a known state so y-placement is deterministic
        http_client.post("/canvas/api/board", json={"board_id": bid, "nodes": [], "edges": []})
        r = http_client.post("/canvas/api/node",
                             json={"board_id": bid, "text": "routed idea", "color": "blue"})
        assert r.status_code == 200
        data = r.json()
        assert data.get("ok") is True and data.get("node_id")
        back = http_client.get(f"/canvas/api/board?board={bid}").json()
        texts = [n.get("text") for n in back.get("nodes", [])]
        assert "routed idea" in texts

    def test_add_node_empty_text_rejected(self, http_client):
        r = http_client.post("/canvas/api/node",
                             json={"board_id": _board_id("addnode2"), "text": ""})
        assert r.status_code == 200
        assert r.json().get("ok") is False

    def test_add_node_places_below_existing_cluster(self, http_client):
        bid = _board_id("addnode-below")
        seed = [{"id": "s1", "x": 100, "y": 50, "width": 250, "height": 200, "text": "seed"}]
        http_client.post("/canvas/api/board", json={"board_id": bid, "nodes": seed, "edges": []})
        http_client.post("/canvas/api/node", json={"board_id": bid, "text": "below"})
        back = http_client.get(f"/canvas/api/board?board={bid}").json()
        added = next(n for n in back["nodes"] if n["text"] == "below")
        # Seed bottom is y=50+200=250; new node should sit below (y >= 250+40)
        assert added["y"] >= 290
        assert added["x"] == 100

    def test_list_nodes_compact_shape(self, http_client):
        bid = _board_id("list-nodes")
        nodes = [
            {"id": "a", "x": 10, "y": 20, "width": 250, "height": 200, "text": "alpha", "color": "blue"},
            {"id": "b", "x": 300, "y": 20, "width": 250, "height": 200, "text": "beta", "color": "green"},
        ]
        http_client.post("/canvas/api/board", json={"board_id": bid, "nodes": nodes, "edges": []})
        r = http_client.get(f"/canvas/api/nodes?board={bid}")
        assert r.status_code == 200
        out = r.json().get("nodes") or []
        assert len(out) == 2
        first = {k: out[0].get(k) for k in ("id", "text", "color", "x", "y")}
        assert first == {"id": "a", "text": "alpha", "color": "blue", "x": 10, "y": 20}
        # No bulky fields leaking into agent view
        assert "width" not in out[0]
        assert "height" not in out[0]

    def test_connect_adds_edge_and_is_idempotent(self, http_client):
        bid = _board_id("connect")
        nodes = [
            {"id": "s", "x": 0, "y": 0, "width": 250, "height": 200, "text": "src"},
            {"id": "t", "x": 400, "y": 0, "width": 250, "height": 200, "text": "tgt"},
        ]
        http_client.post("/canvas/api/board", json={"board_id": bid, "nodes": nodes, "edges": []})
        r1 = http_client.post("/canvas/api/connect",
                              json={"board_id": bid, "source_id": "s", "target_id": "t"})
        assert r1.json().get("ok") is True
        assert r1.json().get("existing") is False
        r2 = http_client.post("/canvas/api/connect",
                              json={"board_id": bid, "source_id": "s", "target_id": "t"})
        assert r2.json().get("existing") is True
        back = http_client.get(f"/canvas/api/board?board={bid}").json()
        assert len(back["edges"]) == 1

    def test_connect_rejects_unknown_node(self, http_client):
        bid = _board_id("connect-bad")
        http_client.post("/canvas/api/board", json={"board_id": bid, "nodes": [], "edges": []})
        r = http_client.post("/canvas/api/connect",
                             json={"board_id": bid, "source_id": "nope", "target_id": "also-nope"})
        assert r.json().get("ok") is False

    def test_promote_largest_cluster_to_project(self, http_client):
        bid = _board_id("promote")
        pid = f"{TEST_PREFIX}promoted-proj"
        # Two clusters: {a-b-c} + {isolated}. Largest wins.
        nodes = [
            {"id": "a", "x": 0,   "y": 0,  "width": 250, "height": 200, "text": "task one"},
            {"id": "b", "x": 300, "y": 0,  "width": 250, "height": 200, "text": "- [ ] task two\n- [ ] task three"},
            {"id": "c", "x": 600, "y": 0,  "width": 250, "height": 200, "text": "task four"},
            {"id": "isolated", "x": 0, "y": 400, "width": 250, "height": 200, "text": "not in cluster"},
        ]
        edges = [
            {"id": "e1", "sourceId": "a", "sourceSide": "right", "targetId": "b", "targetSide": "left"},
            {"id": "e2", "sourceId": "b", "sourceSide": "right", "targetId": "c", "targetSide": "left"},
        ]
        http_client.post("/canvas/api/board", json={"board_id": bid, "nodes": nodes, "edges": edges})
        r = http_client.post("/canvas/api/promote",
                             json={"board_id": bid, "project_id": pid})
        assert r.status_code == 200
        data = r.json()
        assert data.get("ok") is True
        assert data.get("nodes_promoted") == 3
        # 1 task from a + 2 from b's checkbox lines + 1 from c = 4 tasks
        assert data.get("tasks_created") == 4
        # Board gets `project` frontmatter (survives across saves)
        back = http_client.get(f"/canvas/api/board?board={bid}").json()
        assert (back.get("meta") or {}).get("project") == pid
        # Re-save without meta — project link must be preserved
        http_client.post("/canvas/api/board", json={"board_id": bid, "nodes": nodes, "edges": edges})
        back2 = http_client.get(f"/canvas/api/board?board={bid}").json()
        assert (back2.get("meta") or {}).get("project") == pid
        # Cleanup: delete the promoted project directory
        from pathlib import Path
        import shutil
        try:
            resp = http_client.get(f"/projects/api/projects/{pid}")
            if resp.status_code == 200:
                proj_dir = Path(resp.json().get("path", "")).parent
                if proj_dir.exists() and pid in str(proj_dir):
                    shutil.rmtree(proj_dir, ignore_errors=True)
        except Exception:
            pass

    def test_promote_empty_board_rejected(self, http_client):
        bid = _board_id("promote-empty")
        http_client.post("/canvas/api/board", json={"board_id": bid, "nodes": [], "edges": []})
        r = http_client.post("/canvas/api/promote",
                             json={"board_id": bid, "project_id": "anywhere"})
        assert r.json().get("ok") is False

    def test_node_type_round_trips(self, http_client):
        bid = _board_id("type-rt")
        nodes = [
            {"id": "t1", "x": 0, "y": 0, "width": 250, "height": 200, "text": "plain"},
            {"id": "v1", "x": 300, "y": 0, "width": 300, "height": 180,
             "text": "", "type": "vault_note", "path": "20_Areas/Career/job.md"},
        ]
        http_client.post("/canvas/api/board", json={"board_id": bid, "nodes": nodes, "edges": []})
        back = http_client.get(f"/canvas/api/board?board={bid}").json()
        byid = {n["id"]: n for n in back.get("nodes", [])}
        # Text node: no `type` leaked (default behavior)
        assert byid["t1"].get("type") is None or byid["t1"].get("type") == "text"
        # Vault-note node: type + path preserved
        assert byid["v1"]["type"] == "vault_note"
        assert byid["v1"]["path"] == "20_Areas/Career/job.md"

    def test_add_vault_node_creates_vault_type(self, http_client):
        bid = _board_id("add-vault")
        http_client.post("/canvas/api/board", json={"board_id": bid, "nodes": [], "edges": []})
        r = http_client.post("/canvas/api/node/vault",
                             json={"board_id": bid, "path": "30_Resources/demo.md"})
        assert r.status_code == 200
        assert r.json().get("ok") is True
        back = http_client.get(f"/canvas/api/board?board={bid}").json()
        assert len(back["nodes"]) == 1
        assert back["nodes"][0]["type"] == "vault_note"
        assert back["nodes"][0]["path"] == "30_Resources/demo.md"

    def test_vault_preview_returns_section_body(self, http_client, tmp_path):
        # Seed a file in the vault and preview it
        from pathlib import Path
        # Discover vault root by reading emptyos.toml
        import tomllib
        with open("emptyos.toml", "rb") as f:
            cfg = tomllib.load(f)
        vault = Path(cfg.get("notes", {}).get("path", ""))
        assert vault.exists(), "vault not configured"
        rel = f"{TEST_PREFIX}canvas-preview.md"
        target = vault / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "---\ntags:\n  - test\n---\n\n## Overview\n\nthe overview body line.\n\n## Other\nignored.\n",
            encoding="utf-8",
        )
        try:
            r = http_client.get(f"/canvas/api/vault-preview?path={rel}")
            assert r.status_code == 200
            data = r.json()
            assert data.get("ok") is True
            assert data.get("section") == "Overview"
            assert "the overview body line." in data.get("body", "")
        finally:
            try:
                target.unlink()
            except Exception:
                pass

    def test_vault_preview_missing_path(self, http_client):
        r = http_client.get("/canvas/api/vault-preview?path=nope/does-not-exist.md")
        assert r.status_code == 200
        assert r.json().get("ok") is False

    def test_node_think_unknown_kind_rejected(self, http_client):
        bid = _board_id("think-bad")
        nodes = [{"id": "s", "x": 0, "y": 0, "width": 250, "height": 200, "text": "topic"}]
        http_client.post("/canvas/api/board", json={"board_id": bid, "nodes": nodes, "edges": []})
        r = http_client.post("/canvas/api/node/think",
                             json={"board_id": bid, "node_id": "s", "prompt_kind": "bogus"})
        assert r.json().get("ok") is False

    def test_node_think_empty_text_rejected(self, http_client):
        bid = _board_id("think-empty")
        nodes = [{"id": "s", "x": 0, "y": 0, "width": 250, "height": 200, "text": ""}]
        http_client.post("/canvas/api/board", json={"board_id": bid, "nodes": nodes, "edges": []})
        r = http_client.post("/canvas/api/node/think",
                             json={"board_id": bid, "node_id": "s", "prompt_kind": "brainstorm"})
        assert r.json().get("ok") is False

    def test_node_search_creates_vault_cluster(self, http_client):
        """Seed a vault file with a rare token; search on a node whose text is that token."""
        from pathlib import Path
        import tomllib
        with open("emptyos.toml", "rb") as f:
            cfg = tomllib.load(f)
        vault = Path(cfg.get("notes", {}).get("path", ""))
        assert vault.exists(), "vault not configured"
        token = f"{TEST_PREFIX}zXqPlumage-unique-42"
        seed_rel = f"{TEST_PREFIX}canvas-search-seed.md"
        seed = vault / seed_rel
        seed.parent.mkdir(parents=True, exist_ok=True)
        seed.write_text(f"---\n---\n\nThe {token} appears here.\n", encoding="utf-8")
        try:
            bid = _board_id("node-search")
            nodes = [{"id": "s", "x": 0, "y": 0, "width": 250, "height": 200, "text": token}]
            http_client.post("/canvas/api/board", json={"board_id": bid, "nodes": nodes, "edges": []})
            r = http_client.post("/canvas/api/node/search",
                                 json={"board_id": bid, "node_id": "s"}, timeout=60)
            assert r.status_code == 200
            data = r.json()
            assert data.get("ok") is True
            assert data.get("count", 0) >= 1
            back = http_client.get(f"/canvas/api/board?board={bid}").json()
            vault_nodes = [n for n in back["nodes"] if n.get("type") == "vault_note"]
            assert any(seed_rel in (n.get("path") or "") for n in vault_nodes)
        finally:
            try:
                seed.unlink()
            except Exception:
                pass

    def test_capture_canvas_tag_routes_to_canvas(self, http_client):
        bid = _board_id("route-ideas")
        text = f"{TEST_PREFIX}make a thing"
        r = http_client.post("/quick-action/api/add", json={"text": text, "tag": f"canvas/{bid}"})
        assert r.status_code == 200
        back = http_client.get(f"/canvas/api/board?board={bid}").json()
        assert any(n.get("text") == text for n in back.get("nodes", []))

    def test_brainstorm_empty_text(self, http_client):
        r = http_client.post("/canvas/api/brainstorm", json={"text": ""})
        assert r.status_code == 200
        assert r.json().get("ideas") == []

    def test_brainstorm_returns_provenance_shape(self, http_client, require_llm):
        # Exercises the live LLM path — timeout generous because the brainstorm
        # prompt runs through self.think().
        r = http_client.post("/canvas/api/brainstorm", json={"text": "ambient music"}, timeout=120)
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data.get("ideas"), list)
        # Provenance present when a real provider ran; dict either way.
        assert isinstance(data.get("provenance"), dict)


@pytest.mark.interactive
class TestCanvasUI:
    def test_ui_loads(self, app_page, page_errors):
        page = app_page("canvas")
        wait_briefly(page, 1500)
        assert page.locator(".toolbar").count() == 1
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_board_picker_populated(self, app_page, page_errors, http_client):
        # Ensure at least one board exists before loading the page.
        bid = _board_id("ui-pick")
        http_client.post("/canvas/api/board", json={"board_id": bid, "nodes": [], "edges": []})
        page = app_page("canvas")
        wait_briefly(page, 1500)
        options = page.locator("#board-picker option").all_text_contents()
        assert any(bid in o for o in options)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_double_click_creates_card(self, app_page, page_errors):
        page = app_page("canvas")
        wait_briefly(page, 1500)
        viewport = page.locator("#viewport")
        box = viewport.bounding_box()
        page.mouse.dblclick(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        wait_briefly(page, 400)
        assert page.locator(".node").count() >= 1
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_edges_render_as_svg_paths(self, app_page, page_errors, http_client):
        """Seed a board with a known edge and assert the `<path class=edge-path>`
        actually rendered in the DOM (regression against the SVG-innerHTML
        namespace hazard)."""
        bid = _board_id("edge-render")
        nodes = [
            {"id": "a", "x": 100, "y": 100, "width": 250, "height": 200, "text": "src"},
            {"id": "b", "x": 500, "y": 100, "width": 250, "height": 200, "text": "tgt"},
        ]
        edges = [{"id": "e1", "sourceId": "a", "sourceSide": "right",
                  "targetId": "b", "targetSide": "left"}]
        http_client.post("/canvas/api/board",
                         json={"board_id": bid, "nodes": nodes, "edges": edges})
        page = app_page(f"canvas/?board={bid}")
        wait_briefly(page, 1500)
        paths = page.locator(".edges-layer path.edge-path")
        assert paths.count() >= 1, "no edge path rendered"
        d = paths.first.get_attribute("d")
        assert d and d.startswith("M "), f"path has no valid d attr: {d!r}"
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_add_card_button(self, app_page, page_errors):
        page = app_page("canvas")
        wait_briefly(page, 1500)
        before = page.locator(".node").count()
        page.locator("#btn-add-node").click()
        wait_briefly(page, 300)
        assert page.locator(".node").count() == before + 1
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])
