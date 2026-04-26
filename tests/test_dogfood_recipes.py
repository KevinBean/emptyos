"""Dogfood — recipes app.

Month-in-the-life: create recipe → update → cook twice → favorite → rate
→ verify stats/history → delete. No LLM.
"""

import time
import uuid

import pytest

from helpers import TEST_PREFIX


RUN_ID = f"{TEST_PREFIX}recipes-{uuid.uuid4().hex[:6]}"


def _available(http_client, path: str) -> bool:
    try:
        return http_client.get(path).status_code == 200
    except Exception:
        return False


@pytest.mark.dogfood
class TestRecipesLifecycle:
    state: dict = {}

    @pytest.fixture(autouse=True)
    def _app_required(self, http_client):
        # Skips every test in the class when the app isn't loaded (e.g. CI
        # without personal apps). Without this, test_01 would fail with an
        # HTTP error and later tests cascade with KeyError on shared state.
        if not _available(http_client, "/recipes/api/recipes"):
            pytest.skip("recipes app not loaded")

    def test_01_create(self, http_client):
        name = f"{RUN_ID}-pancakes"
        resp = http_client.post(
            "/recipes/api/recipes",
            json={
                "name": name,
                "description": "Weekend pancakes",
                "prep_min": 10,
                "cook_min": 20,
                "servings": 4,
                "difficulty": "easy",
                "tags": ["breakfast", "test"],
                "ingredients": [
                    {"item": "flour", "amount": "200g"},
                    {"item": "eggs", "amount": "2"},
                    {"item": "milk", "amount": "300ml"},
                ],
                "steps": ["mix", "cook", "serve"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("id"), f"no id returned: {data}"
        TestRecipesLifecycle.state["id"] = data["id"]
        TestRecipesLifecycle.state["name"] = name
        # Let the vault watcher's 1s debounce refresh the VaultIndex before
        # any list/search assertions hit a stale cache.
        time.sleep(1.5)

    def test_02_list_contains_it(self, http_client):
        listing = http_client.get("/recipes/api/recipes").json()
        rid = self.state["id"]
        found = next((r for r in listing if r.get("id") == rid), None)
        assert found, f"recipe {rid} missing from list"
        assert found["name"] == self.state["name"]

    def test_03_update_metadata(self, http_client):
        rid = self.state["id"]
        resp = http_client.put(
            f"/recipes/api/recipes/{rid}",
            json={"servings": 6, "difficulty": "medium", "description": "Updated"},
        )
        assert resp.status_code == 200
        got = resp.json()
        assert got.get("servings") == 6
        assert got.get("difficulty") == "medium"

    def test_04_cook_twice_updates_counter(self, http_client):
        rid = self.state["id"]
        for _ in range(2):
            r = http_client.post(f"/recipes/api/recipes/{rid}/cook")
            assert r.status_code == 200
        detail = http_client.get(f"/recipes/api/recipes/{rid}").json()
        assert detail.get("times_cooked", 0) >= 2, (
            f"cook counter did not increment: {detail.get('times_cooked')}"
        )
        assert detail.get("last_cooked"), "last_cooked not set after cook"

    def test_05_toggle_favorite(self, http_client):
        rid = self.state["id"]
        http_client.post(f"/recipes/api/recipes/{rid}/favorite")
        detail = http_client.get(f"/recipes/api/recipes/{rid}").json()
        # favorite is toggled — may now be True
        assert "favorite" in detail

    def test_06_rate(self, http_client):
        rid = self.state["id"]
        resp = http_client.put(
            f"/recipes/api/recipes/{rid}", json={"rating": 5}
        )
        assert resp.status_code == 200
        assert resp.json().get("rating") == 5

    def test_07_stats_include_our_recipe(self, http_client):
        stats = http_client.get("/recipes/api/stats").json()
        assert isinstance(stats, dict)
        # total must at least reflect us
        assert stats.get("total", 0) >= 1

    def test_08_tags_include_breakfast(self, http_client):
        tags = http_client.get("/recipes/api/tags").json()
        # tags endpoint returns list of {tag, count} or similar — we added "test"
        blob = str(tags)
        assert "test" in blob or "breakfast" in blob

    def test_09_delete_and_verify_gone(self, http_client):
        rid = self.state["id"]
        resp = http_client.delete(f"/recipes/api/recipes/{rid}")
        assert resp.status_code == 200
        time.sleep(0.2)
        got = http_client.get(f"/recipes/api/recipes/{rid}").json()
        assert got.get("error") == "not found", f"recipe still there: {got}"


@pytest.fixture(scope="module", autouse=True)
def _cleanup(http_client):
    yield
    try:
        listing = http_client.get("/recipes/api/recipes").json()
        for r in listing if isinstance(listing, list) else []:
            if RUN_ID in str(r.get("name", "")):
                http_client.delete(f"/recipes/api/recipes/{r['id']}")
    except Exception:
        pass
