"""System tests: platform field-suggest endpoints.

Covers GET /api/sdk/suggest-apps + POST /api/sdk/suggest-field — the auto-mount
discovery + dispatch behind the ✨ field-suggest affordance
(.claude/rules/field-suggest.md). Error/shape paths only — no LLM call — so the
suite stays fast and free. The grounded think() path is sandbox-verified.

NOTE: the daemon must have loaded the new [[provides.field_suggest]] manifests
(restart) for declared apps to appear. The shape asserts are restart-agnostic;
the declared-app asserts are conditional so they don't fail on a stale daemon.
"""

import pytest

from helpers import assert_dict_response


@pytest.mark.api
class TestSuggestAppsDiscovery:
    def test_suggest_apps_shape(self, http_client):
        data = assert_dict_response(http_client.get("/api/sdk/suggest-apps"))
        assert "apps" in data
        assert isinstance(data["apps"], list)
        for app in data["apps"]:
            assert "app_id" in app
            assert "route_prefix" in app
            assert isinstance(app["fields"], list)
            for f in app["fields"]:
                assert "field" in f
                assert f["mode"] in ("fill", "append")
                assert "label" in f

    def test_declared_apps_present_when_loaded(self, http_client):
        # Conditional: only asserts if the daemon already loaded the new manifests.
        data = assert_dict_response(http_client.get("/api/sdk/suggest-apps"))
        by_id = {a["app_id"]: a for a in data["apps"]}
        for app_id, field in (("viz", "prompt"), ("scroll", "topic_hint"), ("forge", "design")):
            if app_id in by_id:
                fields = {f["field"] for f in by_id[app_id]["fields"]}
                assert field in fields, f"{app_id} declared but missing field {field}"


@pytest.mark.api
class TestSuggestFieldErrors:
    def test_missing_app_and_field_rejected(self, http_client):
        r = http_client.post("/api/sdk/suggest-field", json={})
        assert r.status_code == 400

    def test_unknown_app_404(self, http_client):
        r = http_client.post(
            "/api/sdk/suggest-field",
            json={"app": "no-such-app-xyz", "field": "prompt"},
        )
        assert r.status_code == 404

    def test_undeclared_field_404(self, http_client):
        # 'reader' uses the bespoke-endpoint path and declares no field_suggest,
        # so any field is "not declared" on the generic platform endpoint.
        r = http_client.post(
            "/api/sdk/suggest-field",
            json={"app": "reader", "field": "definitely-not-declared"},
        )
        assert r.status_code == 404
