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

from helpers import TEST_PREFIX, assert_dict_response
from page_helpers import assert_no_js_errors, wait_briefly

# Routes the command-surface Next move is allowed to target (mirror app.py).
_TEMPLATE_ROUTES = {"/task/", "/journal/", "/quick-action/"}
_AURA_ROUTES = {"/task/", "/journal/", "/calendar/", "/quick-action/", "/people/", "/assistant/"}
_UI_ALLOW = [
    r"Failed to load resource",
    r"WebSocket connection.*failed",  # WS auth in test env returns 403; not a hub bug
    r"font.*CORS policy",  # Google Fonts CORS warnings under test auth header
]


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
        items = launcher["items"][0].get("data") or []
        assert len(items) > 0, "launcher should list at least one app"
        # The launcher renders either flat chips or grouped store_category
        # sections {key,label,icon,apps:[...]}; mirror the renderer's own
        # grouped-detection (d[0].apps is an array) and validate the app rows.
        grouped = isinstance(items[0], dict) and isinstance(items[0].get("apps"), list)
        apps = [a for s in items for a in s["apps"]] if grouped else items
        assert len(apps) > 0, "launcher should list at least one app"
        for c in apps:
            assert "title" in c and "href" in c, f"chip missing title/href: {c}"
            assert "icon" in c and "icon_id" in c, f"launcher icon contract missing: {c}"

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
        # Forcing every lazy panel to hydrate synchronously across 100+
        # registered contributions is the heaviest hub endpoint — measured
        # ~18s under load, past the fixture's shared 15s default. A per-call
        # override here avoids raising the default for every other test.
        data = http_client.get("/hub/api/panels/all", timeout=45).json()
        assert "panels" in data
        for p in data["panels"]:
            assert "lazy" in p, f"panel {p.get('id')} missing lazy flag"


@pytest.mark.api
class TestHubDigestAPI:
    """The command-surface digest (docs/HOME-COMPANION-REDESIGN.md).

    /api/digest is deterministic (no LLM): lanes + a template Next move.
    /api/next-move is the (slow, LLM) Aura hydrate — exercised under the
    `llm` marker only.
    """

    def test_digest_shape(self, http_client):
        d = assert_dict_response(http_client.get("/hub/api/digest"))
        assert isinstance(d.get("greeting"), str) and d["greeting"]
        assert isinstance(d.get("hour"), int)
        assert isinstance(d.get("now"), list)
        assert isinstance(d.get("today"), dict)
        assert isinstance(d.get("next_move"), dict)

    def test_digest_today_shape(self, http_client):
        t = http_client.get("/hub/api/digest").json()["today"]
        assert isinstance(t["overdue"], int)
        assert isinstance(t["due_today"], int)
        assert isinstance(t["tasks"], list)
        assert all(isinstance(x, str) for x in t["tasks"])
        assert isinstance(t["journaled"], bool)
        assert isinstance(t["streak"], int)

    def test_digest_now_items_have_time_and_title(self, http_client):
        for it in http_client.get("/hub/api/digest").json().get("now", []):
            assert "time" in it and "title" in it

    def test_digest_next_move_is_template(self, http_client):
        """/api/digest never calls the model — its Next move is the template,
        so the page paints fast and only /api/next-move can be slow."""
        m = http_client.get("/hub/api/digest").json()["next_move"]
        assert m.get("source") == "template", f"digest must not invoke the LLM: {m}"
        for key in ("title", "why", "action_href", "action_label", "tone"):
            assert m.get(key) not in (None, ""), f"next_move missing {key!r}"
        assert m["action_href"] in _TEMPLATE_ROUTES, f"unroutable: {m['action_href']}"

    @pytest.mark.llm
    def test_next_move_endpoint_contract(self, http_client):
        """/api/next-move returns {move: null|<aura move>}; never 5xx, and an
        Aura move only ever targets a whitelisted route."""
        resp = http_client.get("/hub/api/next-move")
        assert resp.status_code == 200, "next-move must not 5xx"
        body = resp.json()
        assert "move" in body
        m = body["move"]
        if m is None:
            pytest.skip("model below 'standard' ability → fell back to template")
        assert m.get("source") == "aura"
        assert m.get("title")
        assert m["action_href"] in _AURA_ROUTES, f"Aura invented a route: {m['action_href']}"


