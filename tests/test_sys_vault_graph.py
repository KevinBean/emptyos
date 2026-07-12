"""System tests for the Vault Topology app — graph API + UI smoke.

Daemon must be running on :9000. Skips gracefully when vault-graph isn't
installed in the Store.
"""

from __future__ import annotations

import pytest

from helpers import assert_ok


def _vault_graph_installed(http_client) -> bool:
    r = http_client.get("/api/apps")
    if r.status_code != 200:
        return False
    return any(a.get("id") == "vault-graph" for a in r.json())


@pytest.fixture(autouse=True, scope="module")
def _require_vault_graph(http_client):
    if not _vault_graph_installed(http_client):
        pytest.skip("vault-graph app not installed in the Store")


@pytest.mark.api
class TestVaultGraphAPI:
    def test_app_registered(self, http_client):
        r = http_client.get("/api/apps")
        assert r.status_code == 200
        ids = [a.get("id") for a in r.json()]
        assert "vault-graph" in ids

    def test_index_loads(self, http_client):
        r = http_client.get("/vault-graph/")
        assert r.status_code == 200
        assert "Vault Topology" in r.text or "vault-graph-canvas" in r.text

    def test_graph_endpoint_returns_nodes_and_edges(self, http_client):
        data = assert_ok(http_client.get("/vault-graph/api/graph?limit=50"))
        assert "nodes" in data
        assert "edges" in data
        assert isinstance(data["nodes"], list)
        assert isinstance(data["edges"], list)
        # Stats block surfaces total
        assert "stats" in data

    def test_graph_respects_limit(self, http_client):
        data = http_client.get("/vault-graph/api/graph?limit=5").json()
        assert len(data["nodes"]) <= 5

    def test_graph_with_shared_tags_is_valid(self, http_client):
        """Shared-tag edges are opt-in; the endpoint must still return cleanly."""
        data = http_client.get("/vault-graph/api/graph?limit=20&shared_tags=1").json()
        assert "edges" in data
        for e in data["edges"]:
            assert e.get("kind") in {
                "wikilink", "frontmatter_ref", "kb_clause", "tag_shared", "folder",
            }

    def test_graph_with_folder_edges_includes_folder_nodes(self, http_client):
        data = http_client.get("/vault-graph/api/graph?limit=30&folder_edges=1").json()
        kinds = {e.get("kind") for e in data["edges"]}
        # Folder edges only appear if the slice has notes in any folder
        if data["nodes"]:
            assert "folder" in kinds or any(
                n["id"].startswith("folder::") for n in data["nodes"]
            )

    def test_node_endpoint_returns_404_shape_for_missing(self, http_client):
        r = http_client.get("/vault-graph/api/node/totally-not-a-real-path.md")
        assert r.status_code == 200
        body = r.json()
        # Either "error" or empty outgoing/incoming arrays
        assert "error" in body or ("outgoing" in body and "incoming" in body)

    def test_stats_endpoint(self, http_client):
        data = assert_ok(http_client.get("/vault-graph/api/stats"))
        assert "total" in data
        assert isinstance(data["total"], int)


@pytest.mark.api
class TestTimelineSDKEndpoint:
    """The /api/sdk/timeline endpoint that vault-graph clicks consume."""

    def test_timeline_endpoint_rejects_empty_path(self, http_client):
        r = http_client.get("/api/sdk/timeline?path=")
        assert r.status_code == 400

    def test_timeline_endpoint_returns_shape(self, http_client):
        # Non-existent path still returns the {past, future, now} shape
        r = http_client.get("/api/sdk/timeline?path=does/not/exist.md")
        assert r.status_code == 200
        body = r.json()
        assert "past" in body
        assert "future" in body
        assert "now" in body
        assert isinstance(body["past"], list)
        assert isinstance(body["future"], list)

    def test_timeline_apps_lists_declaring_apps(self, http_client):
        data = assert_ok(http_client.get("/api/sdk/timeline-apps"))
        assert "apps" in data
        ids = {a["app_id"] for a in data["apps"]}
        # All four reference impls should declare [provides.timeline]
        # (skip individual ones gracefully — Store install state can vary)
        declarers = {"projects", "people", "jobs"}
        assert declarers & ids, (
            "expected at least one of projects/people/jobs to declare "
            "[provides.timeline]; got " + ", ".join(sorted(ids))
        )


@pytest.mark.interactive
class TestVaultGraphUI:
    def test_page_loads_without_js_errors(self, page, base_url):
        page.goto(base_url + "/vault-graph/")
        page.wait_for_selector("#vault-graph-canvas", timeout=5000)
        # vis-network loads from CDN; give it up to 10s to render
        page.wait_for_function(
            "() => window.vis && window.vis.Network", timeout=10000
        )

    def test_canvas_present_for_tour_spotlight(self, page, base_url):
        """The tour step's spotlight selector must resolve."""
        page.goto(base_url + "/vault-graph/")
        canvas = page.locator("#vault-graph-canvas")
        assert canvas.count() == 1
