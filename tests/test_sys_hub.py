"""System tests: generic hub panel framework (apps/hub/).

The hub at apps/hub/ is a zero-dependency aggregator: it walks every
[[contributes.hub.panel]] entry across loaded apps and renders the result
in priority order. This file tests only what the generic hub guarantees —
the contribution mechanism, API contract, and bare-minimum UI.

Personal life-dashboard tests (cognitive slots, score-ring, pinned, smart bar)
moved to tests/personal/test_sys_hub_life.py — those depend on apps/personal/hub-life/
which is gitignored and not present in CI / fresh clones.
"""

import pytest

from helpers import assert_dict_response
from page_helpers import assert_no_js_errors, wait_briefly


@pytest.mark.api
class TestHubPanelsAPI:

    def test_panels_endpoint_returns_blocks(self, http_client):
        """/hub/api/panels returns {blocks: [...]}"""
        data = assert_dict_response(http_client.get("/hub/api/panels"))
        assert "blocks" in data, f"response missing blocks: {list(data.keys())}"
        assert isinstance(data["blocks"], list)

    def test_panels_blocks_have_required_fields(self, http_client):
        """Every block has id + renderer + priority + items."""
        data = http_client.get("/hub/api/panels").json()
        for b in data.get("blocks", []):
            for key in ("id", "renderer", "priority", "items"):
                assert key in b, f"block missing {key!r}: {b.get('id', '?')}"
            assert isinstance(b["items"], list)

    def test_panels_sorted_by_priority(self, http_client):
        """Blocks emitted in ascending priority order (lower = higher on page)."""
        data = http_client.get("/hub/api/panels").json()
        priorities = [b["priority"] for b in data.get("blocks", [])]
        assert priorities == sorted(priorities), (
            f"blocks not in priority order: {priorities}"
        )

    def test_hub_contributes_its_own_welcome_panel(self, http_client):
        """Hub ships a welcome accent-card so the page is never empty."""
        data = http_client.get("/hub/api/panels").json()
        ids = {b["id"] for b in data.get("blocks", [])}
        assert "hub-welcome" in ids, f"hub-welcome panel missing: {ids}"

    def test_hub_contributes_app_launcher(self, http_client):
        """Hub ships a chips launcher listing every loaded app with a web prefix."""
        data = http_client.get("/hub/api/panels").json()
        launcher = next((b for b in data.get("blocks", []) if b["id"] == "hub-launcher"), None)
        if not launcher:
            pytest.skip("launcher returned None (no other apps loaded)")
        chips = launcher["items"][0].get("data") or []
        assert len(chips) > 0, "launcher should list at least one app"
        for c in chips:
            assert "title" in c and "href" in c, f"chip missing title/href: {c}"

    def test_single_panel_fetch(self, http_client):
        """Fetching one panel returns its full envelope."""
        panels = http_client.get("/hub/api/panels").json().get("blocks", [])
        if not panels:
            pytest.skip("no panels")
        pid = panels[0]["id"]
        data = assert_dict_response(http_client.get(f"/hub/api/panel/{pid}"))
        for key in ("id", "renderer", "source", "data"):
            assert key in data, f"panel {pid} missing {key!r}"

    def test_unknown_panel_returns_error_softly(self, http_client):
        """Unknown panel id returns {error} rather than 5xx."""
        resp = http_client.get("/hub/api/panel/definitely-not-a-panel")
        assert resp.status_code == 200, "should not 5xx on unknown id"
        data = resp.json()
        assert data.get("error"), f"expected error field, got {data}"

    def test_lazy_panels_not_executed_by_default(self, http_client):
        """Panels with lazy=true arrive with data=None + lazy=true."""
        data = http_client.get("/hub/api/panels").json()
        lazy_blocks = [
            b for b in data.get("blocks", [])
            if b["items"] and b["items"][0].get("lazy") is True
        ]
        if not lazy_blocks:
            pytest.skip("no lazy panels currently declared")
        for b in lazy_blocks:
            assert b["items"][0].get("data") is None, (
                f"lazy panel {b['id']} shipped data eagerly"
            )

    def test_panels_all_includes_lazy_executed(self, http_client):
        """/api/panels/all forces lazy contributors to run."""
        data = http_client.get("/hub/api/panels/all").json()
        assert "panels" in data
        for p in data["panels"]:
            assert "lazy" in p, f"panel {p.get('id')} missing lazy flag"


@pytest.mark.interactive
class TestHubUI:

    def test_page_loads(self, page, base_url, page_errors):
        """/hub/ serves the home page and renders blocks via /api/panels."""
        resp = page.goto(base_url + "/hub/", wait_until="domcontentloaded", timeout=15000)
        assert resp.status == 200
        wait_briefly(page, 1500)
        # Welcome card always renders, so .r-accent-card must be present.
        marker = page.locator(".r-accent-card")
        assert marker.count() > 0, "hub page rendered no accent-card (welcome panel missing)"
        assert_no_js_errors(page_errors, allow_patterns=[r"Failed to load resource"])

    def test_welcome_card_has_capture_link(self, page, base_url, page_errors):
        """Welcome card's button links to /quick-action/ (the capture verb)."""
        page.goto(base_url + "/hub/", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 1500)
        # Welcome accent-card href should be /quick-action/
        link = page.locator("a.r-accent-card[href*='/quick-action/']")
        assert link.count() > 0, "welcome card should link to /quick-action/"
        assert_no_js_errors(page_errors, allow_patterns=[r"Failed to load resource"])
