"""System app tests: Work Log — API + UI smoke.

Every test that WRITES targets ``WORKLOG_TEST_DATE``, never ``date.today()``.
A worklog day note is a real user file and ``/api/plan`` + ``/api/update``
*replace* their section, so writing to today destroys whatever was written that
morning. Read-only shape checks may still use today.
"""

from uuid import uuid4

import pytest
from helpers import (
    TEST_PREFIX,
    WORKLOG_EMPLOYER_TEST_DATE,
    WORKLOG_TEST_DATE,
    assert_dict_response,
    assert_ok,
)
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
                             json={"date": WORKLOG_TEST_DATE,
                                   "project": TEST_PREFIX + "ProjX", "text": text, "status": "in-progress"}))
        assert res.get("ok"), res
        assert "employer" in res
        day = assert_dict_response(http_client.get(f"/worklog/api/day?date={WORKLOG_TEST_DATE}"))
        found = any(it["text"] == text and it.get("status") == "in-progress"
                    for g in day["projects"] for it in g["items"])
        assert found, "logged item not read back"

    def test_log_requires_text(self, http_client):
        res = assert_dict_response(http_client.post("/worklog/api/log", json={"project": "X", "text": ""}))
        assert res.get("error")

    def test_log_rejects_conflicting_employer_for_day(self, http_client):
        date_s = WORKLOG_EMPLOYER_TEST_DATE
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
        assert_ok(http_client.post("/worklog/api/log", json={"date": WORKLOG_TEST_DATE,
                  "project": proj, "text": text, "status": "todo"}))
        res = assert_dict_response(http_client.post("/worklog/api/status",
                                  json={"date": WORKLOG_TEST_DATE,
                                        "project": proj, "item": text, "status": "complete"}))
        assert res.get("ok"), res
        day = assert_dict_response(http_client.get(f"/worklog/api/day?date={WORKLOG_TEST_DATE}"))
        st = [it.get("status") for g in day["projects"] for it in g["items"] if it["text"] == text]
        assert st and st[0] == "complete"

    def test_projects_list(self, http_client):
        assert_ok(http_client.post("/worklog/api/log",
                  json={"date": WORKLOG_TEST_DATE,
                        "project": TEST_PREFIX + "Catalogued", "text": TEST_PREFIX + "x", "status": "todo"}))
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

    def test_day_exposes_recorded_hours(self, http_client):
        """/api/day carries both time conventions + the de-duplicated total.

        Parsing itself is pinned by tests/test_unit_worklog_logged_time.py;
        this is the contract the PDF and the UI hours chip both read.
        """
        day = assert_dict_response(
            http_client.get(f"/worklog/api/day?date={WORKLOG_TEST_DATE}"),
            required_keys=["timesheet", "logged_time", "hours"])
        assert isinstance(day["logged_time"], list)
        assert isinstance(day["hours"], (int, float))

    def test_recent_days_carry_hours(self, http_client):
        """The timeline hours chip + the 'Hours (7d)' stat read this."""
        data = assert_dict_response(http_client.get("/worklog/api/recent?days=30"))
        for summary in data.get("days", [])[:5]:
            assert "hours" in summary, summary

    def test_timesheet_pdf_renders(self, http_client):
        resp = http_client.get("/worklog/api/timesheet.pdf?days=7", timeout=120)
        assert resp.status_code == 200, resp.text[:200]
        assert resp.content[:5] == b"%PDF-", "expected a PDF body"

    def test_years_endpoint_grounds_the_range_picker(self, http_client):
        """Only years that hold data — offering a gap year exports an empty PDF."""
        data = assert_dict_response(http_client.get("/worklog/api/years"),
                                    required_keys=["years"])
        for row in data["years"]:
            assert {"year", "days", "hours"} <= set(row), row
            assert row["days"] > 0, "a year with no days must not be offered"
        got = [r["year"] for r in data["years"]]
        assert got == sorted(got, reverse=True), "newest first"

    def test_timesheet_pdf_accepts_an_explicit_range(self, http_client):
        resp = http_client.get(
            "/worklog/api/timesheet.pdf?from=2024-01-01&to=2024-12-31", timeout=180)
        assert resp.status_code == 200, resp.text[:200]
        assert resp.content[:5] == b"%PDF-"
        # Named by the RANGE, not the generation date: without an explicit
        # filename the browser names the download from the URL path, so every
        # export would save as "timesheet.pdf" and overwrite the previous one.
        disposition = resp.headers.get("content-disposition", "")
        assert "timesheet-2024-01-01-to-2024-12-31.pdf" in disposition, disposition

    def test_timesheet_header_names_employers_from_the_window(self, http_client):
        """Not the configured default: an unfiltered FY2024 export was headed
        with the CURRENT employer while every day in it belonged to a previous
        one. On a document handed to an employer that is worse than vague."""
        import io

        pypdf = pytest.importorskip("pypdf")
        resp = http_client.get(
            "/worklog/api/timesheet.pdf?from=2024-01-01&to=2024-12-31", timeout=240)
        assert resp.status_code == 200
        page = pypdf.PdfReader(io.BytesIO(resp.content)).pages[0].extract_text()
        header = next((ln for ln in page.splitlines() if "→" in ln), "")
        assert header, page[:200]
        emp = assert_dict_response(http_client.get("/worklog/api/employers"))
        present = {e["name"] for e in emp.get("employers", [])}
        named = header.split("·")[0].strip()
        # A real employer from the vault, a "<n> employers" count, or the
        # neutral fallback — never the configured default just for being default.
        assert (named in present or named == "All employers"
                or named.endswith("employers") or "," in named), header
        default = (emp.get("default") or "").strip()
        if default and default not in present:
            assert named != default, f"headed with the default employer: {header}"

    def test_timesheet_pdf_rejects_a_malformed_bound(self, http_client):
        """Must not fall through to the trailing window — that would silently
        export a different period than the one asked for."""
        data = assert_dict_response(
            http_client.get("/worklog/api/timesheet.pdf?from=not-a-date"))
        assert data.get("error"), data

    def test_portable_export_shape(self, http_client):
        data = assert_dict_response(http_client.get('/worklog/api/portable'))
        assert data.get('format') == 'emptyos.worklog'
        assert data.get('version') == 1
        assert isinstance(data.get('days'), list)

    def test_portable_import_round_trip_is_idempotent(self, http_client):
        day_s = WORKLOG_TEST_DATE
        text = TEST_PREFIX + 'portable import round trip ' + uuid4().hex
        doc = {
            'format': 'emptyos.worklog',
            'version': 1,
            'days': [{
                'date': day_s, 'employer': '', 'plan': '', 'update': '',
                'projects': [{'project': TEST_PREFIX + 'Portable', 'items': [{
                    'text': text, 'status': 'review',
                }]}],
                'timesheet': [], 'notes': [],
            }],
        }
        first = assert_dict_response(http_client.post(
            '/worklog/api/import', json={'document': doc},
        ))
        assert first.get('items_added') == 1, first
        second = assert_dict_response(http_client.post(
            '/worklog/api/import', json={'document': doc},
        ))
        assert second.get('items_added') == 0, second
        assert second.get('days_unchanged') == 1, second
        day = assert_dict_response(http_client.get(f'/worklog/api/day?date={day_s}'))
        matches = [it for g in day['projects'] for it in g['items'] if it['text'] == text]
        assert len(matches) == 1 and matches[0]['status'] == 'review'

    def test_portable_import_preview_writes_nothing(self, http_client):
        """Preview reports the same effects but must not touch the vault."""
        day_s = WORKLOG_TEST_DATE
        text = TEST_PREFIX + 'preview only ' + uuid4().hex
        doc = {
            'format': 'emptyos.worklog', 'version': 1,
            'days': [{
                'date': day_s, 'employer': '', 'plan': '', 'update': '',
                'projects': [{'project': TEST_PREFIX + 'Preview', 'items': [{
                    'text': text, 'status': 'todo',
                }]}],
                'timesheet': [], 'notes': [],
            }],
        }
        preview = assert_dict_response(http_client.post(
            '/worklog/api/import/preview', json={'document': doc},
        ))
        assert preview.get('preview') is True, preview
        assert preview.get('items_added') == 1, preview
        assert [c['date'] for c in preview.get('changes', [])] == [day_s], preview

        day = assert_dict_response(http_client.get(f'/worklog/api/day?date={day_s}'))
        present = [it for g in day['projects'] for it in g['items'] if it['text'] == text]
        assert present == [], 'preview must not write to the vault'

        # Previewing twice is still a preview — no accidental first-write.
        again = assert_dict_response(http_client.post(
            '/worklog/api/import/preview', json={'document': doc},
        ))
        assert again.get('items_added') == 1, again

    def test_portable_import_appends_notes_instead_of_replacing(self, http_client):
        """A second import must add to ## Notes / ## Timesheet, not re-render them."""
        day_s = WORKLOG_TEST_DATE
        first, second = (TEST_PREFIX + 'note ' + uuid4().hex for _ in range(2))
        # Unique per run: appended rows accumulate in the sentinel day, so a
        # fixed project label would see 4 rows on the second run in a session
        # where cleanup hasn't fired yet.
        project = TEST_PREFIX + 'Append ' + uuid4().hex[:8]

        def _doc(note, hours):
            return {'format': 'emptyos.worklog', 'version': 1, 'days': [{
                'date': day_s, 'employer': '', 'plan': '', 'update': '',
                'projects': [], 'notes': [note],
                'timesheet': [{'project': project, 'hours': hours, 'note': ''}],
            }]}

        assert_dict_response(http_client.post('/worklog/api/import', json={'document': _doc(first, 1.0)}))
        assert_dict_response(http_client.post('/worklog/api/import', json={'document': _doc(second, 2.0)}))

        day = assert_dict_response(http_client.get(f'/worklog/api/day?date={day_s}'))
        notes = day.get('notes') or []
        assert first in notes and second in notes, notes
        hours = [t['hours'] for t in day.get('timesheet') or []
                 if t['project'] == project]
        assert sorted(hours) == [1.0, 2.0], day.get('timesheet')

    def test_portable_import_rejects_unknown_format(self, http_client):
        data = assert_dict_response(http_client.post(
            '/worklog/api/import',
            json={'document': {'format': 'not-worklog', 'version': 1, 'days': []}},
        ))
        assert 'expected format' in data.get('error', '')
        non_object = assert_dict_response(http_client.post('/worklog/api/import', json=[]))
        assert 'JSON object' in non_object.get('error', '')

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
        # Sentinel date is load-bearing: /api/plan REPLACES the section, so
        # posting without a date would overwrite whatever the user planned today.
        res = assert_dict_response(http_client.post("/worklog/api/plan",
                                   json={"date": WORKLOG_TEST_DATE,
                                         "text": TEST_PREFIX + "plan the work"}))
        assert res.get("ok")
        day = assert_dict_response(
            http_client.get(f"/worklog/api/day?date={WORKLOG_TEST_DATE}"))
        assert TEST_PREFIX + "plan the work" in day.get("plan", "")

    def test_update_save(self, http_client):
        res = assert_dict_response(http_client.post("/worklog/api/update",
                                   json={"date": WORKLOG_TEST_DATE,
                                         "text": TEST_PREFIX + "end of day note"}))
        assert res.get("ok")
        day = assert_dict_response(
            http_client.get(f"/worklog/api/day?date={WORKLOG_TEST_DATE}"))
        assert TEST_PREFIX + "end of day note" in day.get("update", "")

    def test_update_draft_proposes_without_writing(self, http_client):
        """Draft returns prose for review and must leave ## Update untouched."""
        proj = TEST_PREFIX + "DraftProj"
        assert_ok(http_client.post("/worklog/api/log", json={
            "date": WORKLOG_TEST_DATE, "project": proj,
            "text": TEST_PREFIX + "drafted work item", "status": "complete"}))
        before = assert_dict_response(
            http_client.get(f"/worklog/api/day?date={WORKLOG_TEST_DATE}"))
        data = assert_dict_response(
            http_client.post("/worklog/api/update/draft",
                             json={"date": WORKLOG_TEST_DATE}), required_keys=["draft"])
        assert data["draft"].strip(), data
        assert data.get("items", 0) >= 1, data
        after = assert_dict_response(
            http_client.get(f"/worklog/api/day?date={WORKLOG_TEST_DATE}"))
        assert after.get("update", "") == before.get("update", ""),             "draft must not write to the Update section"

    def test_update_draft_requires_work_items(self, http_client):
        """A day with no logged items has nothing to summarise — no LLM call."""
        data = assert_dict_response(http_client.post(
            "/worklog/api/update/draft", json={"date": "1990-01-09"}))
        assert data.get("error"), data

    def test_day_has_update_field(self, http_client):
        day = assert_dict_response(http_client.get("/worklog/api/day"))
        assert "update" in day

    def test_log_then_status_then_readback(self, http_client):
        # End-to-end the companion's page actions exercise: log → flip → read.
        text = TEST_PREFIX + "companion flow item"
        proj = TEST_PREFIX + "CompanionProj"
        assert_ok(http_client.post("/worklog/api/log",
                  json={"date": WORKLOG_TEST_DATE, "project": proj,
                        "text": text, "status": "in-progress"}))
        assert_ok(http_client.post("/worklog/api/status",
                  json={"date": WORKLOG_TEST_DATE, "project": proj,
                        "item": text, "status": "complete"}))
        day = assert_dict_response(http_client.get(f"/worklog/api/day?date={WORKLOG_TEST_DATE}"))
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

    def test_data_transfer_control_present(self, app_page, page_errors):
        page = app_page('worklog')
        assert page.locator("button:has-text('Data')").count() == 1
        page.click("button:has-text('Data')")
        assert page.locator("button:has-text('Export JSON')").count() == 1
        assert page.locator("button:has-text('Import JSON')").count() == 1
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
        # The form prefills #wl-date with today — retarget so the submit lands
        # on the sentinel day rather than the user's real note.
        page.fill("#wl-date", WORKLOG_TEST_DATE)
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
        """EOS_UI.hashRoute detail flow: log → deep-link the day → back.

        Opens the day by hash rather than by clicking ``.day-card``: writes go
        to the sentinel day, which sits outside the timeline's 120-day window,
        and clicking whatever card happens to be first would make the test
        depend on pre-existing vault data (CI boots an empty vault).
        """
        item = TEST_PREFIX + "nav item"
        page = app_page("worklog")
        page.fill("#wl-proj", TEST_PREFIX + "NavProj")
        page.fill("#wl-add-text", item)
        page.fill("#wl-date", WORKLOG_TEST_DATE)
        page.click("button:has-text('Log')")
        wait_briefly(page)

        page.goto(page.url.split("#")[0] + "#" + WORKLOG_TEST_DATE)
        wait_briefly(page)
        assert page.locator("#view-detail:not(.hidden)").count() == 1
        body = page.locator("#detail-body").inner_text()
        assert item in body, f"logged item missing from detail: {body[:200]}"
        assert WORKLOG_TEST_DATE in page.url  # hash-route deep link set

        page.click("button:has-text('Back')")
        wait_briefly(page)
        assert page.locator("#view-detail.hidden").count() == 1
        assert_no_js_errors(page_errors)
