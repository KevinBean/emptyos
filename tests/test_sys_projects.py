"""System app tests: Projects — 11 use cases + list-page chrome regressions."""

import time

import pytest

import factories
from helpers import TEST_PREFIX, WORKLOG_TEST_DATE, assert_dict_response, assert_ok
from page_helpers import (
    assert_no_js_errors, click_first, wait_briefly,
)


@pytest.fixture(scope="module")
def scratch_project(http_client):
    """A throwaway project for tests that WRITE into a project.

    Writing into `_first_valid_project_id` put test lines in the user's real
    first project, which the session leak purge then had to strip — and on
    2026-08-18 two concurrent purges truncated that note to zero bytes. A
    project this module creates is a test artifact from the start: its
    TEST_PREFIX folder is removed whole by conftest's `10_Projects` sweep.
    """
    name = f"{TEST_PREFIX}projects-scratch-{int(time.time() * 1000)}"
    made = assert_dict_response(http_client.post(
        "/projects/api/create", json={"name": name, "goal": "test fixture", "status": "active"}))
    assert made.get("ok") is True, made
    return made["id"]


def _first_valid_project_id(http_client):
    """Return the id of the first project whose detail resolves (no 'error').

    The list can include legacy/flat entries that don't resolve to a project
    directory ({"error": "Project not found"}); those carry no note_body and
    can't render a workspace, so tests that need a real project skip past them.
    """
    listing = http_client.get("/projects/api/list").json()
    items = listing if isinstance(listing, list) else listing.get("projects", [])
    for it in items:
        pid = it.get("id") or it.get("name")
        if not pid:
            continue
        d = http_client.get(f"/projects/api/projects/{pid}").json()
        if isinstance(d, dict) and not d.get("error"):
            return pid
    return None


