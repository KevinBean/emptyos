"""System app tests: Projects — 11 use cases."""

import pytest

import factories
from helpers import TEST_PREFIX, assert_dict_response, assert_ok
from page_helpers import (
    assert_no_js_errors, click_first, wait_briefly,
)


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

    def test_worklog_bridge_roundtrip(self, http_client):
        """A project workspace writes through Worklog and reads the item back."""
        pid = _first_valid_project_id(http_client)
        if not pid:
            pytest.skip("No resolvable project")
        text = TEST_PREFIX + "project worklog bridge"
        detail = assert_dict_response(http_client.get(f"/projects/api/projects/{pid}"))
        project_employer = detail.get("employer", "")
        logged = assert_dict_response(http_client.post(
            f"/projects/api/projects/{pid}/worklog",
            json={"text": text, "status": "complete"},
        ))
        assert logged.get("ok") is True, logged
        assert "employer" in logged
        if project_employer:
            assert logged["employer"] == project_employer
        recent = assert_dict_response(http_client.get(
            f"/projects/api/projects/{pid}/worklog?days=30&limit=20"
        ))
        assert recent.get("available") is True, recent
        assert "employer" in recent
        assert any(item.get("text") == text for item in recent.get("items", []))

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

    def test_ui_add_task_to_project(self, app_page, http_client, page_errors):
        """Add a task to first project via API."""
        listing = http_client.get("/projects/api/list").json()
        items = listing if isinstance(listing, list) else listing.get("projects", [])
        if not items:
            pytest.skip("No projects")
        pid = items[0].get("id") or items[0].get("name")
        payload = factories.project_task(text="add task ui flow")
        resp = http_client.post(
            f"/projects/api/projects/{pid}/tasks/add",
            json=payload,
        )
        assert resp.status_code in (200, 201)

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
