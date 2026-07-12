"""System app tests: Promote (internal dev tooling).

Promote scans devlogs + git for shippable work, drafts platform copy, and
routes each draft through a propose → preview → confirm gate. The draft step
hits a think provider (and wants min_ability=strong), so it's @pytest.mark.llm.
The queue/ledger/meta surfaces and the propose/apply/reject lifecycle on a
*social* draft (no vault write) are covered without LLM.

The LLM test deliberately applies only X/LinkedIn proposals (manual, clipboard
only) and rejects the blurb, so no publish-app vault note is written from CI.
"""

from __future__ import annotations

import pytest

from helpers import assert_ok


@pytest.mark.api
class TestPromoteAPI:
    def test_queue_shape(self, http_client):
        d = assert_ok(http_client.get("/promote/api/queue"))
        assert "candidates" in d and isinstance(d["candidates"], list)
        assert "unpromoted" in d
        assert "publish_available" in d
        for c in d["candidates"]:
            assert c["key"] and c["kind"] in ("devlog", "feature")
            assert "score" in c and "promoted" in c

    def test_meta_shape(self, http_client):
        d = assert_ok(http_client.get("/promote/api/meta"))
        assert d["min_ability"] == "strong"
        assert d.get("ability") in ("weak", "standard", "strong")

    def test_ledger_shape(self, http_client):
        d = assert_ok(http_client.get("/promote/api/ledger"))
        for k in ("last_commit", "promoted", "events"):
            assert k in d

    def test_proposals_list(self, http_client):
        d = assert_ok(http_client.get("/promote/api/proposals"))
        assert isinstance(d["proposals"], list)

    def test_draft_requires_key(self, http_client):
        d = http_client.post("/promote/api/draft", json={}).json()
        assert d.get("error")

    def test_caught_up_returns_contract(self, http_client):
        # git may or may not be available; both shapes are valid.
        d = http_client.post("/promote/api/caught-up").json()
        assert ("ok" in d) or ("error" in d)

    def test_channels_shape(self, http_client):
        # Phase 3 — which platforms have an outbound webhook configured.
        d = assert_ok(http_client.get("/promote/api/channels"))
        assert set(d.keys()) == {"x", "linkedin"}
        assert isinstance(d["x"], bool) and isinstance(d["linkedin"], bool)

    def test_weekly_drafter_dark_by_default(self, http_client):
        # Phase 2 — the weekly cron drafter is OFF unless explicitly enabled.
        # Default install => the manual trigger is a no-op skip (never posts).
        d = http_client.post("/promote/api/run-weekly").json()
        # disabled => skip; if a prior run enabled it, a clean "ok" is also valid.
        assert d.get("skipped") == "disabled" or d.get("ok") is True

    def test_channel_registry_shape(self, http_client):
        # Pillar 2 — channel registry carries audience/fit metadata.
        d = assert_ok(http_client.get("/promote/api/channels/registry"))
        chans = {c["id"]: c for c in d["channels"]}
        assert {"x", "linkedin", "blog", "hn", "landing", "newsletter"} <= set(chans)
        assert chans["hn"]["audience"] and chans["hn"]["fit"]  # metadata present
        assert chans["x"]["actionable"] is True
        assert chans["hn"]["actionable"] is False

    def test_recommend_requires_subject(self, http_client):
        # No candidate/title/source => clean error, never a 500.
        d = http_client.post("/promote/api/recommend", json={}).json()
        assert d.get("error")

    def test_meta_has_designer_flag(self, http_client):
        # Pillar 3 — meta reports whether the designer app is available
        # (drives the "Design landing page" affordance + landing actionability).
        d = assert_ok(http_client.get("/promote/api/meta"))
        assert "designer_available" in d and isinstance(d["designer_available"], bool)

    def test_design_landing_unknown_campaign(self, http_client):
        # Clean error for a missing campaign, never a 500.
        d = http_client.post(
            "/promote/api/campaigns/camp-doesnotexist/design-landing", json={}
        ).json()
        assert d.get("error")

    def test_plan_unknown_campaign(self, http_client):
        # Pillar 4 — planner errors cleanly on a missing campaign, never a 500.
        d = http_client.post(
            "/promote/api/campaigns/camp-doesnotexist/plan", json={"execute": False}
        ).json()
        assert d.get("error")

    def test_campaigns_list_shape(self, http_client):
        # Campaign matrix backbone — list + channel columns.
        d = assert_ok(http_client.get("/promote/api/campaigns"))
        assert isinstance(d["campaigns"], list)
        assert isinstance(d["channels"], list) and d["channels"]
        chans = {c["id"] for c in d["channels"]}
        assert {"x", "linkedin", "blog"} <= chans  # actionable copy channels present

    def test_campaign_create_matrix_delete(self, http_client):
        # Create a campaign from the top candidate, verify its matrix cells,
        # then delete it (no LLM — drafting not exercised here).
        q = assert_ok(http_client.get("/promote/api/queue"))
        if not q["candidates"]:
            pytest.skip("no candidates in this environment")
        key = q["candidates"][0]["key"]
        res = http_client.post("/promote/api/campaigns", json={"candidate_key": key}).json()
        assert res.get("ok")
        camp = res["campaign"]
        cid = camp["id"]
        try:
            cells = {c["channel"]: c for c in camp["cells"]}
            assert {"x", "linkedin", "blog", "landing", "hn"} <= set(cells)
            assert cells["x"]["actionable"] is True
            # hn is always a planned channel (no generator); landing is
            # actionable only when the designer app is installed (pillar 3).
            assert cells["hn"]["actionable"] is False
            assert isinstance(cells["landing"]["actionable"], bool)
            assert all(c["status"] in ("none", "drafted", "published") for c in camp["cells"])
            # detail fetch round-trips
            got = assert_ok(http_client.get(f"/promote/api/campaigns/{cid}"))
            assert got["campaign"]["id"] == cid
        finally:
            d = http_client.request("DELETE", f"/promote/api/campaigns/{cid}").json()
            assert d.get("ok")


