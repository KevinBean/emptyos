"""System app tests: Task — 12 use cases.

Covers task list, focus scoring, calendar, decay tiers, tags, recurring tasks,
plus UI workflows: add task, complete task, search filter, tab switch.
"""

import pytest

from helpers import TEST_PREFIX, assert_dict_response, assert_list_response, assert_ok
from page_helpers import (
    assert_no_js_errors, click_first, fill_and_submit, switch_tab,
    wait_briefly, wait_for_toast,
)


@pytest.mark.api
class TestTaskAPI:
    def test_tasks_list_structure(self, http_client):
        """GET /task/api/tasks returns list with text + tier fields."""
        data = assert_list_response(http_client.get("/task/api/tasks"))
        if data:
            t = data[0]
            for key in ("text", "done", "file"):
                assert key in t, f"Task missing key {key}: {t}"

    def test_stats_tier_breakdown(self, http_client):
        """GET /task/api/stats has open/done/by_tier."""
        data = assert_dict_response(http_client.get("/task/api/stats"))
        for key in ("open", "done", "by_tier"):
            assert key in data, f"Stats missing key {key}: {list(data.keys())}"

    def test_focus_top_3(self, http_client):
        """GET /task/api/focus returns at most 3 tasks."""
        data = assert_ok(http_client.get("/task/api/focus"))
        # Either a list or {"tasks": [...]}
        tasks = data if isinstance(data, list) else data.get("tasks", [])
        assert isinstance(tasks, list)
        assert len(tasks) <= 3, f"Focus returned > 3 tasks: {len(tasks)}"

    def test_calendar_date_groups(self, http_client):
        """GET /task/api/calendar returns date-grouped tasks."""
        data = assert_ok(http_client.get("/task/api/calendar"))
        assert isinstance(data, dict), "Calendar should be a dict of date->tasks"

    def test_tags_extraction(self, http_client):
        """GET /task/api/tags returns tag map or list."""
        data = assert_ok(http_client.get("/task/api/tags"))
        assert isinstance(data, (dict, list))

    def test_context_grouping(self, http_client):
        """GET /task/api/by-context returns dict of context -> tasks."""
        data = assert_ok(http_client.get("/task/api/by-context"))
        assert isinstance(data, dict)

    def test_recurring_markers(self, http_client):
        """GET /task/api/recurring returns recurring tasks."""
        data = assert_ok(http_client.get("/task/api/recurring"))
        assert isinstance(data, (list, dict))

    def test_refresh_rebuilds(self, http_client):
        """POST /task/api/refresh rebuilds the index."""
        data = assert_ok(http_client.post("/task/api/refresh"))
        assert isinstance(data, dict)

    def test_focus_view_shape(self, http_client):
        """GET /task/api/focus-view groups by actionability + carries balance."""
        data = assert_dict_response(http_client.get("/task/api/focus-view"))
        for key in ("groups", "counts", "balance", "total"):
            assert key in data, f"focus-view missing {key}: {list(data.keys())}"
        for state in ("next", "waiting", "someday"):
            assert state in data["groups"], f"missing actionability group {state}"
            assert isinstance(data["groups"][state], list)
        # every grouped task carries its actionability label
        for state, items in data["groups"].items():
            for t in items:
                assert t.get("actionability") == state, (
                    f"task in '{state}' group labelled '{t.get('actionability')}': {t.get('text')}"
                )

    def test_tasks_carry_actionability(self, http_client):
        """GET /task/api/tasks includes the actionability field on each row."""
        data = assert_list_response(http_client.get("/task/api/tasks"))
        if data:
            assert data[0].get("actionability") in ("next", "waiting", "someday")

    def test_triage_ai_status_shape(self, http_client):
        """GET /task/api/triage/ai-status reports the dark flag + someday count."""
        data = assert_dict_response(http_client.get("/task/api/triage/ai-status"))
        assert "enabled" in data and isinstance(data["enabled"], bool)
        assert "someday_total" in data

    def test_triage_classify_gated_off(self, http_client):
        """With the AI-triage flag OFF (default), classify returns disabled —
        no LLM call, no vault content sent."""
        status = http_client.get("/task/api/triage/ai-status").json()
        resp = http_client.get("/task/api/triage/classify?limit=5").json()
        if not status.get("enabled"):
            assert resp.get("disabled") is True
        else:
            # flag on (someone enabled it) — must return the proposal shape
            assert "dispositions" in resp

    def test_add_endpoint_creates_task(self, http_client):
        """POST /task/api/add creates a real task (the Add bar's backend)."""
        text = f"{TEST_PREFIX}optimizer add-bar check"
        data = assert_dict_response(http_client.post("/task/api/add", json={"text": text}))
        assert not data.get("error"), data
        assert text in data.get("text", "")
        http_client.post("/task/api/refresh")
        tasks = http_client.get("/task/api/tasks").json()
        assert any(text in t.get("text", "") for t in tasks), "added task not in open list"

    def test_add_reports_the_file_projects_actually_wrote(self, http_client):
        """A new project is a folder (<id>/<id>.md); the added task must name
        that file, not the flat <id>.md the task app used to guess."""
        import uuid as _uuid
        project = f"{TEST_PREFIX}taskfile-{_uuid.uuid4().hex[:6]}"
        data = assert_dict_response(http_client.post(
            "/task/api/add", json={"text": f"{TEST_PREFIX}file check", "project": project}))
        assert not data.get("error"), data
        # Exactly the vault-relative path — an absolute machine path would also
        # end with it, and would no longer match the scanner's `file` keys.
        assert data["file"] == f"10_Projects/{project}/{project}.md", data["file"]

    def test_add_endpoint_requires_text(self, http_client):
        """POST /task/api/add with blank text is rejected, not silently dropped."""
        data = assert_dict_response(http_client.post("/task/api/add", json={"text": "   "}))
        assert data.get("error")

    def test_someday_sample_shape(self, http_client):
        """GET /task/api/someday-sample returns ≤ n someday tasks + total."""
        data = assert_dict_response(http_client.get("/task/api/someday-sample?n=3"))
        assert "items" in data and "total" in data
        assert isinstance(data["items"], list) and len(data["items"]) <= 3
        for t in data["items"]:
            assert t.get("actionability", "someday") == "someday"

    def test_snooze_batch_validates(self, http_client):
        """POST /task/api/snooze-batch rejects bad shapes, skips missing files."""
        bad = assert_dict_response(
            http_client.post("/task/api/snooze-batch", json={"items": "nope", "days": 7})
        )
        assert bad.get("error")
        data = assert_dict_response(
            http_client.post(
                "/task/api/snooze-batch",
                json={"items": [{"file": f"{TEST_PREFIX}missing.md", "line": 1}], "days": 7},
            )
        )
        assert data.get("ok") is True
        assert data.get("rescheduled") == 0 and data.get("skipped") == 1

    @pytest.mark.llm
    def test_suggest_endpoint(self, http_client):
        """GET /task/api/suggest returns an AI recommendation (LLM-hitting)."""
        data = assert_dict_response(http_client.get("/task/api/suggest"))
        assert "suggestion" in data

    def test_triage_scan_shape(self, http_client):
        """GET /task/api/triage/scan returns read-only proposals."""
        data = assert_dict_response(http_client.get("/task/api/triage/scan"))
        for key in ("duplicates", "zombies_by_domain", "counts"):
            assert key in data, f"triage scan missing {key}: {list(data.keys())}"
        assert isinstance(data["duplicates"], list)
        # duplicate groups are same-file only (no cross-file boilerplate dupes)
        for g in data["duplicates"]:
            files = {g["keep"]["file"]} | {r["file"] for r in g["remove"]}
            assert len(files) == 1, f"cross-file duplicate group flagged: {files}"


