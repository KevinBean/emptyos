"""System app tests: Workspaces — curated app groupings ("Spaces").

The app aggregates membership from built-in defaults + manifest opt-ins +
config, and renders a navigable landing per space. English Academy ships as
the built-in default, so when its member apps are loaded it appears in the
listing with a rich hero from academy.dashboard().
"""

import pytest

from helpers import assert_dict_response


@pytest.mark.api
class TestWorkspacesAPI:
    def test_spaces_returns_list(self, http_client):
        data = assert_dict_response(http_client.get("/workspaces/api/spaces"))
        assert "spaces" in data
        assert isinstance(data["spaces"], list)

    def test_space_entries_have_required_fields(self, http_client):
        data = assert_dict_response(http_client.get("/workspaces/api/spaces"))
        for s in data["spaces"]:
            assert s.get("slug"), f"missing slug: {s}"
            assert s.get("title"), f"missing title: {s}"
            assert isinstance(s.get("members"), list), f"members not a list: {s}"
            assert isinstance(s.get("order", 0), int)
            assert isinstance(s.get("member_count", 0), int)
            assert isinstance(s.get("available_count", 0), int)
            # available_count never exceeds member_count
            assert s["available_count"] <= s["member_count"]

    def test_spaces_sorted_by_order_then_title(self, http_client):
        data = assert_dict_response(http_client.get("/workspaces/api/spaces"))
        keys = [(s.get("order", 100), s.get("title", "").lower()) for s in data["spaces"]]
        assert keys == sorted(keys), f"spaces not sorted: {keys}"

    def test_listed_default_spaces_have_an_available_member(self, http_client):
        """A built-in default space is hidden when no member is installed,
        so every *listed* space must have >=1 available member."""
        data = assert_dict_response(http_client.get("/workspaces/api/spaces"))
        for s in data["spaces"]:
            assert s["available_count"] >= 1, f"listed space with 0 apps: {s['slug']}"

    def test_get_single_space_includes_members_and_hero_key(self, http_client):
        listing = assert_dict_response(http_client.get("/workspaces/api/spaces"))
        if not listing["spaces"]:
            pytest.skip("no spaces available in this environment")
        slug = listing["spaces"][0]["slug"]
        data = assert_dict_response(http_client.get(f"/workspaces/api/spaces/{slug}"))
        assert data.get("slug") == slug
        assert isinstance(data.get("members"), list)
        # hero key is always present (None when the space has no hero_app)
        assert "hero" in data

    def test_unknown_space_returns_error(self, http_client):
        data = assert_dict_response(
            http_client.get("/workspaces/api/spaces/does-not-exist-xyz")
        )
        assert data.get("error")

    def test_english_academy_excludes_hero_app_from_members(self, http_client):
        """academy is the hero_app, so it must not also appear as a member tile."""
        data = assert_dict_response(
            http_client.get("/workspaces/api/spaces/english-academy")
        )
        member_ids = {m["id"] for m in data["members"]}
        assert "academy" not in member_ids
        assert data.get("hero_app") == "academy"
        # the curated member set
        assert {"learn", "reader", "dictionary", "speaking", "shadowing"} <= member_ids

    def test_engineering_space_has_no_hero_but_has_members(self, http_client):
        """Proves the generic shell works without a hero_app."""
        data = assert_dict_response(http_client.get("/workspaces/api/spaces/engineering"))
        if data.get("error"):
            pytest.skip("engineering apps not installed in this environment")
        assert not data.get("hero_app")
        assert data.get("hero") is None
        assert data["member_count"] >= 1

    def test_curated_member_titles_applied(self, http_client):
        """Override titles (e.g. 'Courses' for learn) come from the Space def."""
        data = assert_dict_response(
            http_client.get("/workspaces/api/spaces/english-academy")
        )
        by_id = {m["id"]: m for m in data["members"]}
        if "learn" in by_id:
            assert by_id["learn"]["title"] == "Courses"
        if "reader" in by_id:
            assert by_id["reader"]["title"] == "Interactive stories"
            assert by_id["reader"]["href"] == "/reader/"

    def test_member_entries_carry_href_and_available_flag(self, http_client):
        listing = assert_dict_response(http_client.get("/workspaces/api/spaces"))
        if not listing["spaces"]:
            pytest.skip("no spaces available in this environment")
        slug = listing["spaces"][0]["slug"]
        data = assert_dict_response(http_client.get(f"/workspaces/api/spaces/{slug}"))
        for m in data["members"]:
            assert m.get("id"), f"member missing id: {m}"
            assert m.get("href", "").startswith("/"), f"bad href: {m}"
            assert isinstance(m.get("available"), bool)


