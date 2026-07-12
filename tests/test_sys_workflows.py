"""System app tests: workflows — author + run visual workflows over reversible verbs.

The workflows app is a generic consumer of the graph stack: definition CRUD
(stored as data/apps/workflows/*.json), a reversible-verb menu gated against the
verb registry's *stable* set, and a run engine with human decision branches.
"""

import pytest

from helpers import TEST_PREFIX, assert_dict_response, assert_ok
from page_helpers import assert_no_js_errors, wait_briefly


@pytest.mark.api
class TestWorkflowsAPI:
    def test_defs_list_shape(self, http_client):
        resp = http_client.get("/workflows/api/defs")
        data = assert_dict_response(resp, required_keys=["defs"])
        assert isinstance(data["defs"], list)

    def test_seeded_template_present(self, http_client):
        """setup() seeds the capture-triage template — it should always be listed."""
        data = assert_ok(http_client.get("/workflows/api/defs"))
        ids = [d.get("id") for d in data["defs"]]
        assert "capture-triage" in ids

    def test_def_get_returns_graph(self, http_client):
        data = assert_ok(http_client.get("/workflows/api/defs/capture-triage"))
        assert data.get("id") == "capture-triage"
        assert isinstance(data.get("nodes"), list) and data["nodes"]
        assert "start" in data

    def test_def_get_missing(self, http_client):
        data = assert_ok(http_client.get("/workflows/api/defs/no-such-workflow-xyz"))
        assert "error" in data

    def test_verbs_are_reversible_only(self, http_client):
        """The author menu must expose only stable/reversible verbs — never an
        irreversible one like publish.deploy (the CLAUDE.md north star)."""
        data = assert_dict_response(http_client.get("/workflows/api/verbs"),
                                    required_keys=["verbs"])
        verbs = [v.get("verb") for v in data["verbs"]]
        assert all(isinstance(v, str) for v in verbs)
        for forbidden in ("publish.deploy",):
            assert forbidden not in verbs

    def test_save_requires_id(self, http_client):
        resp = http_client.post("/workflows/api/defs", json={"graph": {"nodes": []}})
        data = assert_ok(resp)
        assert "error" in data

    def test_save_rejects_bad_id(self, http_client):
        resp = http_client.post("/workflows/api/defs",
                                json={"id": "bad id!", "graph": {"id": "bad id!"}})
        data = assert_ok(resp)
        assert "error" in data

    def test_save_and_delete_roundtrip(self, http_client):
        """Clone the seeded template under a test id (guarantees a valid graph),
        save it, then delete it — exercises validate + persist + remove."""
        wid = f"{TEST_PREFIX}wf-roundtrip"
        graph = assert_ok(http_client.get("/workflows/api/defs/capture-triage"))
        graph["id"] = wid
        try:
            save = assert_ok(http_client.post("/workflows/api/defs",
                                              json={"id": wid, "graph": graph}))
            assert save.get("ok") is True, save
            assert save.get("id") == wid
            listed = assert_ok(http_client.get("/workflows/api/defs"))
            assert wid in [d.get("id") for d in listed["defs"]]
        finally:
            dele = http_client.request("DELETE", f"/workflows/api/defs/{wid}")
            assert dele.status_code == 200
        assert wid not in [d.get("id") for d in assert_ok(
            http_client.get("/workflows/api/defs"))["defs"]]

    def test_runs_list_shape(self, http_client):
        data = assert_dict_response(http_client.get("/workflows/api/runs"),
                                    required_keys=["runs"])
        assert isinstance(data["runs"], list)

    def test_run_rejects_unknown_workflow(self, http_client):
        resp = http_client.post("/workflows/api/runs",
                                json={"workflow_id": "no-such-workflow-xyz"})
        data = assert_ok(resp)
        assert "error" in data

    def test_decision_requires_choice(self, http_client):
        resp = http_client.post("/workflows/api/runs/nonexistent/decision", json={})
        data = assert_ok(resp)
        assert "error" in data


@pytest.mark.api
class TestWorkflowTemplatesAPI:
    """Action-template registry + chains (absorbed from the retired actions app)."""

    def test_templates_list_shape(self, http_client):
        data = assert_ok(http_client.get("/workflows/api/templates"))
        assert "templates" in data
        assert isinstance(data["templates"], list)

    def test_kb_summarize_template_registered(self, http_client):
        """kb's `summarize-notes` template contributes via
        [[contributes.workflows.template]] (slot moved from actions 2026-07-10)."""
        data = http_client.get("/workflows/api/templates").json()
        ids = [t.get("id") for t in data.get("templates", [])]
        assert "summarize-notes" in ids

    def test_summarize_template_schema(self, http_client):
        """Args schema is parsed JSON, not raw string."""
        data = http_client.get("/workflows/api/templates").json()
        tpl = next(t for t in data["templates"] if t["id"] == "summarize-notes")
        assert isinstance(tpl.get("args_schema"), list)
        assert tpl["app"] == "kb"
        assert tpl["kind"] == "llm"

    def test_run_unknown_template_errors(self, http_client):
        r = http_client.post("/workflows/api/templates/run",
                             json={"template_id": "zzz-missing", "items": []})
        assert r.status_code == 200
        assert "error" in r.json()

    def test_run_with_no_items_errors_cleanly(self, http_client):
        """Running summarize-notes with no items returns a structured error, not 500."""
        r = http_client.post("/workflows/api/templates/run", json={
            "template_id": "summarize-notes",
            "items": [],
            "args": {"style": "bullet"},
        })
        assert r.status_code == 200
        body = r.json()
        assert body.get("ok") is True
        assert "error" in body.get("result", {})

    def test_chains_list_shape(self, http_client):
        data = assert_ok(http_client.get("/workflows/api/chains"))
        assert "chains" in data
        assert isinstance(data["chains"], list)

    def test_chain_create_requires_title(self, http_client):
        r = http_client.post("/workflows/api/chains", json={"steps": []}).json()
        assert "error" in r

    def test_chain_run_unknown_errors(self, http_client):
        r = http_client.post("/workflows/api/chains/zzz-missing/run", json={"items": []})
        assert r.status_code == 200
        assert "error" in r.json()


@pytest.mark.interactive
class TestWorkflowsUI:
    def test_ui_loads(self, app_page, page_errors):
        page = app_page("workflows")
        wait_briefly(page, 1500)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_no_critical_errors(self, app_page, page_errors):
        page = app_page("workflows")
        wait_briefly(page, 2000)
        critical = [e for e in page_errors if "TypeError" in str(e)]
        assert not critical, critical