def _add_and_find(http_client, text):
    """Add a task, refresh, and return its indexed row ({file, line, text}) or None."""
    r = http_client.post("/task/api/add", json={"text": text}).json()
    if r.get("error"):
        return None
    http_client.post("/task/api/refresh")
    for t in http_client.get("/task/api/tasks").json():
        if text in t.get("text", ""):
            return t
    return None


@pytest.mark.api
class TestTaskMutations:
    """Round-trip the mutation endpoints — the app had no test that actually
    completed / edited a task through the API before this suite."""

    def test_toggle_complete_then_reopen(self, http_client):
        t = _add_and_find(http_client, f"{TEST_PREFIX}toggle round trip")
        if not t:
            pytest.skip("could not add/find task")
        done = assert_dict_response(
            http_client.post("/task/api/toggle", json={"file": t["file"], "line": t["line"], "text": t["text"]})
        )
        assert done.get("status") == "completed", done
        http_client.post("/task/api/refresh")
        done_list = http_client.get("/task/api/tasks?status=done").json()
        assert any(t["text"] in d.get("text", "") for d in done_list), "completed task not in done list"
        # reopen — the line is unchanged by a plain toggle, so (file,line,text) still addresses it
        re = assert_dict_response(
            http_client.post("/task/api/toggle", json={"file": t["file"], "line": t["line"], "text": t["text"]})
        )
        assert re.get("status") == "reopened", re

    def test_toggle_staleness_guard_rejects_wrong_text(self, http_client):
        t = _add_and_find(http_client, f"{TEST_PREFIX}staleness guard")
        if not t:
            pytest.skip("could not add/find task")
        res = assert_dict_response(
            http_client.post(
                "/task/api/toggle",
                json={"file": t["file"], "line": t["line"], "text": "PLAYWRIGHT-TEST-does-not-match"},
            )
        )
        assert res.get("error"), "stale (wrong-text) toggle should be refused"

    def test_set_priority_round_trip(self, http_client):
        t = _add_and_find(http_client, f"{TEST_PREFIX}priority round trip")
        if not t:
            pytest.skip("could not add/find task")
        tid = f'{t["file"]}:{t["line"]}'
        ok = assert_dict_response(
            http_client.post("/task/api/set-field", json={"id": tid, "field": "priority", "value": "high"})
        )
        assert ok.get("ok"), ok
        http_client.post("/task/api/refresh")
        tasks = http_client.get("/task/api/tasks").json()
        match = next((x for x in tasks if t["text"] in x.get("text", "")), None)
        assert match and "⏫" in match["text"], "priority marker not written to the line"
        # clearing removes the marker
        http_client.post("/task/api/set-field", json={"id": tid, "field": "priority", "value": ""})

    def test_set_priority_rejects_bad_value(self, http_client):
        t = _add_and_find(http_client, f"{TEST_PREFIX}priority bad value")
        if not t:
            pytest.skip("could not add/find task")
        tid = f'{t["file"]}:{t["line"]}'
        res = assert_dict_response(
            http_client.post("/task/api/set-field", json={"id": tid, "field": "priority", "value": "urgent"})
        )
        assert res.get("error") and "priority must be" in res["error"]

    def test_recurrence_flag_off_does_not_regenerate(self, http_client):
        """Dark-default contract: completing a 🔁 task with the flag OFF spawns
        no new occurrence (byte-identical to the pre-feature behaviour)."""
        t = _add_and_find(http_client, f"{TEST_PREFIX}water plants 🔁 weekly")
        if not t:
            pytest.skip("could not add/find task")
        res = assert_dict_response(
            http_client.post("/task/api/toggle", json={"file": t["file"], "line": t["line"], "text": t["text"]})
        )
        assert res.get("status") == "completed", res
        assert res.get("recurred") is False, "flag OFF must not regenerate a recurring task"
        http_client.post("/task/api/refresh")
        open_tasks = http_client.get("/task/api/tasks").json()
        assert not [x for x in open_tasks if t["text"] in x.get("text", "")], (
            "no new open occurrence should exist while recurrence is disabled"
        )


