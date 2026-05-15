"""System tests for the Explore app — Flipbook-style visual exploration."""

from __future__ import annotations

import pytest

from page_helpers import assert_no_js_errors, wait_briefly


@pytest.mark.api
class TestExploreAPI:
    def test_app_registered(self, http_client):
        r = http_client.get("/api/apps")
        assert r.status_code == 200
        ids = [a.get("id") for a in r.json()]
        assert "explore" in ids

    def test_index_loads(self, http_client):
        r = http_client.get("/explore/")
        assert r.status_code == 200
        assert "Explore" in r.text or "explore" in r.text

    def test_start_requires_topic(self, http_client):
        r = http_client.post("/explore/api/start", json={})
        assert r.status_code == 200
        assert "error" in r.json()

    def test_expand_requires_label(self, http_client):
        r = http_client.post("/explore/api/expand", json={"parents": ["x"]})
        assert r.status_code == 200
        assert "error" in r.json()

    def test_save_requires_title(self, http_client):
        r = http_client.post("/explore/api/save", json={"page": {}})
        assert r.status_code == 200
        assert "error" in r.json()

    def test_list_returns_items(self, http_client):
        r = http_client.get("/explore/api/list")
        assert r.status_code == 200
        body = r.json()
        assert "items" in body
        assert isinstance(body["items"], list)

    def test_graph_shape(self, http_client):
        """`/explore/api/graph` returns {nodes, edges, stats}."""
        r = http_client.get("/explore/api/graph?limit=50")
        assert r.status_code == 200
        body = r.json()
        assert "nodes" in body
        assert "edges" in body
        assert isinstance(body["nodes"], list)
        assert isinstance(body["edges"], list)

    def test_graph_limit_capped(self, http_client):
        """Limit caps at 1500."""
        body = http_client.get("/explore/api/graph?limit=99999").json()
        assert len(body.get("nodes", [])) <= 1500

    def test_graph_page_loads(self, http_client):
        """The graph.html static page is served."""
        r = http_client.get("/explore/pages/graph.html")
        assert r.status_code == 200
        assert "vis-network" in r.text

    def test_symbols_list(self, http_client):
        r = http_client.get("/explore/api/symbols")
        assert r.status_code == 200
        body = r.json()
        assert "symbols" in body
        # Demo seed should populate at least one symbol on first boot
        assert isinstance(body["symbols"], list)

    def test_symbol_save_requires_name(self, http_client):
        r = http_client.post("/explore/api/symbols", json={"svg": "<svg/>"})
        assert r.status_code == 200
        assert "error" in r.json()

    def test_refine_requires_topic(self, http_client):
        r = http_client.post("/explore/api/refine_anchors", json={})
        assert r.status_code == 200
        assert "error" in r.json()

    def test_detail_requires_label(self, http_client):
        r = http_client.post("/explore/api/detail", json={})
        assert r.status_code == 200
        assert "error" in r.json()

    @pytest.mark.llm
    def test_start_returns_page_shape(self, http_client):
        r = http_client.post(
            "/explore/api/start",
            json={"topic": "underground power cables"},
            timeout=120,
        )
        assert r.status_code == 200
        page = r.json()
        for key in ("title", "svg", "callouts", "breadcrumb"):
            assert key in page
        assert isinstance(page["callouts"], list)
        assert page["breadcrumb"][-1] == "underground power cables"

    @pytest.mark.llm
    def test_expand_extends_breadcrumb(self, http_client):
        r = http_client.post(
            "/explore/api/expand",
            json={"label": "Conductor", "parents": ["underground power cables"]},
            timeout=120,
        )
        assert r.status_code == 200
        page = r.json()
        assert page["breadcrumb"] == ["underground power cables", "Conductor"]


@pytest.mark.interactive
class TestExploreUI:
    def test_ui_start_screen_renders(self, app_page, page_errors):
        page = app_page("explore")
        wait_briefly(page)
        assert page.locator("#ex-topic").is_visible()
        assert "Explore" in page.text_content("body")
        assert_no_js_errors(page_errors)

    def test_ui_saved_list_loads(self, app_page, page_errors):
        page = app_page("explore")
        # The saved-list panel is async — wait for either items or empty state
        page.wait_for_selector(".ex-saved-list h3, .ex-saved-empty", timeout=8000)
        assert_no_js_errors(page_errors)

    def test_ui_symbol_library_loads(self, app_page, page_errors):
        page = app_page("explore")
        # Symbol library should render (demo seed → at least one item)
        page.wait_for_selector("#ex-symbols h3, #ex-symbols .ex-saved-empty",
                               timeout=8000)
        assert_no_js_errors(page_errors)

    def test_ui_eos_components_present(self, app_page, page_errors):
        page = app_page("explore")
        wait_briefly(page)
        # eos-components.js must load — confirms shared UI helpers are wired
        ok = page.evaluate("() => typeof EOS_UI !== 'undefined' && "
                           "typeof EOS_UI.confirm === 'function' && "
                           "typeof EOS_UI.toast === 'function'")
        assert ok, "EOS_UI helpers not loaded on /explore/"
        assert_no_js_errors(page_errors)