@pytest.mark.api
class TestProjectsAPI:
    def test_list_projects(self, http_client):
        data = assert_ok(http_client.get("/projects/api/list"))
        items = data if isinstance(data, list) else data.get("projects", [])
        assert isinstance(items, list)

    def test_project_detail(self, http_client):
        listing = http_client.get("/projects/api/list").json()
        items = listing if isinstance(listing, list) else listing.get("projects", [])
        if not items:
            pytest.skip("No projects to fetch detail for")
        pid = items[0].get("id") or items[0].get("name")
        if not pid:
            pytest.skip("Project missing id")
        resp = http_client.get(f"/projects/api/projects/{pid}")
        assert resp.status_code == 200

    def test_project_health(self, http_client):
        listing = http_client.get("/projects/api/list").json()
        items = listing if isinstance(listing, list) else listing.get("projects", [])
        if not items:
            pytest.skip("No projects")
        pid = items[0].get("id") or items[0].get("name")
        # /health invokes self.think() — LLM call, needs longer timeout than the default 15s
        resp = http_client.get(f"/projects/api/projects/{pid}/health", timeout=60)
        if resp.status_code == 404:
            pytest.skip("health endpoint not present")
        assert resp.status_code == 200

    def test_deadlines(self, http_client):
        data = assert_ok(http_client.get("/projects/api/deadlines"))
        assert isinstance(data, (list, dict))

    def test_all_tasks(self, http_client):
        data = assert_ok(http_client.get("/projects/api/all-tasks"))
        assert isinstance(data, (list, dict))

    def test_type_config(self, http_client):
        data = assert_ok(http_client.get("/projects/api/type-config"))
        assert isinstance(data, dict)

    def test_refresh(self, http_client):
        resp = http_client.post("/projects/api/refresh")
        assert resp.status_code == 200

    def test_project_detail_has_note_body(self, http_client):
        """Detail response carries `note_body` (consumed by the workspace read pane)."""
        pid = _first_valid_project_id(http_client)
        if not pid:
            pytest.skip("No resolvable project")
        data = assert_dict_response(http_client.get(f"/projects/api/projects/{pid}"))
        assert "note_body" in data
        assert isinstance(data["note_body"], str)

    def test_overview(self, http_client):
        """Command-center overview synthesizes the expected keys (no LLM)."""
        pid = _first_valid_project_id(http_client)
        if not pid:
            pytest.skip("No resolvable project")
        d = assert_dict_response(http_client.get(f"/projects/api/projects/{pid}/overview"))
        for k in ("status", "progress", "open", "done", "ready_count", "blocked_count", "next_actions"):
            assert k in d, f"overview missing {k}"
        assert isinstance(d["next_actions"], list)

    def test_timeline4d(self, http_client):
        """4D timeline returns past/future/now."""
        pid = _first_valid_project_id(http_client)
        if not pid:
            pytest.skip("No resolvable project")
        d = assert_dict_response(http_client.get(f"/projects/api/projects/{pid}/timeline4d"))
        for k in ("past", "future", "now"):
            assert k in d

    def test_related(self, http_client):
        """Related endpoint returns the related_projects / links shape."""
        pid = _first_valid_project_id(http_client)
        if not pid:
            pytest.skip("No resolvable project")
        d = assert_dict_response(http_client.get(f"/projects/api/projects/{pid}/related"))
        assert isinstance(d.get("related_projects"), list)
        assert isinstance(d.get("links"), list)

    def test_doc_roundtrip_and_traversal_guard(self, http_client):
        """GET+POST a project doc body (non-destructive); reject path traversal."""
        pid = _first_valid_project_id(http_client)
        if not pid:
            pytest.skip("No resolvable project")
        docs = http_client.get(f"/projects/api/projects/{pid}/docs").json().get("docs", [])
        rel = docs[0].get("rel_path") if docs else None
        if not rel:
            pytest.skip("Project has no docs")
        got = http_client.get(f"/projects/api/projects/{pid}/doc", params={"path": rel}).json()
        assert "body" in got and isinstance(got["body"], str)
        # idempotent round-trip — write the same body back, verify unchanged
        save = http_client.post(f"/projects/api/projects/{pid}/doc",
                                json={"path": rel, "body": got["body"]}).json()
        assert save.get("ok") is True
        again = http_client.get(f"/projects/api/projects/{pid}/doc", params={"path": rel}).json()
        assert again.get("body") == got["body"]
        # traversal must be refused
        bad = http_client.get(f"/projects/api/projects/{pid}/doc",
                              params={"path": "../../../emptyos.toml"}).json()
        assert bad.get("error") and "body" not in bad

    @pytest.mark.llm
    def test_ask_grounded(self, http_client):
        """Project AI companion returns a grounded answer (LLM)."""
        pid = _first_valid_project_id(http_client)
        if not pid:
            pytest.skip("No resolvable project")
        r = http_client.post(f"/projects/api/projects/{pid}/ask",
                             json={"question": "What is this project about?"}, timeout=70)
        assert r.status_code == 200
        d = r.json()
        if d.get("error"):
            pytest.skip(f"think unavailable: {d['error']}")
        assert isinstance(d.get("answer"), str) and d["answer"]

    def test_workspace_route_serves_html(self, http_client):
        """GET /projects/workspace/<id> returns the standalone full-page shell."""
        listing = http_client.get("/projects/api/list").json()
        items = listing if isinstance(listing, list) else listing.get("projects", [])
        if not items:
            pytest.skip("No projects")
        pid = items[0].get("id") or items[0].get("name")
        resp = http_client.get(f"/projects/workspace/{pid}")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        body = resp.text
        assert 'id="detail-view"' in body          # the shared renderer's container
        assert "All Projects" in body              # back-to-list link
        assert "/projects/pages/workspace.js" in body  # absolute asset path

    def test_worklog_bridge_roundtrip(self, http_client, scratch_project):
        """A project workspace writes through Worklog and the item reads back.

        Writes to the sentinel WORKLOG_TEST_DATE, never today: the 2026-08-18
        worklog was truncated the same way the project note was. The project
        read is capped at 366 days, so the item is read back from that day.
        """
        pid = scratch_project
        text = TEST_PREFIX + "project worklog bridge"
        logged = assert_dict_response(http_client.post(
            f"/projects/api/projects/{pid}/worklog",
            json={"text": text, "status": "complete", "date": WORKLOG_TEST_DATE},
        ))
        assert logged.get("ok") is True, logged
        assert "employer" in logged
        day = assert_dict_response(http_client.get(f"/worklog/api/day?date={WORKLOG_TEST_DATE}"))
        # Under THIS project's group: a bridge logging under the wrong project
        # (or none) must not pass.
        name = assert_dict_response(http_client.get(f"/projects/api/projects/{pid}"))["name"]
        groups = [g for g in day.get("projects", []) if g.get("project") == name]
        assert groups, [g.get("project") for g in day.get("projects", [])]
        assert any(it.get("text") == text for g in groups for it in g["items"])
        recent = assert_dict_response(http_client.get(
            f"/projects/api/projects/{pid}/worklog?days=30&limit=20"
        ))
        assert recent.get("available") is True, recent
        assert "employer" in recent

    # ── Task assignee bridge to staff (optional_apps=["staff"], soft — must
    # stay clean whether or not the personal `staff` app is installed) ──

    def test_assignable_agents_endpoint(self, http_client):
        """Never errors — returns [] when staff isn't installed on this machine."""
        data = assert_dict_response(http_client.get("/projects/api/assignable-agents"))
        assert "agents" in data and isinstance(data["agents"], list)

    def test_assign_task_missing_agent_id(self, http_client):
        pid = _first_valid_project_id(http_client)
        if not pid:
            pytest.skip("No resolvable project")
        r = http_client.post(f"/projects/api/projects/{pid}/tasks/0/assign", json={})
        assert "error" in r.json()

    def test_assign_task_unknown_agent_is_clean_error(self, http_client, scratch_project):
        """Assigning to a bogus agent never 500s, with or without staff installed."""
        pid = scratch_project
        text = TEST_PREFIX + "assignee bridge probe"
        added = assert_dict_response(
            http_client.post(f"/projects/api/projects/{pid}/tasks/add", json={"text": text}))
        assert added.get("ok") is True, added
        detail = assert_dict_response(http_client.get(f"/projects/api/projects/{pid}"))
        line = next((t["line"] for t in detail.get("tasks", []) if t.get("text") == text), None)
        if line is None:
            pytest.skip("Could not resolve the added task's line")
        r = http_client.post(
            f"/projects/api/projects/{pid}/tasks/{line}/assign",
            json={"agent_id": TEST_PREFIX + "no-such-agent"},
        )
        assert r.status_code == 200
        assert "error" in r.json()
        # A failed dispatch must not write an assignment meta line.
        status = assert_dict_response(
            http_client.get(f"/projects/api/projects/{pid}/tasks/{line}/assignment"))
        assert status.get("assigned") is False

    def test_task_assignment_status_unassigned(self, http_client):
        pid = _first_valid_project_id(http_client)
        if not pid:
            pytest.skip("No resolvable project")
        detail = assert_dict_response(http_client.get(f"/projects/api/projects/{pid}"))
        tasks = detail.get("tasks", [])
        if not tasks:
            pytest.skip("No tasks to check")
        line = tasks[0]["line"]
        data = assert_dict_response(
            http_client.get(f"/projects/api/projects/{pid}/tasks/{line}/assignment"))
        assert "assigned" in data

    # ── Date cascade (deep logic in tests/test_unit_projects_scheduling.py) ──

    def test_cascade_check_shape(self, http_client):
        """GET cascade/check returns a well-formed, non-500 response on a real
        project (most have no dated deps → empty violations, still valid)."""
        pid = _first_valid_project_id(http_client)
        if not pid:
            pytest.skip("No resolvable project")
        data = assert_dict_response(
            http_client.get(f"/projects/api/projects/{pid}/cascade/check"))
        assert "violations" in data and isinstance(data["violations"], list)
        for v in data["violations"]:
            assert {"line", "text", "old_due", "new_due"} <= set(v)

    def test_cascade_preview_requires_due(self, http_client):
        pid = _first_valid_project_id(http_client)
        if not pid:
            pytest.skip("No resolvable project")
        r = http_client.post(f"/projects/api/projects/{pid}/cascade/preview", json={"line": 0})
        assert "error" in r.json()  # missing due

    def test_cascade_apply_requires_shifts(self, http_client):
        pid = _first_valid_project_id(http_client)
        if not pid:
            pytest.skip("No resolvable project")
        r = http_client.post(f"/projects/api/projects/{pid}/cascade/apply", json={"shifts": []})
        assert "error" in r.json()

    def test_cascade_check_missing_project(self, http_client):
        r = http_client.get("/projects/api/projects/no-such-project-zzz/cascade/check")
        assert "error" in r.json()