@pytest.mark.api
class TestWorkspacesWidgets:
    """Dynamic Spaces — live widgets (embed + panel) rendered on the detail page."""

    def test_every_space_has_widgets_key(self, http_client):
        listing = assert_dict_response(http_client.get("/workspaces/api/spaces"))
        for s in listing["spaces"]:
            data = assert_dict_response(
                http_client.get(f"/workspaces/api/spaces/{s['slug']}")
            )
            assert isinstance(data.get("widgets"), list), f"{s['slug']} widgets not a list"

    def test_news_space_widgets(self, http_client):
        data = assert_dict_response(http_client.get("/workspaces/api/spaces/news"))
        if data.get("error"):
            pytest.skip("news space not available (daily-brief not installed)")
        widgets = data.get("widgets") or []
        assert widgets, "news space should declare widgets"
        for w in widgets:
            assert w.get("type") in ("embed", "panel"), f"bad widget type: {w}"
            assert isinstance(w.get("available"), bool)
            if w["type"] == "embed":
                assert w.get("src", "").endswith("embed=1"), f"embed src not ?embed=1: {w}"
            else:  # panel
                assert "renderer" in w and "data" in w
        # at least one embed (daily-brief) + the panel
        assert any(w["type"] == "embed" for w in widgets)
        assert any(w["type"] == "panel" for w in widgets)


@pytest.mark.api
class TestNewsCenterRetired:
    def test_news_center_no_longer_loads(self, http_client):
        """news-center was retired into _retired/ — its routes must be gone."""
        resp = http_client.get("/news-center/api/stats")
        assert resp.status_code == 404


@pytest.mark.api
class TestWorkspacesAsk:
    def test_english_academy_has_ask(self, http_client):
        data = assert_dict_response(
            http_client.get("/workspaces/api/spaces/english-academy")
        )
        assert data.get("has_ask") is True
        assert data.get("context", {}).get("kb_tags")

    def test_engineering_has_no_ask(self, http_client):
        data = assert_dict_response(http_client.get("/workspaces/api/spaces/engineering"))
        if data.get("error"):
            pytest.skip("engineering apps not installed")
        # Engineering ships no context → no ask surface.
        assert data.get("has_ask") is False

    def test_ask_requires_question(self, http_client):
        data = assert_dict_response(
            http_client.post("/workspaces/api/spaces/english-academy/ask", json={"question": ""})
        )
        assert data.get("error")

    def test_ask_rejects_space_without_context(self, http_client):
        listing = assert_dict_response(http_client.get("/workspaces/api/spaces/engineering"))
        if listing.get("error"):
            pytest.skip("engineering apps not installed")
        data = assert_dict_response(
            http_client.post("/workspaces/api/spaces/engineering/ask", json={"question": "hi"})
        )
        assert data.get("error")

    @pytest.mark.llm
    def test_ask_returns_grounded_answer(self, http_client):
        # A real think call (claude-cli) far exceeds httpx's default timeout.
        data = assert_dict_response(
            http_client.post(
                "/workspaces/api/spaces/english-academy/ask",
                json={"question": "What should I focus on to improve my English?"},
                timeout=180,
            )
        )
        assert data.get("ok") is True
        assert isinstance(data.get("answer"), str) and data["answer"]
        assert isinstance(data.get("sources"), list)
        assert "scoped" in data


