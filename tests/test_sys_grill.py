"""System app tests: Grill — recipe-driven Socratic elicitation.

Covers the session lifecycle (start → answer → answer) and recipe discovery.
Skips the LLM-hitting `/sessions/{sid}/finish` endpoint — that's exercised
manually or via the dogfood suite when needed.
"""

from __future__ import annotations

import pytest

from helpers import TEST_PREFIX, assert_dict_response, assert_list_response, assert_ok
from page_helpers import assert_no_js_errors, wait_briefly


@pytest.mark.api
class TestGrillAPI:
    def test_list_recipes_returns_new_app(self, http_client):
        data = assert_dict_response(
            http_client.get("/grill/api/recipes"),
            required_keys=["recipes"],
        )
        recipes = data["recipes"]
        assert isinstance(recipes, list)
        ids = [r.get("id") for r in recipes]
        assert "new-app" in ids, f"new-app recipe missing from {ids}"

    def test_list_recipe_shape(self, http_client):
        data = assert_dict_response(http_client.get("/grill/api/recipes"))
        recipes = data["recipes"]
        for r in recipes:
            assert "id" in r
            assert "title" in r
            assert "question_count" in r
            assert isinstance(r["question_count"], int)

    def test_get_recipe_returns_questions(self, http_client):
        data = assert_dict_response(http_client.get("/grill/api/recipes/new-app"))
        # Recipe TOML structure: { recipe: {...}, questions: [...] }
        assert "recipe" in data
        assert data["recipe"].get("id") == "new-app"
        assert "questions" in data
        assert isinstance(data["questions"], list)
        assert len(data["questions"]) >= 8

    def test_get_recipe_questions_have_why(self, http_client):
        """Every question must carry a `why` line — that's the grill's job."""
        data = assert_dict_response(http_client.get("/grill/api/recipes/new-app"))
        for q in data["questions"]:
            assert "key" in q
            assert "prompt" in q
            assert "why" in q and q["why"], f"Question {q.get('key')} missing 'why'"

    def test_get_unknown_recipe_returns_error(self, http_client):
        data = http_client.get("/grill/api/recipes/no-such-recipe").json()
        assert "error" in data

    def test_start_session_returns_recipe_and_id(self, http_client):
        resp = http_client.post(
            "/grill/api/sessions",
            json={"recipe_id": "new-app"},
        )
        data = assert_ok(resp)
        assert "session_id" in data
        assert isinstance(data["session_id"], str) and len(data["session_id"]) > 0
        assert "recipe" in data
        assert data["recipe"]["recipe"]["id"] == "new-app"

    def test_start_session_bad_recipe(self, http_client):
        resp = http_client.post(
            "/grill/api/sessions",
            json={"recipe_id": "no-such-recipe"},
        )
        data = resp.json()
        assert "error" in data

    def test_answer_stores_value(self, http_client):
        # Start a session, post an answer, count goes up.
        sess = assert_ok(http_client.post(
            "/grill/api/sessions",
            json={"recipe_id": "new-app"},
        ))
        sid = sess["session_id"]
        resp = http_client.post(
            f"/grill/api/sessions/{sid}/answer",
            json={"key": "verb", "value": f"{TEST_PREFIX}capture-and-route"},
        )
        data = assert_ok(resp)
        assert data.get("ok") is True
        assert data.get("answered") == 1

    def test_multiple_answers_accumulate(self, http_client):
        sess = assert_ok(http_client.post(
            "/grill/api/sessions",
            json={"recipe_id": "new-app"},
        ))
        sid = sess["session_id"]
        for i, (key, value) in enumerate([
            ("shape_gate", "trivial CRUD"),
            ("verb", f"{TEST_PREFIX}log"),
            ("data_shape", "markdown vault notes"),
        ]):
            resp = http_client.post(
                f"/grill/api/sessions/{sid}/answer",
                json={"key": key, "value": value},
            )
            data = assert_ok(resp)
            assert data.get("answered") == i + 1

    def test_answer_unknown_session(self, http_client):
        resp = http_client.post(
            "/grill/api/sessions/no-such-session/answer",
            json={"key": "verb", "value": "x"},
        )
        data = resp.json()
        assert "error" in data

    def test_answer_requires_key(self, http_client):
        sess = assert_ok(http_client.post(
            "/grill/api/sessions",
            json={"recipe_id": "new-app"},
        ))
        sid = sess["session_id"]
        resp = http_client.post(
            f"/grill/api/sessions/{sid}/answer",
            json={"key": "", "value": "x"},
        )
        data = resp.json()
        assert "error" in data


@pytest.mark.interactive
class TestGrillUI:
    def test_page_loads_without_js_errors(self, page, base_url, page_errors):
        page.goto(base_url + "/grill/")
        wait_briefly(page)
        assert_no_js_errors(page_errors)

    def test_recipe_card_renders(self, page, base_url):
        page.goto(base_url + "/grill/")
        wait_briefly(page)
        # The new-app recipe should appear as a card.
        card = page.locator(".recipe-card").first
        card.wait_for(state="visible", timeout=4000)
        assert "New EmptyOS App" in card.inner_text()

    def test_picking_recipe_shows_wizard(self, page, base_url):
        page.goto(base_url + "/grill/")
        wait_briefly(page)
        page.locator(".recipe-card").first.click()
        # Wizard view becomes visible, progress pips render.
        page.locator(".progress .pip").first.wait_for(state="visible", timeout=4000)
        # First question prompt is the gate question.
        assert page.locator(".q-prompt").first.is_visible()
        assert page.locator(".q-why").first.is_visible()