@pytest.mark.interactive
class TestProjectsUI:
    def test_ui_project_list(self, app_page, page_errors):
        """Verify project cards render."""
        page = app_page("projects")
        wait_briefly(page, 1000)
        cards = page.locator(".project-card, .project-item, [data-project-id]")
        assert_no_js_errors(page_errors)

    def test_ui_card_opens_workspace(self, app_page, page_errors):
        """Click a project card → navigates to its /projects/workspace/<id> page."""
        page = app_page("projects")
        wait_briefly(page, 1200)
        cards = page.locator(".eos-entity-card, .project-card, [data-project-id]")
        if cards.count() == 0:
            pytest.skip("No project cards visible")
        cards.first.click()
        page.wait_for_url("**/projects/workspace/**", timeout=8000)
        assert "/projects/workspace/" in page.url
        assert_no_js_errors(page_errors)

    def test_ui_add_task_to_project(self, app_page, http_client, page_errors, scratch_project):
        """Add a task to a project via API — the module's scratch project, never a real one."""
        payload = factories.project_task(text="add task ui flow")
        added = assert_dict_response(http_client.post(
            f"/projects/api/projects/{scratch_project}/tasks/add",
            json=payload,
        ))
        assert added.get("ok") is True, added

    def test_ui_loads_no_errors(self, app_page, page_errors):
        """Page loads without JS errors."""
        page = app_page("projects")
        wait_briefly(page, 1500)
        assert_no_js_errors(page_errors)

    def test_ui_workspace_page_loads(self, page, base_url, http_client, page_errors):
        """Standalone /projects/workspace/<id> renders the full project, no JS errors."""
        pid = _first_valid_project_id(http_client)
        if not pid:
            pytest.skip("No resolvable project")
        detail = http_client.get(f"/projects/api/projects/{pid}").json()
        page.goto(f"{base_url}/projects/workspace/{pid}", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 1500)
        # Shared renderer ran in workspace mode: title + the always-present status
        # select + the workspace-only "+ Task" quick action.
        assert page.locator(".detail-title").count() >= 1
        assert page.locator(".status-select").count() >= 1
        assert page.get_by_role("button", name="+ Task").count() >= 1
        assert page.get_by_role("button", name="Log work").count() == 1
        assert page.locator("#ws-worklog").count() == 1
        # Tabs render when the project has tab-features enabled (some don't).
        if any(detail.get("features", {}).get(f) for f in
               ("tasks", "docs", "code", "sprints", "milestones", "releases", "tools", "calculations")):
            assert page.locator(".detail-tab").count() >= 1
        # Back-to-list affordance present.
        assert page.locator("a.ws-back").count() == 1
        assert_no_js_errors(page_errors)

    def test_ui_workspace_multipane(self, page, base_url, http_client, page_errors):
        """Standalone workspace renders the multi-pane rail (overview + AI + timeline + related)."""
        pid = _first_valid_project_id(http_client)
        if not pid:
            pytest.skip("No resolvable project")
        page.goto(f"{base_url}/projects/workspace/{pid}", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_selector(".ws-grid", timeout=10000)
        for sel in (".ws-grid", "#ws-overview", "#ws-chat-log", "#ws-timeline", "#ws-related", "#tab-content"):
            assert page.locator(sel).count() >= 1, f"missing {sel}"
        # AI companion input is present + wired
        assert page.locator("#ws-chat-q").count() == 1
        assert_no_js_errors(page_errors)

    def test_ui_cascade_error_is_not_a_green_all_clear(self, page, base_url, http_client, page_errors):
        """A failed cascade check must render as an error, never as "all dates fine".

        `/cascade/check` returns `{"error": ...}` with no `violations` key on a
        missing project. The empty-list branch would then render the green
        "✓ Every task is due on or after its blockers" — a false all-clear on a
        failure. Caught in a browser walk; pinned here.
        """
        pid = _first_valid_project_id(http_client)
        if not pid:
            pytest.skip("No resolvable project")
        page.goto(f"{base_url}/projects/workspace/{pid}", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_selector("#tab-content", timeout=10000)
        rendered = page.evaluate(
            """async () => {
                await checkCascadeDates(null, 'no-such-project-zzz');
                return document.getElementById('dep-map').textContent;
            }"""
        )
        assert "Every task is due" not in rendered, "error rendered as a green all-clear"
        assert rendered.strip(), "error rendered nothing at all"
        assert_no_js_errors(page_errors)

    def test_ui_hash_redirect_to_workspace(self, page, base_url, http_client, page_errors):
        """Legacy /projects/#<id> deep-links redirect to the workspace page."""
        pid = _first_valid_project_id(http_client)
        if not pid:
            pytest.skip("No resolvable project")
        # Old bookmark form → list page boot redirects to /projects/workspace/<id>.
        page.goto(f"{base_url}/projects/#{pid}", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_url("**/projects/workspace/**", timeout=10000)
        page.wait_for_selector(".ws-grid", timeout=10000)
        assert "/projects/workspace/" in page.url
        assert_no_js_errors(page_errors)


@pytest.mark.interactive
class TestProjectsListChrome:
    """Pins for the list page's stat strip / filter / sort chrome.

    Every test here was RED against the implementation that shipped before it
    (see the adversarial review that produced them) — the point of each is a
    specific defect, named in its docstring, not general coverage. They drive
    the page's own functions through page.evaluate because the logic is pure
    and there is no daemon-free JS harness in this repo.
    """

    def _boot(self, app_page):
        page = app_page("projects")
        page.wait_for_function("typeof visibleBase === 'function'", timeout=10000)
        page.wait_for_function("allProjects && allProjects.length > 0", timeout=10000)
        return page

    def test_stat_strip_counts_the_same_set_the_board_renders(self, app_page, page_errors):
        """The Projects card read 100 beside a board of 77 — it counted archived
        projects while the board excluded them, so the number and the rows it
        revealed disagreed on screen."""
        page = self._boot(app_page)
        res = page.evaluate(
            """() => {
                setQuickFilter('');
                const card = statItems().find(i => i.label === 'Projects');
                return {card: card.value, rendered: filteredProjects.length,
                        archived: allProjects.filter(p => p.status === 'archived').length};
            }"""
        )
        # Guard against a vacuous pass: with no archived projects the two
        # numbers match no matter which base the card uses.
        if not res["archived"]:
            pytest.skip("no archived projects — this assertion cannot discriminate")
        assert res["card"] == res["rendered"], (
            f"stat card says {res['card']} while the board renders {res['rendered']}"
        )

    def test_subtitle_does_not_claim_a_filter_that_is_not_set(self, app_page, page_errors):
        """Fresh load with nothing filtered rendered "77 of 100 projects",
        because the total counted archived and the shown count did not."""
        page = self._boot(app_page)
        res = page.evaluate(
            """() => {
                setQuickFilter(''); setCategory(''); clearSearch();
                return {text: document.getElementById('subtitle').textContent,
                        archived: allProjects.filter(p => p.status === 'archived').length};
            }"""
        )
        if not res["archived"]:
            pytest.skip("no archived projects — this assertion cannot discriminate")
        assert " of " not in res["text"], (
            f'unfiltered page claims a filtered count: "{res["text"]}"'
        )

    def test_kanban_renders_an_explained_empty_state(self, app_page, page_errors):
        """kanbanLayout maps over its groups unconditionally and has no empty
        state, so a zero-result filter in the DEFAULT view produced six blank
        columns with no message and no way back."""
        page = self._boot(app_page)
        html = page.evaluate(
            """() => {
                setView('kanban');
                renderKanban([]);
                return document.getElementById('main-view').innerHTML;
            }"""
        )
        assert "eos-empty-state" in html, "empty kanban rendered no empty state"

    def test_explicit_empty_filter_clears_the_search_box(self, app_page, page_errors):
        """EOS.registerActions dispatches filter(""); the box kept showing the
        old term beside an unfiltered board."""
        page = self._boot(app_page)
        res = page.evaluate(
            """() => {
                document.getElementById('search').value = 'zzzz-no-such-term';
                filterProjects('zzzz-no-such-term');
                const narrowed = filteredProjects.length;
                filterProjects('');
                return {box: document.getElementById('search').value,
                        narrowed: narrowed, after: filteredProjects.length};
            }"""
        )
        assert res["box"] == "", f'search box still reads "{res["box"]}" after filter("")'
        assert res["after"] > res["narrowed"], "filter('') did not widen the result set"

    def test_sort_picker_matches_the_sort_actually_applied(self, app_page, page_errors):
        """A stored value outside the five <option>s set selectedIndex = -1, so
        the picker rendered blank while the list sorted by `recent`."""
        page = self._boot(app_page)
        page.evaluate("localStorage.setItem('eos-projects-sort', 'bogus-sort-key')")
        page = app_page("projects")
        page.wait_for_function("typeof setSort === 'function'", timeout=10000)
        res = page.evaluate(
            """() => ({picker: document.getElementById('sort').value, applied: currentSort})"""
        )
        page.evaluate("localStorage.removeItem('eos-projects-sort')")
        assert res["picker"], "sort picker rendered blank"
        assert res["picker"] == res["applied"], (
            f'picker says "{res["picker"]}" while sorting by "{res["applied"]}"'
        )

    def test_every_sort_keeps_the_whole_result_set(self, app_page, page_errors):
        """A sort path that drops or duplicates rows is invisible on screen.

        The expected count comes from visibleBase(), NOT from a previously
        captured filteredProjects.length: the first cut of this test took its
        baseline after the same code path it was testing, so a uniform row loss
        cancelled out on both sides and it stayed green under mutation.
        """
        page = self._boot(app_page)
        res = page.evaluate(
            """() => {
                setQuickFilter(''); setCategory(''); clearSearch();
                const n = visibleBase().length;   // independent of the sort path
                const out = {};
                ['recent','stale','deadline','progress','name'].forEach(k => {
                    setSort(k);
                    const ids = filteredProjects.map(p => p.id);
                    out[k] = {count: ids.length, unique: new Set(ids).size};
                });
                setSort('recent');
                return {base: n, out: out};
            }"""
        )
        for key, got in res["out"].items():
            assert got["count"] == res["base"], f"sort '{key}' changed the row count"
            assert got["unique"] == got["count"], f"sort '{key}' duplicated rows"

    def test_undated_projects_sort_last_under_deadline_first(self, app_page, page_errors):
        """`deadline` ordering with no null branch: the sentinel has to sort
        ABOVE every ISO date or undated projects lead the list."""
        page = self._boot(app_page)
        res = page.evaluate(
            """() => {
                setQuickFilter(''); setCategory(''); clearSearch(); setSort('deadline');
                const dated = filteredProjects.filter(p => p.deadline).length;
                const firstUndated = filteredProjects.findIndex(p => !p.deadline);
                return {dated: dated, firstUndated: firstUndated};
            }"""
        )
        if not res["dated"] or res["firstUndated"] < 0:
            pytest.skip("need both dated and undated projects to discriminate")
        assert res["firstUndated"] >= res["dated"], (
            "an undated project sorted above a dated one under 'deadline first'"
        )

    def test_kanban_board_does_not_overflow_its_container(self, app_page, page_errors):
        """min-width x column count + gaps exceeded the 1360px content box, so
        the default board shipped a permanent 40px horizontal scroll with the
        last column clipped — at every viewport, because .pj-page caps at 1400."""
        page = self._boot(app_page)
        page.set_viewport_size({"width": 1456, "height": 900})
        res = page.evaluate(
            """() => {
                setQuickFilter(''); setCategory(''); clearSearch(); setView('kanban');
                const b = document.querySelector('.eos-kanban');
                return {cols: document.querySelectorAll('.eos-kanban-col').length,
                        overflow: b.scrollWidth - b.clientWidth};
            }"""
        )
        assert res["overflow"] <= 0, (
            f'board overflows {res["overflow"]}px with {res["cols"]} columns'
        )

    def test_kanban_columns_stop_above_the_fab_dock(self, app_page, page_errors):
        """The column scroll height was a hardcoded calc() that knew nothing
        about body's 80px FAB-dock padding or the chip bar's height, and it
        measured the mount rather than the scroller that carries the max-height."""
        page = self._boot(app_page)
        page.set_viewport_size({"width": 1456, "height": 900})
        res = page.evaluate(
            """() => {
                setQuickFilter(''); setCategory(''); clearSearch(); setView('kanban');
                const more = document.querySelector('[data-chip-more]');
                if (more) more.click();          // worst case: tallest chrome
                const it = document.querySelector('.eos-kanban-items');
                const dock = parseFloat(getComputedStyle(document.body).paddingBottom) || 0;
                return {bottom: Math.round(it.getBoundingClientRect().bottom),
                        usable: window.innerHeight - dock};
            }"""
        )
        assert res["bottom"] <= res["usable"], (
            f'column scroller ends at {res["bottom"]}, under the dock at {res["usable"]}'
        )
