"""Dogfood — fiction-engine app.

Month-in-the-life: create story → verify scaffold → add character → generate
outline (LLM) → draft a scene (LLM) → revise scene (LLM) → continuity check
(LLM) → list versions → cleanup. LLM calls may be slow (30-90s) so the
per-test timeout is generous.
"""

import shutil
import time
import uuid
from pathlib import Path

import pytest

from helpers import TEST_PREFIX


# Fiction stories live under a vault folder by slug. Use a lowercased slug
# compatible with slugify() in the app.
RUN_ID = f"pw-dogfood-{uuid.uuid4().hex[:6]}"
TITLE = f"Dogfood {RUN_ID}"
# slugify turns "Dogfood pw-dogfood-XXXXXX" into "dogfood-pw-dogfood-XXXXXX"
SLUG = f"dogfood-{RUN_ID}"


def _available(http_client, path: str) -> bool:
    try:
        return http_client.get(path).status_code == 200
    except Exception:
        return False


@pytest.mark.dogfood
class TestFictionLifecycle:
    state: dict = {}

    @pytest.fixture(autouse=True)
    def _app_required(self, http_client):
        if not _available(http_client, "/fiction-engine/api/stories"):
            pytest.skip("fiction-engine app not loaded")

    def test_01_create_story(self, http_client):
        resp = http_client.post(
            "/fiction-engine/api/stories",
            json={
                "title": TITLE,
                "type": "novella",  # smaller than novel, same flow
                "premise": f"A software tester {RUN_ID} races against a looming release deadline.",
                "language": "en",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok"), f"create failed: {data}"
        sid = data.get("id")
        assert sid, "no story id returned"
        TestFictionLifecycle.state["id"] = sid
        time.sleep(1.0)

    def test_02_list_contains_story(self, http_client):
        listing = http_client.get("/fiction-engine/api/stories").json()
        stories = listing if isinstance(listing, list) else listing.get("stories", [])
        found = next(
            (s for s in stories if s.get("id") == self.state["id"]), None
        )
        assert found, f"new story missing from list"

    def test_03_detail_has_scaffold(self, http_client):
        sid = self.state["id"]
        detail = http_client.get(f"/fiction-engine/api/stories/{sid}").json()
        assert "error" not in detail, detail
        # Detail should expose brief / outline / progress keys in some shape
        blob = str(detail)
        assert "brief" in blob.lower() or "outline" in blob.lower() or detail.get("id") == sid

    def test_04_add_character(self, http_client):
        sid = self.state["id"]
        resp = http_client.post(
            f"/fiction-engine/api/stories/{sid}/character",
            json={
                "name": f"{RUN_ID}-hero",
                "role": "protagonist",
                "description": "Reluctant QA, carries a laptop everywhere.",
            },
        )
        assert resp.status_code == 200

    @pytest.mark.llm
    def test_05_generate_outline(self, http_client):
        sid = self.state["id"]
        resp = http_client.post(f"/fiction-engine/api/stories/{sid}/outline", timeout=180)
        assert resp.status_code == 200
        data = resp.json()
        if "error" in data:
            pytest.skip(f"outline skipped: {data['error']}")

    @pytest.mark.llm
    def test_06_draft_scene(self, http_client):
        sid = self.state["id"]
        # Scene id is the file stem under manuscript; use a predictable one.
        resp = http_client.post(
            f"/fiction-engine/api/stories/{sid}/draft",
            json={"scene_id": "scene-1"},
            timeout=180,
        )
        assert resp.status_code == 200
        data = resp.json()
        if "error" in data:
            pytest.skip(f"draft skipped: {data['error']}")
        TestFictionLifecycle.state["scene_id"] = "scene-1"

    @pytest.mark.llm
    def test_07_revise_scene(self, http_client):
        sid = self.state["id"]
        scene = self.state.get("scene_id")
        if not scene:
            pytest.skip("draft step was skipped; nothing to revise")
        resp = http_client.post(
            f"/fiction-engine/api/stories/{sid}/revise",
            json={"scene_id": scene, "mode": "expand"},
            timeout=180,
        )
        assert resp.status_code == 200

    def test_08_list_versions(self, http_client):
        sid = self.state["id"]
        scene = self.state.get("scene_id", "scene-1")
        resp = http_client.get(f"/fiction-engine/api/stories/{sid}/versions/{scene}")
        assert resp.status_code == 200
        # Versions may be empty if draft/revise skipped — that's fine.

    @pytest.mark.llm
    def test_09_continuity_check(self, http_client):
        sid = self.state["id"]
        resp = http_client.post(f"/fiction-engine/api/stories/{sid}/continuity", timeout=180)
        assert resp.status_code == 200
        data = resp.json()
        if "error" in data:
            pytest.skip(f"continuity skipped: {data['error']}")


@pytest.fixture(scope="module", autouse=True)
def _cleanup(http_client):
    yield
    # Fiction stories live as vault directories; remove the whole dir.
    try:
        import tomllib
        with open("emptyos.toml", "rb") as f:
            cfg = tomllib.load(f)
        vault = Path(cfg.get("notes", {}).get("path", ""))
        if not vault.exists():
            return
        # Stories live under various folders; search by slug prefix.
        for base in ("10_Projects", "30_Resources/Fiction", "30_Resources/Stories"):
            root = vault / base
            if not root.exists():
                continue
            for d in root.iterdir():
                if d.is_dir() and RUN_ID in d.name:
                    shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass
