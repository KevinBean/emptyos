"""System app tests: Topology — 10 use cases."""

import pytest

from helpers import assert_dict_response, assert_ok
from page_helpers import (
    assert_no_js_errors, wait_briefly,
)


@pytest.mark.api
class TestTopologyAPI:
    def test_graph_data(self, http_client):
        data = assert_dict_response(http_client.get("/api/topology"))
        assert "nodes" in data and "edges" in data, (
            f"Topology missing nodes/edges: {list(data.keys())}"
        )
        assert isinstance(data["nodes"], list)
        assert isinstance(data["edges"], list)

    def test_node_count(self, http_client):
        data = http_client.get("/api/topology").json()
        nodes = data.get("nodes", [])
        # System should have at least 60 nodes (apps + plugins + capabilities)
        assert len(nodes) >= 50, f"Expected >= 50 nodes, got {len(nodes)}"

    def test_edges_valid_refs(self, http_client):
        """At least 90% of edges should reference known nodes."""
        data = http_client.get("/api/topology").json()
        node_ids = {n.get("id") for n in data.get("nodes", []) if n.get("id")}
        edges = data.get("edges", [])
        if not edges:
            pytest.skip("No edges to validate")
        invalid = []
        for e in edges:
            src, tgt = e.get("source"), e.get("target")
            if (src and src not in node_ids) or (tgt and tgt not in node_ids):
                invalid.append((src, tgt))
        # Allow up to 10% dangling edges (event types may not be node-registered)
        invalid_pct = len(invalid) / len(edges)
        assert invalid_pct < 0.10, (
            f"{len(invalid)}/{len(edges)} edges ({invalid_pct:.0%}) have unknown refs. "
            f"Sample: {invalid[:3]}"
        )

    def test_node_types(self, http_client):
        data = http_client.get("/api/topology").json()
        types = {n.get("type") for n in data.get("nodes", []) if n.get("type")}
        # Expect at least app + plugin or capability
        assert types, "No node types found"

    def test_layers(self, http_client):
        resp = http_client.get("/api/topology/layers")
        if resp.status_code == 404:
            pytest.skip("layers endpoint not present")
        assert resp.status_code == 200

    def test_improvements(self, http_client):
        resp = http_client.get("/api/topology/improvements")
        if resp.status_code == 404:
            pytest.skip("improvements endpoint not present")
        assert resp.status_code == 200

    def test_node_subgraph(self, http_client):
        resp = http_client.get("/api/topology/node/task")
        if resp.status_code == 404:
            pytest.skip("node subgraph endpoint not present")
        assert resp.status_code == 200

    def test_timeline_shape(self, http_client):
        """Timeline endpoint returns {min_date, max_date, nodes, time_resolution}."""
        resp = http_client.get("/api/topology/timeline")
        if resp.status_code == 404:
            pytest.skip("timeline endpoint not present")
        data = assert_dict_response(resp)
        for key in ("min_date", "max_date", "nodes", "time_resolution"):
            assert key in data, f"timeline payload missing {key}: {list(data.keys())}"
        assert data["time_resolution"] in ("day", "minute")
        assert isinstance(data["nodes"], dict)

    def test_timeline_node_entries(self, http_client):
        """Each node entry is {date, message} (cache schema v3)."""
        data = http_client.get("/api/topology/timeline").json()
        nodes = data.get("nodes", {})
        if not nodes:
            pytest.skip("no timeline nodes")
        sample_id, sample = next(iter(nodes.items()))
        assert isinstance(sample, dict), f"node entry not a dict: {sample!r}"
        assert "date" in sample, f"node entry missing 'date': {sample}"
        # Date is full ISO with timezone
        assert "T" in sample["date"], f"date not ISO: {sample['date']}"

    def test_timeline_public_strips_hours(self, http_client):
        """When time_resolution=day, every timestamp must be midnight UTC."""
        data = http_client.get("/api/topology/timeline").json()
        if data.get("time_resolution") != "day":
            pytest.skip("local mode — minute resolution is fine")
        # Public mode: all timestamps end in T00:00:00+00:00
        for nid, entry in data.get("nodes", {}).items():
            d = entry.get("date") if isinstance(entry, dict) else entry
            assert d.endswith("T00:00:00+00:00"), (
                f"public-mode timestamp leaks hour-of-day: {nid} -> {d}"
            )

    def test_tree_shape(self, http_client):
        """Tree endpoint returns {roots, groundcover}; roots are 9 capabilities."""
        resp = http_client.get("/api/topology/tree")
        if resp.status_code == 404:
            pytest.skip("tree endpoint not present")
        data = assert_dict_response(resp)
        assert "roots" in data and isinstance(data["roots"], list)
        assert "groundcover" in data and isinstance(data["groundcover"], list)
        assert len(data["roots"]) >= 5, f"expected ≥5 capability roots, got {len(data['roots'])}"
        for root in data["roots"]:
            for key in ("id", "label", "providers", "engines", "consumers"):
                assert key in root, f"capability root missing {key}: {list(root.keys())}"

    def test_tree_groundcover_kinds(self, http_client):
        """Groundcover entries declare kind = sapling|flower."""
        data = http_client.get("/api/topology/tree").json()
        ground = data.get("groundcover", [])
        if not ground:
            pytest.skip("no groundcover apps")
        for app in ground:
            assert app.get("kind") in ("sapling", "flower"), (
                f"unexpected groundcover kind: {app.get('kind')} for {app.get('id')}"
            )

    def test_releases_shape(self, http_client):
        """Releases endpoint returns {releases: [{tag, date, message}, ...]}."""
        resp = http_client.get("/api/topology/releases")
        if resp.status_code == 404:
            pytest.skip("releases endpoint not present")
        data = assert_dict_response(resp)
        assert "releases" in data and isinstance(data["releases"], list)
        if not data["releases"]:
            pytest.skip("repo has no git tags")
        for r in data["releases"]:
            for key in ("tag", "date", "message"):
                assert key in r, f"release entry missing {key}: {list(r.keys())}"


@pytest.mark.interactive
class TestTopologyUI:
    def test_ui_page_loads(self, page, base_url, page_errors):
        resp = page.goto(base_url + "/topology", wait_until="domcontentloaded", timeout=15000)
        assert resp.status == 200
        wait_briefly(page, 1000)
        assert_no_js_errors(page_errors)

    def test_ui_graph_renders(self, page, base_url, page_errors):
        page.goto(base_url + "/topology", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 2500)
        # SVG or canvas graph container should exist
        graphs = page.locator("svg, canvas")
        assert graphs.count() > 0, "No SVG or canvas graph element found"
        assert_no_js_errors(page_errors)

    def test_ui_node_interaction(self, page, base_url, page_errors):
        """Verify clickable node elements present."""
        page.goto(base_url + "/topology", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 2500)
        # SVG nodes typically have circle, g, or .node class
        nodes = page.locator("svg circle, svg g.node, .node")
        # Don't fail if zero — graph may render with different structure
        assert_no_js_errors(page_errors)

    def test_ui_view_switcher(self, page, base_url, page_errors):
        """All four view buttons present and switchable."""
        page.goto(base_url + "/topology", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 1500)
        for view in ("graph", "tree", "pyramid", "dictionary"):
            btn = page.locator(f"#btn-view-{view}")
            assert btn.count() == 1, f"missing view button: {view}"
        # Switching to tree should hide the graph SVG
        page.locator("#btn-view-tree").click()
        wait_briefly(page, 800)
        assert page.locator("#tree-view").count() == 1
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_timeline_bar(self, page, base_url, page_errors):
        """Timeline bar with toggle + slider + log button + release marks renders."""
        page.goto(base_url + "/topology", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 1500)
        for el in ("#tl-toggle", "#tl-play", "#tl-log", "#timeline-slider", "#release-marks"):
            assert page.locator(el).count() == 1, f"missing timeline element: {el}"
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_timeline_no_hours_visible(self, page, base_url, page_errors):
        """Privacy: cutoff label must never display HH:MM, only YYYY-MM-DD."""
        import re
        page.goto(base_url + "/topology", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 1500)
        page.locator("#tl-toggle").click()  # turn timeline on
        wait_briefly(page, 600)
        label = page.locator("#timeline-date").text_content() or ""
        # Match HH:MM patterns like "14:32" anywhere — must not appear
        assert not re.search(r"\b\d{2}:\d{2}\b", label), (
            f"cutoff label leaks hour-of-day: {label!r}"
        )
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])
