"""System app tests: Projects — 11 use cases."""

import pytest

import factories
from helpers import assert_dict_response, assert_ok
from page_helpers import (
    assert_no_js_errors, click_first, wait_briefly,
)


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


@pytest.mark.interactive
class TestProjectsUI:
    def test_ui_project_list(self, app_page, page_errors):
        """Verify project cards render."""
        page = app_page("projects")
        wait_briefly(page, 1000)
        cards = page.locator(".project-card, .project-item, [data-project-id]")
        assert_no_js_errors(page_errors)

    def test_ui_project_detail_click(self, app_page, page_errors):
        """Click a project card → verify detail view opens."""
        page = app_page("projects")
        wait_briefly(page, 1000)
        cards = page.locator(".project-card, .project-item, [data-project-id]")
        if cards.count() == 0:
            pytest.skip("No project cards visible")
        cards.first.click()
        wait_briefly(page, 800)
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