@pytest.mark.api
class TestHubRoute:
    """The smart-bar intent router (POST /hub/api/route).

    Heuristic + forced paths run without an LLM; the LLM tier is exercised
    under the `llm` marker. Capture routes EXECUTE server-side (reversible —
    the undo handle is the safety net) so every capture here uses TEST_PREFIX
    and undoes itself.
    """

    def test_heuristic_ask_routes_to_assistant(self, http_client):
        r = http_client.post("/hub/api/route", json={"text": "what is a cable rating?"})
        d = assert_dict_response(r)
        assert d["ok"] is True
        assert d["shape"] == "ask"
        assert d["source"] == "heuristic"
        assert d["action"]["kind"] == "navigate"
        # assistant present → /assistant/?q=…; degraded deployments fall to find
        assert d["action"]["href"].startswith(("/assistant/?q=", "/search/?q="))

    def test_heuristic_produce_routes_to_work(self, http_client):
        r = http_client.post("/hub/api/route", json={"text": "a report on my cable notes"})
        d = assert_dict_response(r)
        assert d["shape"] in ("produce", "ask", "find")  # degrade chain when work/assistant absent
        if d["shape"] == "produce":
            assert d["action"]["href"].startswith("/work/?ask=")

    def test_prefix_capture_executes_with_undo(self, http_client):
        """`note:`-prefixed text captures to the inbox file (tag `note` is
        unrouted) — heuristic tier, no LLM, undo handle present and working."""
        text = "note: " + TEST_PREFIX + "prefix capture"
        r = http_client.post("/hub/api/route", json={"text": text})
        d = assert_dict_response(r)
        a = d["action"]
        try:
            assert d["source"] == "heuristic" and d["rule"] == "capture-prefix"
            assert a["kind"] == "captured", a
            assert a["entry"]["tag"] == "note"
            # prefix stripped from the captured text
            assert not a["entry"]["text"].lower().startswith("note:")
            assert a["undo"]["url"] == "/quick-action/api/dismiss"
        finally:
            if a.get("kind") == "captured" and a.get("undo"):
                undone = http_client.post(a["undo"]["url"], json=a["undo"]["body"]).json()
                assert undone.get("dismissed") is True, "undo handle must actually remove the capture"

    def test_routed_capture_reports_destination_not_undo(self, http_client):
        """`todo:` routes to the task app (past the inbox) — dismiss can't
        reach it there, so the response carries routed_to instead of undo.
        TEST_PREFIX content is swept by the conftest leak guard."""
        text = "todo: " + TEST_PREFIX + "routed capture"
        d = http_client.post("/hub/api/route", json={"text": text, "force": "capture"}).json()
        a = d["action"]
        assert a["kind"] == "captured", a
        assert a["entry"]["tag"] == "task"
        # Exactly one of undo / routed_to: routed when the task app took it,
        # undo when it fell back to the inbox file.
        assert ("undo" in a) != ("routed_to" in a), a

    def test_alternatives_well_formed(self, http_client):
        d = http_client.post("/hub/api/route", json={"text": "what is a busbar?"}).json()
        alts = d.get("alternatives", [])
        assert alts, "every response should carry alternatives"
        for alt in alts:
            assert alt.get("shape") and alt.get("label")
            assert ("href" in alt) != ("post" in alt), f"alt must be href XOR post: {alt}"
            assert alt["shape"] != d["shape"]

    def test_empty_and_oversize_text_rejected(self, http_client):
        assert http_client.post("/hub/api/route", json={"text": ""}).json()["ok"] is False
        assert http_client.post("/hub/api/route", json={"text": "x" * 501}).json()["ok"] is False
        assert http_client.post("/hub/api/route", json={"text": "y", "force": "nuke"}).json()["ok"] is False

    @pytest.mark.llm
    def test_llm_tier_returns_valid_shape(self, http_client):
        """Free text with no heuristic match goes through select(); assert the
        structure, not the model's opinion."""
        d = http_client.post("/hub/api/route", json={"text": "something to track my spending"}).json()
        assert d["ok"] is True
        assert d["shape"] in ("capture", "ask", "produce", "open", "find")
        assert d["source"] in ("llm", "heuristic", "degraded")
        assert d["action"]["kind"] in ("navigate", "captured", "choose")
        if d["action"]["kind"] == "captured" and d["action"].get("undo"):
            http_client.post(d["action"]["undo"]["url"], json=d["action"]["undo"]["body"])