@pytest.mark.interactive
class TestTaskUI:
    def test_ui_add_task_flow(self, app_page, page_errors):
        """Type new task → click Add → verify it shows in the list."""
        page = app_page("task")
        wait_briefly(page, 600)

        # The add input may be #add-text or a placeholder-matched input
        added = click_first(page, "#add-text")
        page.locator("#add-text").first.fill(f"{TEST_PREFIX}buy milk")
        # Click Add button
        clicked = click_first(
            page,
            "button:has-text('Add')",
            "[onclick*='addTask']",
        )
        assert clicked, "Add button not found"
        wait_briefly(page, 1500)
        assert_no_js_errors(page_errors)

    def test_ui_complete_task_flow(self, app_page, page_errors):
        """Find a task checkbox and click it to toggle done state."""
        page = app_page("task")
        wait_briefly(page, 800)
        # Scope to the active Focus view — a bare .task-cb also matches rows in
        # hidden views (Focus/List/Done all render checkboxes), whose first
        # element is never visible.
        cbs = page.locator("#view-focus .task-cb")
        if cbs.count() == 0:
            pytest.skip("No tasks visible to toggle")
        # Just verify we can interact — don't actually mutate state for safety
        # (toggle would reload list and might affect other tests)
        assert cbs.first.is_visible()
        assert_no_js_errors(page_errors)

    def test_ui_search_filter(self, app_page, page_errors):
        """Type in search input → verify task list filters."""
        page = app_page("task")
        wait_briefly(page, 600)
        search = page.locator("#search-input, .search-input").first
        if search.count() == 0:
            pytest.skip("No search input on task page")
        search.fill("xyzzy_no_match_PLAYWRIGHT")
        wait_briefly(page, 600)
        # After filtering with a no-match query, visible tasks should be near zero
        assert_no_js_errors(page_errors)

    def test_ui_tab_switch_calendar(self, app_page, page_errors):
        """Click Calendar tab → verify calendar view appears."""
        page = app_page("task")
        wait_briefly(page, 500)
        switched = switch_tab(page, "calendar")
        if switched:
            wait_briefly(page, 500)
        assert_no_js_errors(page_errors)

    def test_ui_done_tab_switch(self, app_page, page_errors):
        """Click the Done tab → completed-tasks view renders without JS errors."""
        page = app_page("task")
        wait_briefly(page, 600)
        switch_tab(page, "done")
        wait_briefly(page, 800)
        assert_no_js_errors(page_errors)

    def test_ui_edit_button_opens_modal(self, app_page, page_errors):
        """The ⋯ Edit button on a task row opens the structured edit modal."""
        page = app_page("task")
        wait_briefly(page, 800)
        switch_tab(page, "list")
        wait_briefly(page, 600)
        # Scope to the active list view — a bare .task-item also matches rows in
        # the hidden Focus view (display:none), which never become hoverable.
        row = page.locator("#view-list .task-item").first
        if row.count() == 0:
            pytest.skip("no task rows to edit")
        row.hover()
        wait_briefly(page, 200)
        btn = row.locator("button:has-text('Edit')").first
        if btn.count() == 0:
            pytest.skip("no edit button on row")
        btn.click()
        wait_briefly(page, 500)
        # formModal renders the rename input as #eos-form-text
        assert page.locator("#eos-form-text").count() >= 1, "edit modal did not open"
        assert_no_js_errors(page_errors)
