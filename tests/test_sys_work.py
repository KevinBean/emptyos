"""System app tests: Work (outcome-first deliverable production) — 12 use cases.

Non-LLM cases cover the registry, run lifecycle plumbing, and UI smoke.
LLM-marked cases exercise the real plan/produce path (local runs only).
"""

import pytest
from helpers import TEST_PREFIX, assert_dict_response
from page_helpers import assert_no_js_errors


@pytest.mark.api
class TestWorkAPI:
    def test_deliverables_structure(self, http_client):
        data = assert_dict_response(http_client.get("/work/api/deliverables"))
        assert "deliverables" in data
        ids = {d["id"] for d in data["deliverables"]}
        assert {"report", "research-doc"} <= ids  # built-ins always listed
        for d in data["deliverables"]:
            assert {"id", "label", "description", "available", "app"} <= set(d)

    def test_contributed_kinds_present(self, http_client):
        """[[contributes.work.deliverable]] entries from loaded apps appear in
        the gallery with their owning app id."""
        data = assert_dict_response(http_client.get("/work/api/deliverables"))
        by_id = {d["id"]: d for d in data["deliverables"]}
        if "slide-deck" in by_id:
            assert by_id["slide-deck"]["app"] == "viz"
        if "web-page" in by_id:
            assert by_id["web-page"]["app"] == "designer"
        if "presentation" in by_id:
            assert by_id["presentation"]["app"] == "ppt"

    def test_outcome_box_folded_into_smart_bar(self, http_client):
        """The dedicated hub outcome box was folded into the hub smart bar
        (2026-06-11): no work-outcome panel anymore; the work.produce tour
        step spotlights the smart bar, and produce-shaped text reaches
        /work/?ask= via POST /hub/api/route."""
        data = assert_dict_response(http_client.get("/hub/api/panels"))
        box = [b for b in data.get("blocks", []) if b.get("id") == "work-outcome"]
        assert not box, "work-outcome panel should be folded into the smart bar"
        steps = assert_dict_response(http_client.get("/tour/api/steps"))
        produce = [s for s in steps.get("steps", []) if s.get("id") == "work.produce"]
        assert produce, "work.produce tour step missing"
        assert produce[0].get("spotlight") == "#hub-search-mount"

    def test_delete_unknown_run(self, http_client):
        r = http_client.delete("/work/api/runs/nope")
        data = assert_dict_response(r)
        assert data["ok"] is False
        assert "unknown" in data["error"]

    def test_runs_list_carries_extras(self, http_client):
        """Rows are enriched with ask/ts for the re-ask button + relative time."""
        data = assert_dict_response(http_client.get("/work/api/runs"))
        for r in data["runs"]:
            assert "ask" in r and "ts" in r

    def test_refine_unknown_run(self, http_client):
        r = http_client.post("/work/api/runs/nope/refine", json={"instruction": "bigger"})
        data = assert_dict_response(r)
        assert data["ok"] is False

    def test_refine_requires_instruction(self, http_client):
        r = http_client.post("/work/api/runs/nope/refine", json={"instruction": ""})
        data = assert_dict_response(r)
        assert data["ok"] is False
        assert "instruction" in data["error"]

    def test_deliverables_reports_preview_setting(self, http_client):
        data = assert_dict_response(http_client.get("/work/api/deliverables"))
        assert isinstance(data["plan_preview"], bool)

    def test_runs_list_structure(self, http_client):
        data = assert_dict_response(http_client.get("/work/api/runs"))
        assert isinstance(data["runs"], list)

    def test_run_requires_ask(self, http_client):
        r = http_client.post("/work/api/run", json={"ask": ""})
        data = assert_dict_response(r)
        assert data["ok"] is False
        assert "ask" in data["error"]

    def test_run_rejects_unknown_kind(self, http_client):
        r = http_client.post("/work/api/run", json={"ask": TEST_PREFIX + "x", "kind": "hologram"})
        data = assert_dict_response(r)
        assert data["ok"] is False

    def test_unknown_run_id(self, http_client):
        data = assert_dict_response(http_client.get("/work/api/runs/nope-such-run"))
        assert data["ok"] is False

    def test_unknown_run_artifact(self, http_client):
        data = assert_dict_response(http_client.get("/work/api/runs/nope-such-run/artifact"))
        assert data["ok"] is False

    def test_resume_unknown_run(self, http_client):
        r = http_client.post("/work/api/runs/nope-such-run/resume", json={})
        data = assert_dict_response(r)
        assert data["ok"] is False

    @pytest.mark.llm
    def test_plan_pause_and_shape(self, http_client):
        """Full plan stage: run pauses at plan (preview=True) and the plan
        carries kind/title/outline."""
        import time

        r = http_client.post(
            "/work/api/run",
            json={"ask": TEST_PREFIX + "one-paragraph report on testing", "kind": "report", "preview": True},
        )
        data = assert_dict_response(r)
        assert data["ok"] is True, data
        run_id = data["run_id"]
        for _ in range(60):
            s = assert_dict_response(http_client.get(f"/work/api/runs/{run_id}"))
            if s.get("status") in ("paused", "error", "complete"):
                break
            time.sleep(2)
        assert s["status"] == "paused", s.get("error")
        plan = s["results"]["plan"]
        assert plan["kind"] == "report"
        assert plan["title"]


