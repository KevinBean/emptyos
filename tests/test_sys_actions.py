"""System tests for the Actions app — template registry, run, workflows."""

from __future__ import annotations

import pytest

from helpers import assert_ok
from page_helpers import assert_no_js_errors, wait_briefly


@pytest.mark.api
class TestActionsAPI:
    def test_app_registered(self, http_client):
        r = http_client.get("/api/apps")
        assert r.status_code == 200
        ids = [a.get("id") for a in r.json()]
        assert "actions" in ids

    def test_index_loads(self, http_client):
        r = http_client.get("/actions/")
        assert r.status_code == 200
        assert "Actions" in r.text

    def test_templates_list_shape(self, http_client):
        """`/actions/api/templates` always returns {templates: [...]}."""
        data = assert_ok(http_client.get("/actions/api/templates"))
        assert "templates" in data
        assert isinstance(data["templates"], list)

    def test_kb_summarize_template_registered(self, http_client):
        """kb's `summarize-blocks` template is the first contributor."""
        data = http_client.get("/actions/api/templates").json()
        ids = [t.get("id") for t in data.get("templates", [])]
        assert "summarize-blocks" in ids

    def test_summarize_template_schema(self, http_client):
        """Args schema is parsed JSON, not raw string."""
        data = http_client.get("/actions/api/templates").json()
        tpl = next(t for t in data["templates"] if t["id"] == "summarize-blocks")
        assert isinstance(tpl.get("args_schema"), list)
        assert tpl["app"] == "kb"
        assert tpl["kind"] == "llm"

    def test_run_unknown_template_errors(self, http_client):
        r = http_client.post("/actions/api/run", json={"template_id": "zzz-missing", "items": []})
        assert r.status_code == 200
        assert "error" in r.json()

    def test_run_with_no_items_errors_cleanly(self, http_client):
        """Running summarize-blocks with no items should return a structured error, not 500."""
        r = http_client.post("/actions/api/run", json={
            "template_id": "summarize-blocks",
            "items": [],
            "args": {"style": "bullet"},
        })
        assert r.status_code == 200
        # Should be {"ok": True, "result": {"error": "no blocks selected"}}
        body = r.json()
        assert body.get("ok") is True
        assert "error" in body.get("result", {})

    def test_workflows_list_shape(self, http_client):
        data = assert_ok(http_client.get("/actions/api/workflows"))
        assert "workflows" in data
        assert isinstance(data["workflows"], list)

    def test_workflow_create_requires_title(self, http_client):
        r = http_client.post("/actions/api/workflows", json={"steps": []}).json()
        assert "error" in r

    def test_workflow_run_unknown_errors(self, http_client):
        r = http_client.post("/actions/api/workflows/zzz-missing/run", json={"items": []})
        assert r.status_code == 200
        assert "error" in r.json()


@pytest.mark.interactive
class TestActionsUI:
    def test_ui_loads(self, app_page, page_errors):
        page = app_page("actions")
        wait_briefly(page, 2000)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_templates_tab_renders(self, app_page, page_errors):
        page = app_page("actions")
        wait_briefly(page, 2000)
        # The templates pane is visible by default
        assert page.locator("#pane-templates").is_visible()
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])