@pytest.mark.api
class TestWorkspacesEditor:
    """Membership editor — the editable single source of truth (runtime overrides)."""

    SLUG = "zz-pw-editor-test"

    def _cleanup(self, http_client):
        http_client.delete(f"/workspaces/api/spaces/{self.SLUG}")

    def test_apps_available(self, http_client):
        data = assert_dict_response(http_client.get("/workspaces/api/apps-available"))
        apps = data.get("apps")
        assert isinstance(apps, list) and apps
        for a in apps[:5]:
            assert a.get("id") and "name" in a and isinstance(a.get("enabled"), bool)

    def test_create_rejects_bad_slug(self, http_client):
        data = assert_dict_response(
            http_client.post("/workspaces/api/spaces", json={"slug": "Bad Slug!"})
        )
        assert data.get("error")

    def test_create_rejects_builtin_collision(self, http_client):
        data = assert_dict_response(
            http_client.post("/workspaces/api/spaces", json={"slug": "engineering"})
        )
        assert data.get("error")

    def test_full_lifecycle(self, http_client):
        self._cleanup(http_client)
        try:
            r = assert_dict_response(http_client.post(
                "/workspaces/api/spaces",
                json={"slug": self.SLUG, "title": "PW Editor Test", "icon": "🧪"}))
            assert r.get("ok")

            d = assert_dict_response(http_client.get(f"/workspaces/api/spaces/{self.SLUG}"))
            assert d.get("user_created") is True
            assert d["member_count"] == 0

            avail = assert_dict_response(http_client.get("/workspaces/api/apps-available"))["apps"]
            installed = [a["id"] for a in avail if a["enabled"]]
            assert installed, "need at least one installed app"
            app1 = installed[0]

            assert assert_dict_response(http_client.post(
                f"/workspaces/api/spaces/{self.SLUG}/members", json={"app": app1})).get("ok")
            d = assert_dict_response(http_client.get(f"/workspaces/api/spaces/{self.SLUG}"))
            assert app1 in {m["id"] for m in d["members"]}

            # idempotent add — no duplicate
            http_client.post(f"/workspaces/api/spaces/{self.SLUG}/members", json={"app": app1})
            d = assert_dict_response(http_client.get(f"/workspaces/api/spaces/{self.SLUG}"))
            assert [m["id"] for m in d["members"]].count(app1) == 1

            # set members wholesale
            app2 = installed[1] if len(installed) > 1 else app1
            assert assert_dict_response(http_client.put(
                f"/workspaces/api/spaces/{self.SLUG}/members", json={"members": [app2]})).get("ok")
            d = assert_dict_response(http_client.get(f"/workspaces/api/spaces/{self.SLUG}"))
            assert {m["id"] for m in d["members"]} == {app2}

            # derive bundle
            b = assert_dict_response(http_client.get(f"/workspaces/api/spaces/{self.SLUG}/bundle"))
            assert app2 in b["apps"]
            assert f"[tiers.{self.SLUG}]" in b["tier_snippet"]

            # remove member
            assert_dict_response(http_client.delete(
                f"/workspaces/api/spaces/{self.SLUG}/members/{app2}"))
            d = assert_dict_response(http_client.get(f"/workspaces/api/spaces/{self.SLUG}"))
            assert d["member_count"] == 0

            # delete created space
            r = assert_dict_response(http_client.delete(f"/workspaces/api/spaces/{self.SLUG}"))
            assert r.get("deleted") is True
            assert assert_dict_response(
                http_client.get(f"/workspaces/api/spaces/{self.SLUG}")).get("error")
        finally:
            self._cleanup(http_client)

    def test_edit_builtin_then_reset(self, http_client):
        eng = http_client.get("/workspaces/api/spaces/engineering").json()
        if eng.get("error"):
            pytest.skip("engineering not installed")
        avail = http_client.get("/workspaces/api/apps-available").json()["apps"]
        members = {m["id"] for m in eng["members"]}
        cand = next((a["id"] for a in avail
                     if a["enabled"] and a["id"] not in members and a["id"] != "engineering"), None)
        if not cand:
            pytest.skip("no candidate app to add")
        try:
            assert http_client.post(
                "/workspaces/api/spaces/engineering/members", json={"app": cand}).json().get("ok")
            d = http_client.get("/workspaces/api/spaces/engineering").json()
            assert d.get("edited") is True
            assert cand in {m["id"] for m in d["members"]}
        finally:
            rr = http_client.delete("/workspaces/api/spaces/engineering").json()
            assert rr.get("ok")
            d = http_client.get("/workspaces/api/spaces/engineering").json()
            assert cand not in {m["id"] for m in d["members"]}, "reset should revert to default members"


@pytest.mark.interactive
class TestWorkspacesUI:
    def test_index_page_loads(self, page, base_url):
        page.goto(f"{base_url}/workspaces/")
        page.wait_for_selector("#spaces-grid")
        # Either space cards render, or the empty state shows — both are valid.
        page.wait_for_selector(".space-card, #index-empty *", timeout=5000)

    def test_open_space_shows_detail(self, page, base_url):
        page.goto(f"{base_url}/workspaces/")
        page.wait_for_selector("#spaces-grid")
        cards = page.locator(".space-card")
        if cards.count() == 0:
            pytest.skip("no spaces available to open")
        cards.first.click()
        page.wait_for_selector("#detail-view:not([hidden])", timeout=5000)
        page.wait_for_selector("#member-grid")
        # Back button returns to the index.
        page.locator(".back-btn").click()
        page.wait_for_selector("#index-view:not([hidden])", timeout=5000)
