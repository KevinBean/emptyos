"""System tests for apps/kb-gap-miner/ — the Q&A-history mining loop.

Exercises the live HTTP contract against the running daemon. Pure-logic
coverage (question filter, signature grouping, coverage matching, lifecycle
transitions) lives in tests/test_unit_kb_gap_miner.py; this file checks the
endpoints behave and the page loads.

Mutations use the {"dry": true} sweep mode only — observe + coverage with no
LLM calls and no proposals — so the suite never burns cloud spend or files
pending actions. The only state written is gaps.json (the app's own data/
telemetry), so no TEST_PREFIX cleanup is needed.
"""

import pytest

from helpers import assert_dict_response


@pytest.mark.api
class TestKBGapMinerAPI:

    def test_status_shape(self, http_client):
        d = assert_dict_response(http_client.get("/kb-gap-miner/api/status"))
        for k in ("total", "by_status", "enabled"):
            assert k in d, f"status missing {k!r}: {list(d.keys())}"
        assert isinstance(d["by_status"], dict)

    def test_dry_sweep_returns_summary(self, http_client):
        d = assert_dict_response(http_client.post("/kb-gap-miner/api/sweep", json={"dry": True}))
        for k in ("messages", "pairs", "gaps", "open", "new", "lookback_days"):
            assert k in d, f"dry sweep missing {k!r}: {list(d.keys())}"
        assert isinstance(d["new"], list)
        assert d["lookback_days"] > 0

    def test_dry_sweep_never_reports_llm_work(self, http_client):
        # {"dry": true} is the no-LLM contract — triage/proposal keys belong
        # to the full cycle only.
        d = http_client.post("/kb-gap-miner/api/sweep", json={"dry": True}).json()
        assert "proposed" not in d and "triaged" not in d

    def test_gaps_list_shape(self, http_client):
        http_client.post("/kb-gap-miner/api/sweep", json={"dry": True})
        d = assert_dict_response(http_client.get("/kb-gap-miner/api/gaps"))
        assert "gaps" in d and isinstance(d["gaps"], list)
        assert d["count"] == len(d["gaps"])

    def test_gaps_sorted_by_score_desc(self, http_client):
        http_client.post("/kb-gap-miner/api/sweep", json={"dry": True})
        gaps = http_client.get("/kb-gap-miner/api/gaps").json()["gaps"]
        scores = [g.get("score", 0) for g in gaps]
        assert scores == sorted(scores, reverse=True)

    def test_gaps_status_filter(self, http_client):
        http_client.post("/kb-gap-miner/api/sweep", json={"dry": True})
        opened = http_client.get("/kb-gap-miner/api/gaps?status=open").json()["gaps"]
        assert all(g["status"] == "open" for g in opened)

    def test_gap_fields_present(self, http_client):
        http_client.post("/kb-gap-miner/api/sweep", json={"dry": True})
        gaps = http_client.get("/kb-gap-miner/api/gaps").json()["gaps"]
        if not gaps:
            pytest.skip("no Q&A history gaps to assert field shape against")
        for k in ("hash", "question", "signature", "ask_count", "score",
                  "status", "first_asked", "last_asked", "sources"):
            assert k in gaps[0], f"gap missing {k!r}: {list(gaps[0].keys())}"
        assert gaps[0]["status"] in (
            "open", "covered", "proposed", "resolved", "reopened", "dismissed")

    def test_propose_on_missing_gap_errors(self, http_client):
        d = http_client.post("/kb-gap-miner/api/gaps/does-not-exist/propose", json={}).json()
        assert d.get("error"), "proposing a missing gap should error, not file an action"

    def test_dismiss_on_missing_gap_errors(self, http_client):
        d = http_client.post("/kb-gap-miner/api/gaps/does-not-exist/dismiss", json={}).json()
        assert d.get("error"), "dismissing a missing gap should error"


@pytest.mark.interactive
class TestKBGapMinerUI:

    def test_page_loads_without_js_errors(self, app_page, page_errors):
        from page_helpers import assert_no_js_errors

        app_page("kb-gap-miner")
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_sweep_button_present(self, app_page):
        page = app_page("kb-gap-miner")
        assert page.locator("text=Sweep now").count() >= 1
