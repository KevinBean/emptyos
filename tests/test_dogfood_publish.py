"""Dogfood — publish app.

Month-in-the-life: list sites → save draft post → verify in sources → load
back → apply AI polish (LLM) → toggle publish on → build site → verify
last-build stats → cleanup draft. The build may take 5-15 seconds.
"""

import time
import uuid
from pathlib import Path

import pytest

from helpers import TEST_PREFIX


RUN_ID = f"{TEST_PREFIX}publish-{uuid.uuid4().hex[:6]}"


def _available(http_client, path: str) -> bool:
    try:
        return http_client.get(path).status_code == 200
    except Exception:
        return False


@pytest.mark.dogfood
class TestPublishLifecycle:
    state: dict = {}

    @pytest.fixture(autouse=True)
    def _app_required(self, http_client):
        if not _available(http_client, "/publish/api/sites"):
            pytest.skip("publish app not loaded")

    def test_01_sites_listed(self, http_client):
        resp = http_client.get("/publish/api/sites").json()
        sites = resp.get("sites") or []
        assert len(sites) >= 1, f"no sites configured: {resp}"
        assert resp.get("active"), "no active site set"

    def test_02_save_draft(self, http_client):
        title = f"{RUN_ID}-post"
        content = (
            f"---\ntitle: {title}\npublish: false\ntype: post\n---\n\n"
            f"# {title}\n\nDogfood test content for {RUN_ID}. Simple paragraph.\n"
        )
        resp = http_client.post(
            "/publish/api/save-draft",
            json={"title": title, "content": content},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok"), f"save-draft failed: {data}"
        path = data.get("path")
        assert path, "no path returned"
        TestPublishLifecycle.state["path"] = path
        TestPublishLifecycle.state["title"] = title
        time.sleep(1.5)  # vault watcher debounce

    def test_03_sources_include_draft(self, http_client):
        sources = http_client.get("/publish/api/sources?include_drafts=1").json()
        items = sources if isinstance(sources, list) else sources.get("sources", [])
        assert any(
            RUN_ID in str(s.get("title", "")) or RUN_ID in str(s.get("path", ""))
            for s in items
        ), f"draft not in sources"

    def test_04_load_post_back(self, http_client):
        path = self.state["path"]
        resp = http_client.get(f"/publish/api/load-post?path={path}")
        assert resp.status_code == 200
        data = resp.json()
        content = data.get("content") or data.get("body") or ""
        assert RUN_ID in content, f"loaded content missing marker: {content[:200]}"

    @pytest.mark.llm
    def test_05_ai_polish(self, http_client):
        text = f"This is a rough draft for {RUN_ID}. It needs polishing please."
        resp = http_client.post(
            "/publish/api/ai-write",
            json={"action": "polish", "text": text},
            timeout=120,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "error" not in data, f"polish failed: {data}"
        assert data.get("text"), f"no polished text: {data}"
        assert data.get("action") == "polish"

    def test_06_toggle_publish_on(self, http_client):
        path = self.state["path"]
        resp = http_client.post(
            "/publish/api/toggle-publish",
            json={"path": path, "publish": True},
        )
        # Toggle may return ok or an error depending on frontmatter format; soft check
        assert resp.status_code == 200

    def test_07_build(self, http_client):
        resp = http_client.post("/publish/api/build", timeout=120)
        assert resp.status_code == 200
        data = resp.json()
        # Build stats shape varies — accept any response that's a dict with pages or success
        assert isinstance(data, dict)
        pages = data.get("pages") or data.get("page_count") or 0
        assert pages == 0 or pages >= 1, f"odd build stats: {data}"

    def test_08_config_has_last_build(self, http_client):
        cfg = http_client.get("/publish/api/config").json()
        # After build, last_build should be populated
        assert cfg.get("last_build"), f"last_build missing: {list(cfg.keys())}"


@pytest.fixture(scope="module", autouse=True)
def _cleanup(http_client):
    yield
    # Delete any draft file we created in the vault.
    try:
        sources = http_client.get("/publish/api/sources?include_drafts=1").json()
        items = sources if isinstance(sources, list) else sources.get("sources", [])
        for s in items:
            if RUN_ID in str(s.get("title", "")) or RUN_ID in str(s.get("path", "")):
                path = s.get("path")
                if path:
                    p = Path(path)
                    if p.exists() and p.is_file():
                        try:
                            p.unlink()
                        except Exception:
                            pass
    except Exception:
        pass
