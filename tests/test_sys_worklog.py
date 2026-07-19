"""System app tests: Work Log — API + UI smoke."""

import pytest

from helpers import TEST_PREFIX, assert_dict_response, assert_ok
from page_helpers import assert_no_js_errors, wait_briefly


@pytest.mark.api
class TestWorklogAPI:
    def test_timeline_items_contract(self, http_client):
        """Life-suite timeline contract (docs/suites/life-cohesion.md):
        every item carries ts/title/kind/href, kind is 'worklog'."""
        data = assert_dict_response(http_client.get("/worklog/api/timeline-items?days=7"))
        items = data.get("items")
        assert isinstance(items, list)
        for it in items[:10]:
            assert {"ts", "title", "kind", "href"} <= set(it), it
            assert it["kind"] == "worklog"

    def test_recent_shape(self, http_client):
        data = assert_dict_response(http_client.get("/worklog/api/recent"))
        assert "days" in data and isinstance(data["days"], list)

    def test_day_shape(self, http_client):
        data = assert_dict_response(http_client.get("/worklog/api/day"))
        for k in ("date", "projects", "plan"):
            assert k in data, f"missing {k}"

    def test_log_then_read_back(self, http_client):
        text = TEST_PREFIX + "wired the API"
        res = assert_dict_response(
            http_client.post("/worklog/api/log",
                             json={"project": TEST_PREFIX + "ProjX", "text": text, "status": "in-progress"}))
        assert res.get("ok"), res
        assert "employer" in res
        day = assert_dict_response(http_client.get("/worklog/api/day"))
        found = any(it["text"] == text and it.get("status") == "in-progress"
                    for g in day["projects"] for it in g["items"])
        assert found, "logged item not read back"

    def test_log_requires_text(self, http_client):
        res = assert_dict_response(http_client.post("/worklog/api/log", json={"project": "X", "text": ""}))
        assert res.get("error")

    def test_log_rejects_conflicting_employer_for_day(self, http_client):
        date_s = "2000-01-01"
        company = TEST_PREFIX + "CompanyA"
        first = assert_dict_response(http_client.post(
            "/worklog/api/log",
            json={"date": date_s, "project": TEST_PREFIX + "EmployerProject",
                  "text": TEST_PREFIX + "company category", "employer": company},
        ))
        assert first.get("ok") is True, first
        conflict = assert_dict_response(http_client.post(
            "/worklog/api/log",
            json={"date": date_s, "project": TEST_PREFIX + "EmployerProject",
                  "text": TEST_PREFIX + "wrong category",
                  "employer": TEST_PREFIX + "CompanyB"},
        ))
        assert "already categorized" in conflict.get("error", "")

    def test_status_flip(self, http_client):
        text = TEST_PREFIX + "flip me"
        proj = TEST_PREFIX + "FlipProj"
        assert_ok(http_client.post("/worklog/api/log", json={"project": proj, "text": text, "status": "todo"}))
        res = assert_dict_response(http_client.post("/worklog/api/status",
                                  json={"project": proj, "item": text, "status": "complete"}))
        assert res.get("ok"), res
        day = assert_dict_response(http_client.get("/worklog/api/day"))
        st = [it.get("status") for g in day["projects"] for it in g["items"] if it["text"] == text]
        assert st and st[0] == "complete"

    def test_projects_list(self, http_client):
        assert_ok(http_client.post("/worklog/api/log",
                  json={"project": TEST_PREFIX + "Catalogued", "text": TEST_PREFIX + "x", "status": "todo"}))
        data = assert_dict_response(http_client.get("/worklog/api/projects"))
        assert "projects" in data and isinstance(data["projects"], list)

    def test_projects_list_includes_active_projects_catalog(self, http_client):
        projects = assert_ok(http_client.get("/projects/api/list"))
        current = [p for p in projects if p.get("name") and p.get("status") not in {
            "archived", "completed", "shelved",
        }]
        if not current:
            pytest.skip("No current project to merge into Worklog")
        data = assert_dict_response(http_client.get("/worklog/api/projects"))
        names = {p.get("name") for p in data.get("projects", [])}
        assert current[0]["name"] in names

    def test_by_project_requires_name(self, http_client):
        data = assert_dict_response(http_client.get("/worklog/api/by-project"))
        assert data.get("error")

    def test_heatmap_shape(self, http_client):
        data = assert_dict_response(http_client.get("/worklog/api/heatmap"))
        assert isinstance(data.get("data"), dict)

    def test_month_cells(self, http_client):
        data = assert_dict_response(http_client.get("/worklog/api/month"))
        assert "cells" in data and isinstance(data["cells"], list)

    def test_status_rollup(self, http_client):
        data = assert_dict_response(http_client.get("/worklog/api/status-rollup"))
        assert "totals" in data and isinstance(data["totals"], dict)

    def test_employers(self, http_client):
        data = assert_dict_response(http_client.get("/worklog/api/employers"))
        assert "employers" in data and "default" in data

    def test_carryover_shape(self, http_client):
        """GET /worklog/api/carryover returns open items from the last prior day."""
        data = assert_dict_response(http_client.get("/worklog/api/carryover"))
        assert "items" in data and isinstance(data["items"], list)
        assert "from" in data
        for it in data["items"]:
            assert it.get("status") in ("in-progress", "blocked", "waiting", "todo", "next")

    @pytest.mark.llm
    def test_rollup_endpoint(self, http_client):
        """GET /worklog/api/rollup returns an AI summary (LLM-hitting)."""
        data = assert_dict_response(http_client.get("/worklog/api/rollup?days=7"))
        assert ("rollup" in data) or data.get("error", "").startswith("no work items")

    @pytest.mark.llm
    def test_smart_parse_returns_parse_only(self, http_client):
        """POST /worklog/api/smart-parse returns a parse and does NOT write."""
        before = assert_dict_response(http_client.get("/worklog/api/day"))
        n_before = sum(len(g["items"]) for g in before.get("projects", []))
        data = assert_dict_response(
            http_client.post("/worklog/api/smart-parse",
                             json={"text": f"finished the {TEST_PREFIX} widget review today"})
        )
        assert not data.get("error"), data
        assert data.get("project") and data.get("status") and "text" in data
        after = assert_dict_response(http_client.get("/worklog/api/day"))
        n_after = sum(len(g["items"]) for g in after.get("projects", []))
        assert n_after == n_before, "smart-parse must not write"

    def test_smart_parse_requires_text(self, http_client):
        data = assert_dict_response(http_client.post("/worklog/api/smart-parse", json={"text": ""}))
        assert data.get("error")

    def test_plan_save(self, http_client):
        res = assert_dict_response(http_client.post("/worklog/api/plan",
                                   json={"text": TEST_PREFIX + "plan the work"}))
        assert res.get("ok")

    def test_update_save(self, http_client):
        res = assert_dict_response(http_client.post("/worklog/api/update",
                                   json={"text": TEST_PREFIX + "end of day note"}))
        assert res.get("ok")

    def test_day_has_update_field(self, http_client):
        day = assert_dict_response(http_client.get("/worklog/api/day"))
        assert "update" in day

    def test_log_then_status_then_readback(self, http_client):
        # End-to-end the companion's page actions exercise: log → flip → read.
        text = TEST_PREFIX + "companion flow item"
        proj = TEST_PREFIX + "CompanionProj"
        assert_ok(http_client.post("/worklog/api/log",
                  json={"project": proj, "text": text, "status": "in-progress"}))
        assert_ok(http_client.post("/worklog/api/status",
                  json={"project": proj, "item": text, "status": "complete"}))
        day = assert_dict_response(http_client.get("/worklog/api/day"))
        st = [it.get("status") for g in day["projects"] for it in g["items"] if it["text"] == text]
        assert st and st[0] == "complete"


