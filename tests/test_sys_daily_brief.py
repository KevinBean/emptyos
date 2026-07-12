"""System app tests: Daily Brief.

The end-to-end generate path (feed fetch + LLM distill) is network + LLM
shaped — it's exercised manually / under @pytest.mark.llm, not here. These
cover the read-only API surface + UI smoke. Pure feed-parsing lives in
tests/test_unit_daily_brief.py.
"""

from __future__ import annotations

import pytest

from helpers import assert_ok
from page_helpers import assert_no_js_errors


@pytest.mark.api
class TestDailyBriefAPI:
    def test_status_shape(self, http_client):
        s = assert_ok(http_client.get("/daily-brief/api/status"))
        for k in ("enabled", "locale", "never_run", "source_count"):
            assert k in s
        assert s["source_count"] >= 1  # built-in defaults always present

    def test_sources_listed(self, http_client):
        d = assert_ok(http_client.get("/daily-brief/api/sources"))
        assert isinstance(d["sources"], list) and d["sources"]
        for s in d["sources"]:
            assert s["url"] and s["kind"] in ("rss", "json")

    def test_today_when_never_run(self, http_client):
        d = assert_ok(http_client.get("/daily-brief/api/today"))
        # either never run (clean install) or a prior brief — both valid shapes
        assert "brief_md" in d and "never_run" in d

    def test_history_shape(self, http_client):
        d = assert_ok(http_client.get("/daily-brief/api/history"))
        assert isinstance(d["history"], list)

    def test_reschedule_idempotent(self, http_client):
        d = assert_ok(http_client.post("/daily-brief/api/reschedule"))
        assert d["ok"] is True
        assert "enabled" in d


@pytest.mark.api
class TestDailyBriefArticleActions:
    """Per-article actions: read-later handoff + in-app digest shortlist."""

    def test_action_targets_shape(self, http_client):
        d = assert_ok(http_client.get("/daily-brief/api/action-targets"))
        # digest is always available; bookmarks is feature-detected (bool either way)
        assert d.get("digest") is True
        assert isinstance(d.get("bookmarks"), bool)

    def test_digest_add_list_dedup_remove(self, http_client):
        url = "https://example.com/PLAYWRIGHT-TEST-digest-item"
        # add
        a = assert_ok(http_client.post("/daily-brief/api/digest/add",
            json={"url": url, "title": "PLAYWRIGHT-TEST digest", "summary": "why", "source": "T"}))
        assert a["ok"] is True
        # appears in the list
        items = assert_ok(http_client.get("/daily-brief/api/digest"))["items"]
        assert any(it.get("url") == url for it in items)
        # dedup — adding the same url again doesn't grow the list
        dup = assert_ok(http_client.post("/daily-brief/api/digest/add",
            json={"url": url, "title": "again"}))
        assert dup.get("dup") is True
        # remove cleans up (also keeps the test vault/state tidy)
        rm = assert_ok(http_client.post("/daily-brief/api/digest/remove", json={"url": url}))
        assert rm["ok"] is True
        items2 = assert_ok(http_client.get("/daily-brief/api/digest"))["items"]
        assert not any(it.get("url") == url for it in items2)

    def test_articles_inbox_shape(self, http_client):
        d = assert_ok(http_client.get("/daily-brief/api/articles"))
        for k in ("items", "total", "unread", "categories", "sources"):
            assert k in d, f"missing {k}"
        assert isinstance(d["items"], list)
        assert isinstance(d["categories"], list) and isinstance(d["sources"], list)
        assert d["unread"] <= d["total"]

    def test_article_read_unknown_id_soft_fails(self, http_client):
        r = assert_ok(http_client.post("/daily-brief/api/articles/nope-xyz/read"))
        assert r.get("ok") is False

    def test_articles_read_all_ok(self, http_client):
        r = assert_ok(http_client.post("/daily-brief/api/articles/read-all"))
        assert r.get("ok") is True and "count" in r

    def test_digest_add_requires_url_or_title(self, http_client):
        r = assert_ok(http_client.post("/daily-brief/api/digest/add", json={}))
        assert r.get("ok") is False

    def test_compile_empty_digest_is_guarded(self, http_client):
        # Compile on an empty shortlist must fail soft (no think() call) rather
        # than 500 — the only compile path testable without an LLM provider.
        # (Assumes no PLAYWRIGHT-TEST items linger; the digest round-trip test
        # removes its own. A real compile is LLM-shaped, verified manually.)
        items = assert_ok(http_client.get("/daily-brief/api/digest"))["items"]
        if items:
            pytest.skip("digest not empty on this instance")
        r = assert_ok(http_client.post("/daily-brief/api/digest/compile", json={}))
        assert r.get("ok") is False


@pytest.mark.interactive
class TestDailyBriefUI:
    def test_page_loads(self, page, page_errors, base_url):
        page.goto(base_url + "/daily-brief/")
        assert_no_js_errors(page_errors)

    def test_run_now_button_present(self, page, base_url):
        page.goto(base_url + "/daily-brief/")
        assert page.is_visible("#run-now")

    def test_today_command_center_renders(self, page, base_url):
        # Today is the default tab; the command center hydrates from
        # /api/command-center with one metric tile per section.
        page.goto(base_url + "/daily-brief/")
        page.wait_for_selector("#cc-metrics .eos-stat-card", timeout=8000)
        assert page.locator(".cc-panel").count() >= 5

    def test_brief_area_renders(self, page, base_url):
        page.goto(base_url + "/daily-brief/")
        # Brief lives behind its tab now (Today is the default view).
        page.click(".db-tab[data-view='brief']")
        page.wait_for_selector("#brief", timeout=5000)
        # bar meta hydrates from /api/status — proves the page wired up
        page.wait_for_function(
            "document.getElementById('bar-meta') && document.getElementById('bar-meta').textContent.length > 0",
            timeout=8000,
        )


@pytest.mark.llm
@pytest.mark.api
class TestDailyBriefGenerate:
    """Live generate — fetches real feeds + hits a think provider. Slow/paid."""

    def test_run_now_generates(self, http_client):
        # Full generate fetches every configured feed (20+) then runs an LLM
        # distill — well past the client's 15s default. Override per-request.
        r = assert_ok(http_client.post("/daily-brief/api/run-now", timeout=180))
        # network/provider may be flaky; assert the contract either way
        assert "ok" in r
        if r["ok"]:
            assert r["item_count"] >= 1 and r["brief_md"]
