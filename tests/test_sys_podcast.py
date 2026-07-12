"""System app tests: Podcast.

The end-to-end generate path (script LLM + TTS + cover/slideshow) is network +
LLM + GPU shaped — exercised manually / via the sandbox, not here. These cover
the read-only API surface + the UI smoke after the 2026-06 shared-component
consolidation (statCards / .eos-tabs / .eos-badge / EOS_UI.modal / emptyState).

Side-effect-free: no episode is generated and /api/voice-sample (which writes a
cached clip into the vault) is deliberately not called, so the suite leaves no
artifacts behind.
"""

from __future__ import annotations

import pytest

from helpers import assert_ok
from page_helpers import assert_no_js_errors


@pytest.mark.api
class TestPodcastAPI:
    def test_voice_pairs_shape(self, http_client):
        d = assert_ok(http_client.get("/podcast/api/voice-pairs"))
        assert isinstance(d["pairs"], list) and d["pairs"]
        for p in d["pairs"]:
            assert p["id"] and p["label"] and p["A"] and p["B"]
        assert isinstance(d["durations"], dict) and "short" in d["durations"]
        assert isinstance(d["image_styles"], list) and d["image_styles"]

    def test_history_is_list(self, http_client):
        d = assert_ok(http_client.get("/podcast/api/history"))
        assert isinstance(d, list)

    def test_files_shape(self, http_client):
        d = assert_ok(http_client.get("/podcast/api/files"))
        assert "files" in d and isinstance(d["files"], list)
        assert "dir" in d and "count" in d

    def test_vault_source_returns_topic_context(self, http_client):
        # Never raises — degrades to a default topic + empty context.
        d = assert_ok(http_client.get("/podcast/api/vault-source/vault_daily"))
        assert d["source"] == "vault_daily"
        assert "topic" in d and "context" in d
        assert isinstance(d["topic"], str)

    def test_vault_source_unknown_degrades(self, http_client):
        d = assert_ok(http_client.get("/podcast/api/vault-source/not_a_source"))
        assert d["topic"]  # falls back to a sensible default, never blank


@pytest.mark.interactive
class TestPodcastUI:
    def test_page_loads(self, page, page_errors, base_url):
        page.goto(base_url + "/podcast/")
        assert_no_js_errors(page_errors)

    def test_stats_render_via_statcards(self, page, base_url):
        page.goto(base_url + "/podcast/")
        # Hero stats migrated to EOS_UI.statCards → .eos-stat-card grid.
        page.wait_for_selector("#pod-stats .eos-stat-card", timeout=8000)
        assert page.locator("#pod-stats .eos-stat-card").count() == 3

    def test_shared_tabs_switch(self, page, base_url):
        page.goto(base_url + "/podcast/")
        page.wait_for_selector(".eos-tabs .eos-tab", timeout=5000)
        page.get_by_role("tab", name="Episodes").click()
        page.wait_for_selector("#tab-history.active", timeout=5000)

    def test_episodes_tab_renders_state(self, page, base_url):
        page.goto(base_url + "/podcast/")
        page.get_by_role("tab", name="Episodes").click()
        # Either a populated list or the shared empty-state — never blank.
        page.wait_for_function(
            "document.getElementById('history-list') && "
            "document.getElementById('history-list').children.length > 0",
            timeout=8000,
        )

    def test_create_wizard_present(self, page, base_url):
        page.goto(base_url + "/podcast/")
        assert page.is_visible("#btn-script")
        assert page.is_visible(".vault-start")  # vault quick-start chips
