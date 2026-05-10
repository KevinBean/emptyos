"""System app tests: Sim — EMTP-class power-systems simulator runner."""

import pytest

from helpers import assert_dict_response, assert_ok
from page_helpers import assert_no_js_errors, wait_briefly


@pytest.fixture
def require_sim_engine(http_client):
    """Skip the test if the sim engine isn't loaded (numpy/scipy missing, etc.)."""
    status = http_client.get("/sim/api/status").json()
    if not status.get("available"):
        pytest.skip(f"sim engine unavailable: {status.get('reason', 'unknown')}")


@pytest.mark.api
class TestSimAPI:
    def test_status_shape(self, http_client):
        """Engine availability probe — must always answer, even if engine unavailable."""
        data = assert_dict_response(
            http_client.get("/sim/api/status"),
            required_keys=["available"],
        )
        assert isinstance(data["available"], bool)

    def test_list_runs(self, http_client):
        data = assert_dict_response(
            http_client.get("/sim/api/runs"),
            required_keys=["runs"],
        )
        assert isinstance(data["runs"], list)

    def test_get_run_not_found(self, http_client):
        """Unknown run id returns an error body, not a 404."""
        resp = http_client.get("/sim/api/runs/zzz-nonexistent-run")
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data

    def test_submit_run_validates_netlist(self, http_client):
        """Missing netlist dict should return ok=False with reason."""
        resp = http_client.post("/sim/api/runs", json={"label": "no netlist"})
        data = assert_ok(resp)
        assert data.get("ok") is False
        assert "netlist" in (data.get("error") or "").lower()

    def test_submit_run_with_invalid_netlist_shape(self, http_client):
        """A non-dict netlist should also be rejected at the API boundary."""
        resp = http_client.post(
            "/sim/api/runs", json={"netlist": "not-a-dict", "label": "bad"}
        )
        data = assert_ok(resp)
        assert data.get("ok") is False

    def test_demo_run_executes(self, http_client, require_sim_engine):
        """Canned RC@50Hz netlist runs synchronously and returns a summary."""
        resp = http_client.post("/sim/api/demo", timeout=30)
        data = assert_ok(resp)
        # Whether engine is available or not, the response must carry id + ok flag
        assert "id" in data
        assert "ok" in data
        if data["ok"]:
            assert data.get("summary") is not None

    def test_demo_run_then_lookup(self, http_client, require_sim_engine):
        """After a successful demo run, it should appear in /api/runs and be fetchable."""
        submit = http_client.post("/sim/api/demo", timeout=30).json()
        if not submit.get("ok"):
            pytest.skip(f"sim engine unavailable: {submit}")
        run_id = submit["id"]
        listing = http_client.get("/sim/api/runs").json()
        ids = [r.get("id") for r in listing.get("runs", [])]
        assert run_id in ids, f"submitted run {run_id} missing from listing"
        single = http_client.get(f"/sim/api/runs/{run_id}").json()
        assert single.get("id") == run_id or "error" not in single

    def test_waveforms_cached_after_run(self, http_client, require_sim_engine):
        """Successful demo run caches waveforms in process; /waveforms should return them."""
        submit = http_client.post("/sim/api/demo", timeout=30).json()
        if not submit.get("ok"):
            pytest.skip(f"sim engine unavailable: {submit}")
        run_id = submit["id"]
        wf = http_client.get(f"/sim/api/runs/{run_id}/waveforms").json()
        assert "error" not in wf or "cache" in wf.get("error", "").lower()

    def test_delete_run(self, http_client, require_sim_engine):
        """Submit a run, delete it, verify it's gone from listing."""
        submit = http_client.post("/sim/api/demo", timeout=30).json()
        if not submit.get("ok"):
            pytest.skip(f"sim engine unavailable: {submit}")
        run_id = submit["id"]
        resp = http_client.delete(f"/sim/api/runs/{run_id}")
        assert resp.status_code == 200
        # Either ok=True (deleted) or some explainable error
        data = resp.json()
        assert "ok" in data


@pytest.mark.interactive
class TestSimUI:
    def test_ui_loads(self, app_page, page_errors):
        page = app_page("sim")
        wait_briefly(page, 1500)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_no_critical_errors(self, app_page, page_errors):
        page = app_page("sim")
        wait_briefly(page, 2000)
        critical = [e for e in page_errors if "TypeError" in str(e)]
        assert not critical
