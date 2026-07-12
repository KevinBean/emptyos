"""System app tests: Explore (public-web research with cited answers) — 14 use cases.

Non-network cases cover route plumbing, input validation, the SSRF guard, and
the vault save path. The one real-web search case is @pytest.mark.llm so the
fast lane stays offline (DDG flakes shouldn't fail CI).
"""

from pathlib import Path

import pytest
from helpers import TEST_PREFIX, assert_dict_response
from page_helpers import assert_no_js_errors, wait_for_toast


def _vault_root() -> Path:
    import tomllib

    cfg_path = Path(__file__).resolve().parents[1] / "emptyos.toml"
    with open(cfg_path, "rb") as f:
        data = tomllib.load(f)
    return Path((data.get("notes") or {}).get("path", ""))


@pytest.mark.api
class TestExploreAPI:
    def test_recent_structure(self, http_client):
        data = assert_dict_response(http_client.get("/explore/api/recent"))
        assert isinstance(data["recent"], list)
        for r in data["recent"]:
            assert {"query", "sources", "ts"} <= set(r)

    def test_search_requires_query(self, http_client):
        data = assert_dict_response(http_client.get("/explore/api/search?q="))
        assert data["ok"] is False
        assert "q is required" in data["error"]

    def test_read_rejects_invalid_url(self, http_client):
        r = http_client.post("/explore/api/read", json={"url": "not-a-url"})
        data = assert_dict_response(r)
        assert data["ok"] is False
        assert "invalid URL" in data["error"]

    def test_read_blocks_non_public_url(self, http_client):
        """SSRF guard: the server-side browser must refuse loopback/private
        targets before navigating (no auth, ComfyUI, sandbox members...)."""
        for url in (
            "http://127.0.0.1:9000/settings/",
            "http://localhost:8188/",
            "http://169.254.169.254/latest/meta-data/",
            "http://192.168.1.1/",
        ):
            r = http_client.post("/explore/api/read", json={"url": url})
            data = assert_dict_response(r)
            assert data["ok"] is False, url
            assert "blocked non-public URL" in data["error"], url

    def test_run_requires_query(self, http_client):
        r = http_client.post("/explore/api/run", json={})
        data = assert_dict_response(r)
        assert data["ok"] is False
        assert "query is required" in data["error"]

    def test_answer_requires_query(self, http_client):
        r = http_client.post("/explore/api/answer", json={"query": "", "sources": []})
        data = assert_dict_response(r)
        assert data["ok"] is False

    def test_answer_requires_sources_with_text(self, http_client):
        r = http_client.post(
            "/explore/api/answer",
            json={"query": TEST_PREFIX + "q", "sources": [{"url": "https://example.com"}]},
        )
        data = assert_dict_response(r)
        assert data["ok"] is False
        assert "sources with text" in data["error"]

    def test_vault_compare_requires_query(self, http_client):
        r = http_client.post("/explore/api/vault-compare", json={"query": ""})
        data = assert_dict_response(r)
        assert data["ok"] is False

    def test_save_requires_query(self, http_client):
        r = http_client.post("/explore/api/save", json={"query": "", "answer": "x"})
        data = assert_dict_response(r)
        assert data["ok"] is False

    def test_save_writes_outputs_note(self, http_client):
        """Save lands an AI-authored note under the app's outputs/ folder with
        block-style tags and the source link list."""
        r = http_client.post(
            "/explore/api/save",
            json={
                "query": TEST_PREFIX + "explore save smoke",
                "answer": "A tiny cited answer [1].",
                "sources": [{"url": "https://example.com/a", "title": "Alpha", "site": "example.com"}],
            },
        )
        data = assert_dict_response(r)
        assert data["ok"] is True, data
        note = _vault_root() / data["path"]
        try:
            assert "/outputs/" in data["path"]
            assert note.exists()
            body = note.read_text(encoding="utf-8")
            assert "tags:\n  - explore\n  - web-research" in body
            assert "[Alpha](https://example.com/a)" in body
        finally:
            note.unlink(missing_ok=True)

    @pytest.mark.llm
    def test_search_real_web(self, http_client):
        """Real DDG search — external network, excluded from the fast lane."""
        data = assert_dict_response(http_client.get("/explore/api/search?q=python+language&top=3"))
        assert data["ok"] is True, data
        assert data["results"], "expected at least one web result"
        for s in data["results"]:
            assert s["url"].startswith("http")
            assert {"id", "url", "title", "site"} <= set(s)


@pytest.mark.interactive
class TestExploreUI:
    def test_page_loads(self, page, base_url, page_errors):
        page.goto(base_url + "/explore/")
        page.wait_for_selector("#query", timeout=10000)
        assert page.locator("#run-btn").is_visible()
        assert page.locator("#sources").count() == 1
        assert_no_js_errors(page_errors)

    def test_empty_query_toasts(self, page, base_url, page_errors):
        page.goto(base_url + "/explore/")
        page.wait_for_selector("#run-btn", timeout=10000)
        page.click("#run-btn")
        wait_for_toast(page, "Enter a query")
        assert_no_js_errors(page_errors)

    def test_settings_panel_opens(self, page, base_url, page_errors):
        page.goto(base_url + "/explore/")
        page.wait_for_selector(".btn-settings", timeout=10000)
        page.click(".btn-settings")
        page.wait_for_selector("#explore-settings-panel", state="visible", timeout=3000)
        assert_no_js_errors(page_errors)

    def test_query_param_prefills_input(self, page, base_url, page_errors):
        page.goto(base_url + "/explore/?q=hello+world")
        page.wait_for_selector("#query", timeout=10000)
        assert page.input_value("#query") == "hello world"
        assert_no_js_errors(page_errors)
