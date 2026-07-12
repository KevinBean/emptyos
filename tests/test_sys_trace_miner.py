"""System tests for apps/trace-miner/ — the syslog mining friction source.

Exercises the live HTTP contract against the running daemon. Pure-logic
coverage (signature normalization, classification, scoring) lives in
tests/test_unit_trace_miner.py; this file checks the endpoints behave and the
page loads. Read-only against syslog — the only mutation is issues.json (the
app's own data/ telemetry), so no TEST_PREFIX cleanup is needed.
"""

import pytest

from helpers import assert_dict_response


@pytest.mark.api
class TestTraceMinerAPI:

    def test_status_shape(self, http_client):
        d = assert_dict_response(http_client.get("/trace-miner/api/status"))
        for k in ("total", "by_kind", "prompted"):
            assert k in d, f"status missing {k!r}: {list(d.keys())}"
        assert isinstance(d["by_kind"], dict)

    def test_scan_returns_summary(self, http_client):
        d = assert_dict_response(http_client.post("/trace-miner/api/scan", json={}))
        for k in ("scanned_rows", "issues", "new_codebugs", "lookback_days"):
            assert k in d, f"scan missing {k!r}: {list(d.keys())}"
        assert isinstance(d["new_codebugs"], list)
        assert d["lookback_days"] > 0

    def test_issues_list_shape(self, http_client):
        http_client.post("/trace-miner/api/scan", json={})
        d = assert_dict_response(http_client.get("/trace-miner/api/issues"))
        assert "issues" in d and isinstance(d["issues"], list)
        assert d["count"] == len(d["issues"])

    def test_issues_default_hides_external(self, http_client):
        http_client.post("/trace-miner/api/scan", json={})
        default = http_client.get("/trace-miner/api/issues").json()["issues"]
        assert all(i["kind"] != "external" for i in default), "external leaked into default view"

    def test_issues_external_flag_includes_them(self, http_client):
        http_client.post("/trace-miner/api/scan", json={})
        with_ext = http_client.get("/trace-miner/api/issues?external=1").json()["issues"]
        default = http_client.get("/trace-miner/api/issues").json()["issues"]
        assert len(with_ext) >= len(default)

    def test_issues_sorted_by_score_desc(self, http_client):
        http_client.post("/trace-miner/api/scan", json={})
        issues = http_client.get("/trace-miner/api/issues?external=1").json()["issues"]
        scores = [i.get("score", 0) for i in issues]
        assert scores == sorted(scores, reverse=True)

    def test_issue_fields_present(self, http_client):
        http_client.post("/trace-miner/api/scan", json={})
        issues = http_client.get("/trace-miner/api/issues?external=1").json()["issues"]
        if not issues:
            pytest.skip("no syslog issues to assert field shape against")
        for k in ("hash", "signature", "kind", "count", "score", "status", "first_seen", "last_seen"):
            assert k in issues[0], f"issue missing {k!r}: {list(issues[0].keys())}"
        assert issues[0]["kind"] in ("code-bug", "unknown", "external")

    def test_emit_on_missing_issue_errors(self, http_client):
        d = http_client.post("/trace-miner/api/issues/does-not-exist/fix-prompt", json={}).json()
        assert d.get("error"), "emitting on a missing issue should error, not create a prompt"

    def test_verify_untracked_hash_is_optimistic(self, http_client):
        # fix-agent relies on this: an untracked signature must not block a merge.
        d = http_client.post(
            "/trace-miner/api/verify",
            json={"verify_signature": "deadbeef9999", "since_ts": "2026-01-01T00:00:00+00:00"},
        ).json()
        assert d.get("target_fixed") is True
        assert d.get("recurred") == 0

    def test_verify_accepts_iso_and_epoch(self, http_client):
        for ts in ("2026-01-01T00:00:00+00:00", 1700000000, "1700000000"):
            d = http_client.post(
                "/trace-miner/api/verify",
                json={"verify_signature": "deadbeef9999", "since_ts": ts},
            ).json()
            assert "target_fixed" in d, f"verify failed for since_ts={ts!r}: {d}"


@pytest.mark.interactive
class TestTraceMinerUI:

    def test_page_loads_without_js_errors(self, app_page, page_errors):
        from page_helpers import assert_no_js_errors

        app_page("trace-miner")
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_scan_button_present(self, app_page):
        page = app_page("trace-miner")
        assert page.locator("text=Scan now").count() >= 1