@pytest.mark.api
class TestWorklogVerbs:
    """Companion verbs in the unified registry — needs the daemon to have loaded
    the worklog manifest (fresh CI boot or post-restart)."""

    def test_set_status_verb_registered(self, http_client):
        data = assert_dict_response(http_client.get("/voice-assistant/debug/intents"))
        verbs = {i.get("verb") for i in (data.get("registry") or [])}
        assert "worklog.set_status" in verbs, f"worklog.set_status missing from {sorted(verbs)}"

    def test_plan_update_verbs_registered(self, http_client):
        data = assert_dict_response(http_client.get("/voice-assistant/debug/intents"))
        verbs = {i.get("verb") for i in (data.get("registry") or [])}
        assert {"worklog.set_plan", "worklog.set_update"} <= verbs


@pytest.mark.interactive
class TestWorklogUI:
    def test_page_loads_no_js_errors(self, app_page, page_errors):
        page = app_page("worklog")
        assert "Work Log" in page.content()
        assert_no_js_errors(page_errors)

    def test_tabs_present(self, app_page, page_errors):
        page = app_page("worklog")
        for label in ("Timeline", "Calendar", "By project", "Heatmap"):
            assert page.locator(f".tab:has-text('{label}')").count() >= 1
        assert_no_js_errors(page_errors)

    def test_log_via_form(self, app_page, page_errors):
        page = app_page("worklog")
        page.fill("#wl-proj", TEST_PREFIX + "UIProj")
        page.fill("#wl-add-text", TEST_PREFIX + "logged via UI")
        page.click("button:has-text('Log')")
        wait_briefly(page)
        assert_no_js_errors(page_errors)

    def test_companion_actions_registered(self, app_page, page_errors):
        # The page wires itself into the page-assistant sidebar.
        page = app_page("worklog")
        src = page.content()
        assert "EOS.registerActions" in src
        for name in ("wlSetStatus", "applyProse", "wlLogFromDraft", "getPageMetrics"):
            assert name in src, f"missing companion wiring: {name}"
        assert_no_js_errors(page_errors)

    def test_detail_hashroute_open_and_back(self, app_page, page_errors):
        # Exercises the EOS_UI.hashRoute detail flow: log → open day → back.
        page = app_page("worklog")
        page.fill("#wl-proj", TEST_PREFIX + "NavProj")
        page.fill("#wl-add-text", TEST_PREFIX + "nav item")
        page.click("button:has-text('Log')")
        wait_briefly(page)
        page.click(".day-card")
        wait_briefly(page)
        assert page.locator("#view-detail:not(.hidden)").count() == 1
        assert "work" in page.locator("#detail-body").inner_text().lower()
        assert "#" in page.url  # hash-route deep link set
        page.click("button:has-text('Back')")
        wait_briefly(page)
        assert page.locator("#view-detail.hidden").count() == 1
        assert_no_js_errors(page_errors)
