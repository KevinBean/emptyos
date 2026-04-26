"""Dogfood — places app.

Month-in-the-life: create place → update metadata → mark visited (twice) →
search → verify categories/stats → delete. No LLM.
"""

import time
import uuid

import pytest

from helpers import TEST_PREFIX


RUN_ID = f"{TEST_PREFIX}places-{uuid.uuid4().hex[:6]}"


def _available(http_client, path: str) -> bool:
    try:
        return http_client.get(path).status_code == 200
    except Exception:
        return False


@pytest.mark.dogfood
class TestPlacesLifecycle:
    state: dict = {}

    @pytest.fixture(autouse=True)
    def _app_required(self, http_client):
        if not _available(http_client, "/places/api/places"):
            pytest.skip("places app not loaded")

    def test_01_create(self, http_client):
        name = f"{RUN_ID} Cafe"
        resp = http_client.post(
            "/places/api/places",
            json={
                "name": name,
                "category": "cafe",
                "address": "Test Street 1",
                "rating": 4,
                "notes": "dogfood test place",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok"), f"create failed: {data}"
        filename = data.get("file")
        assert filename, f"no filename returned: {data}"
        TestPlacesLifecycle.state["file"] = filename
        TestPlacesLifecycle.state["name"] = name
        # Vault watcher has a 1s debounce; allow the index to refresh before
        # any subsequent list/search/category calls.
        time.sleep(1.5)

    def test_02_detail_reflects_fields(self, http_client):
        filename = self.state["file"]
        detail = http_client.get(f"/places/api/places/{filename}").json()
        assert detail.get("name") == self.state["name"], f"name mismatch: {detail}"
        assert detail.get("category") == "cafe"

    def test_03_update_metadata(self, http_client):
        filename = self.state["file"]
        resp = http_client.put(
            f"/places/api/places/{filename}",
            json={"rating": 5, "notes": "upgraded to 5 stars"},
        )
        assert resp.status_code == 200
        assert resp.json().get("ok"), resp.text[:200]

    def test_04_log_visit(self, http_client):
        filename = self.state["file"]
        resp = http_client.post(
            f"/places/api/places/{filename}/visit",
            json={"note": "first visit"},
        )
        assert resp.status_code == 200
        assert resp.json().get("ok"), resp.text[:200]

    def test_05_categories_include_cafe(self, http_client):
        cats = http_client.get("/places/api/categories").json()
        names = [c.get("category", "").lower() for c in cats if isinstance(c, dict)]
        assert "cafe" in names, f"cafe missing from categories: {cats}"

    def test_06_search_finds_place(self, http_client):
        results = http_client.get(f"/places/api/search?q={RUN_ID}").json()
        assert isinstance(results, list)
        assert any(RUN_ID in str(p.get("name", "")) for p in results), (
            f"search for {RUN_ID} found nothing"
        )

    def test_07_stats_include_place(self, http_client):
        stats = http_client.get("/places/api/stats").json()
        assert stats.get("total", 0) >= 1

    def test_08_list_filter_by_category(self, http_client):
        listing = http_client.get("/places/api/places?category=cafe").json()
        assert isinstance(listing, list)
        assert any(RUN_ID in str(p.get("name", "")) for p in listing), (
            "category filter didn't include our place"
        )

    def test_09_delete_and_verify_gone(self, http_client):
        filename = self.state["file"]
        resp = http_client.delete(f"/places/api/places/{filename}")
        assert resp.status_code == 200
        time.sleep(0.2)
        detail = http_client.get(f"/places/api/places/{filename}").json()
        assert detail.get("error") == "not found", f"place still there: {detail}"


@pytest.fixture(scope="module", autouse=True)
def _cleanup(http_client):
    yield
    try:
        listing = http_client.get("/places/api/places").json()
        for p in listing if isinstance(listing, list) else []:
            if RUN_ID in str(p.get("name", "")):
                fname = p.get("file") or p.get("filename")
                if fname:
                    http_client.delete(f"/places/api/places/{fname}")
    except Exception:
        pass