@pytest.mark.interactive
class TestPromoteUI:
    def test_page_loads(self, page, page_errors, base_url):
        from page_helpers import assert_no_js_errors

        page.goto(base_url + "/promote/")
        page.wait_for_selector("#queue", timeout=6000)
        assert_no_js_errors(page_errors)

    def test_toolbar_present(self, page, base_url):
        page.goto(base_url + "/promote/")
        assert page.is_visible("text=Refresh queue")
        assert page.is_visible("#model-pill")


@pytest.mark.llm
@pytest.mark.api
class TestPromoteDraftFlow:
    """Live draft → preview → apply(social, manual) / reject(blurb). Slow/paid."""

    def test_draft_preview_apply_reject(self, http_client):
        q = assert_ok(http_client.get("/promote/api/queue"))
        if not q["candidates"]:
            pytest.skip("no promo candidates in this environment")
        key = q["candidates"][0]["key"]

        # Drafting is a real LLM call (can be 30–60s on a local/under-powered
        # provider) — give it a generous timeout rather than the client default.
        res = http_client.post(
            "/promote/api/draft", json={"candidate_key": key}, timeout=180
        ).json()
        assert "ok" in res
        if not res.get("ok"):
            pytest.skip(f"draft unavailable: {res.get('error')}")
        props = res["proposals"]
        assert props, "expected at least one platform draft"

        for p in props:
            assert p["platform"] in ("x", "linkedin", "blurb")
            assert p["status"] == "pending"
            if p["platform"] in ("x", "linkedin"):
                # social → manual only, never auto-posts
                a = http_client.post(
                    f"/promote/api/proposals/{p['id']}/apply"
                ).json()
                assert a.get("ok")
                assert a.get("routed") == "manual"
                assert "clipboard" in a
            else:
                # don't write a publish note from CI — reject the blurb
                r = http_client.post(
                    f"/promote/api/proposals/{p['id']}/reject"
                ).json()
                assert r.get("ok")

        # ledger should now record the candidate as promoted (social applied)
        ledger = assert_ok(http_client.get("/promote/api/ledger"))
        social = [p for p in props if p["platform"] in ("x", "linkedin")]
        if social:
            assert key in ledger["promoted"]
