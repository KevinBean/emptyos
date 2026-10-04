"""System app tests: replay — record & replay a finished agent session as a recipe.

Replay is dark-flag-gated (``replay.feature.enabled``). These assertions hold
whether the flag is on or off so the suite is green in CI (default off) and
locally (on): every endpoint returns a 200 envelope, and the validation/not-found
contracts collapse to ``ok: false`` / ``error`` in both states.
"""

import pytest

from helpers import assert_dict_response, assert_ok
from page_helpers import assert_no_js_errors, wait_briefly


@pytest.mark.api
class TestReplayAPI:
    def test_config_exposes_enabled_flag(self, http_client):
        data = assert_dict_response(http_client.get("/replay/api/config"),
                                    required_keys=["enabled"])
        assert isinstance(data["enabled"], bool)

    def test_recipes_list_shape(self, http_client):
        """Returns a recipes list either way (empty when the feature is dark)."""
        data = assert_dict_response(http_client.get("/replay/api/recipes"),
                                    required_keys=["recipes"])
        assert isinstance(data["recipes"], list)

    def test_recipe_missing_returns_error(self, http_client):
        data = assert_ok(http_client.get("/replay/api/recipes/no-such-recipe-xyz"))
        assert "error" in data  # "not found" when enabled, "disabled" when off

    def test_distill_session_requires_sid(self, http_client):
        data = assert_ok(http_client.post("/replay/api/distill/session", json={}))
        assert data.get("ok") is False  # "sid required" or "disabled"

    def test_distill_trace_requires_trace_id(self, http_client):
        data = assert_ok(http_client.post("/replay/api/distill/trace", json={}))
        assert data.get("ok") is False

    def test_run_status_missing(self, http_client):
        data = assert_ok(http_client.get("/replay/api/runs/no-such-run-xyz"))
        assert data.get("ok") is False

    def test_dry_run_missing_recipe(self, http_client):
        """A dry-run of a non-existent recipe must fail soft, never 500."""
        data = assert_ok(http_client.post(
            "/replay/api/recipes/no-such-recipe-xyz/dry-run", json={"inputs": {}}))
        assert data.get("ok") is False or "error" in data

    def test_resume_missing_run(self, http_client):
        data = assert_ok(http_client.post(
            "/replay/api/runs/no-such-run-xyz/resume", json={}))
        assert data.get("ok") is False or "error" in data


@pytest.mark.interactive
class TestReplayUI:
    def test_ui_loads(self, app_page, page_errors):
        page = app_page("replay")
        wait_briefly(page, 1500)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_no_critical_errors(self, app_page, page_errors):
        page = app_page("replay")
        wait_briefly(page, 2000)
        critical = [e for e in page_errors if "TypeError" in str(e)]
        assert not critical, critical