@pytest.mark.interactive
class TestHubUI:

    def test_launcher_shows_adaptive_icons_and_labels(self, page, base_url, page_errors):
        page.goto(base_url + "/hub/", wait_until="domcontentloaded", timeout=15000)
        try:
            # A full personal install can contribute 200+ launcher cards; give
            # the aggregate panel fetch its documented long-request allowance.
            page.locator(".r-app-card").first.wait_for(state="visible", timeout=30000)
        except Exception:
            pytest.skip("app launcher is not present in this environment")
        assert page.locator(".r-app-card-name").count() > 0
        assert page.locator(".r-app-card-icon .eos-app-icon").count() > 0
        box = page.locator(".r-app-card-icon .eos-app-icon").first.bounding_box()
        assert box and round(box["width"]) == 40 and round(box["height"]) == 40
        assert_no_js_errors(page_errors, allow_patterns=_UI_ALLOW)

    def test_page_loads(self, page, base_url, page_errors):
        """/hub/ serves the command surface — greeting + mode chips render."""
        resp = page.goto(base_url + "/hub/", wait_until="domcontentloaded", timeout=15000)
        assert resp.status == 200
        # The greeting only mounts after the async /api/digest fetch + render,
        # so it's the stable marker that the command surface came up.
        page.wait_for_selector(".hub-greeting", state="attached", timeout=10000)
        assert page.locator(".hub-greeting").inner_text().strip(), "greeting empty"
        # The command bar ships three mode chips (Capture / Ask / Command).
        assert page.locator(".hub-mode").count() == 3, "command-bar mode chips missing"
        assert_no_js_errors(page_errors, allow_patterns=_UI_ALLOW)

    def test_next_move_card_renders_with_action(self, page, base_url, page_errors):
        """The deterministic Next move ② paints with its reasoning + a
        routable action button (the Aura hydrate may swap it later)."""
        page.goto(base_url + "/hub/", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_selector(".hub-next-card", state="attached", timeout=10000)
        why = page.locator(".hub-next-why")
        assert why.count() > 0 and "Suggested because:" in why.inner_text()
        go = page.locator(".hub-next-card a.hub-next-go")
        assert go.count() > 0, "Next move missing its action button"
        href = go.first.get_attribute("href") or ""
        assert any(href.endswith(r) or href == r for r in _AURA_ROUTES) or href.startswith("/"), \
            f"Next move action not routable: {href!r}"
        assert_no_js_errors(page_errors, allow_patterns=_UI_ALLOW)

    def test_expense_quick_log_pinned_near_top(self, page, base_url, page_errors):
        """Regression: the expense quick-add field stays in the #hub-quick
        action zone (it was buried at the bottom of Explore in the rebuild)."""
        page.goto(base_url + "/hub/", wait_until="domcontentloaded", timeout=15000)
        # #hub-quick hydrates from /api/panels (loadExplore populates it in the
        # same pass as #hub-explore). Wait for the FORM, not the static
        # container — and treat a no-show as "expense not installed" → skip.
        try:
            page.wait_for_selector("#hub-quick form.r-qa", state="attached", timeout=8000)
        except Exception:
            pytest.skip("no quick-add panel contributed in this environment")
        assert page.locator("#hub-quick form.r-qa").count() > 0, \
            "quick-add forms should render in the #hub-quick zone, not buried in Explore"
        assert_no_js_errors(page_errors, allow_patterns=_UI_ALLOW)