@pytest.mark.interactive
class TestWorkUI:
    def test_page_loads(self, page, base_url, page_errors):
        page.goto(base_url + "/work/")
        page.wait_for_selector("#ask")
        assert_no_js_errors(page_errors)

    def test_ask_param_prefills(self, page, base_url, page_errors):
        """/work/?ask=<text> (the hub outcome-box handoff) prefills the textarea."""
        page.goto(base_url + "/work/?ask=" + "a%20report%20on%20testing")
        page.wait_for_selector("#ask")
        page.wait_for_function("document.getElementById('ask').value.length > 0")
        assert page.locator("#ask").input_value() == "a report on testing"
        assert_no_js_errors(page_errors)

    def test_hub_smart_bar_offers_route_item(self, page, base_url, page_errors):
        """Typing a multi-word outcome into the hub smart bar surfaces the
        'Do this' route item at slot 0 (the folded outcome box's heir)."""
        page.goto(base_url + "/hub/")
        bar = page.locator("#hub-search-mount input")
        bar.wait_for(timeout=15000)
        bar.fill("a report on my notes")
        page.wait_for_selector(".eos-search-item", timeout=5000)
        first = page.locator(".eos-search-item").first.inner_text()
        assert "Do this" in first, f"smart route item should lead the list, got: {first!r}"
        assert_no_js_errors(page_errors)

    def test_hub_smart_bar_single_token_keeps_app_launch(self, page, base_url, page_errors):
        """Single-token queries keep the instant app-launch behavior — no
        smart item, an app match leads the list (zero regression guard)."""
        page.goto(base_url + "/hub/")
        bar = page.locator("#hub-search-mount input")
        bar.wait_for(timeout=15000)
        bar.fill("work")
        page.wait_for_selector(".eos-search-item", timeout=5000)
        first = page.locator(".eos-search-item").first.inner_text()
        assert "Do this" not in first, "single-token query must not smart-route"
        assert_no_js_errors(page_errors)

    def test_gallery_renders_kinds(self, page, base_url):
        page.goto(base_url + "/work/")
        page.wait_for_selector(".work-card")
        assert page.locator(".work-card").count() >= 3

    def test_settings_panel_opens(self, page, base_url):
        page.goto(base_url + "/work/")
        page.wait_for_selector(".btn-settings")
        page.click(".btn-settings")
        page.wait_for_selector("#work-settings-panel", state="visible")

    def test_empty_ask_shows_toast(self, page, base_url):
        page.goto(base_url + "/work/")
        page.wait_for_selector("#run-btn")
        page.click("#run-btn")
        page.wait_for_selector(".eos-toast", state="visible")
