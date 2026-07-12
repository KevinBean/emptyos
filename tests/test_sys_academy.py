"""System tests for the English Academy learner dashboard."""

import tomllib
from pathlib import Path


def test_academy_dashboard_shape(http_client):
    response = http_client.get("/academy/api/dashboard")
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {
        "preferences",
        "onboarding_required",
        "courses",
        "reviews",
        "next_action",
        "degraded",
    }
    assert set(data["courses"]) == {"available", "in_progress", "completed"}
    assert set(data["reviews"]) == {"course_due", "vocabulary_due", "saved_words"}
    assert data["next_action"]["href"]
    assert data["next_action"]["label"]


def test_academy_preferences_shape(http_client):
    response = http_client.get("/academy/api/preferences")
    assert response.status_code == 200
    assert set(response.json()) == {
        "display_name",
        "level",
        "goal",
        "daily_minutes",
        "onboarding_completed",
    }


def test_academy_rejects_invalid_level(http_client):
    response = http_client.post("/academy/api/preferences", json={"level": "expert"})
    assert response.status_code == 200
    assert response.json() == {"error": "Choose a supported English level."}


def test_academy_rejects_invalid_daily_minutes(http_client):
    response = http_client.post("/academy/api/preferences", json={"daily_minutes": 181})
    assert response.status_code == 200
    assert response.json() == {
        "error": "Daily study time must be between 5 and 180 minutes."
    }


def test_academy_saves_preferences(http_client):
    original = http_client.get("/academy/api/preferences").json()
    try:
        response = http_client.post(
            "/academy/api/preferences",
            json={
                "display_name": "Test Learner",
                "level": "intermediate",
                "goal": "conversation",
                "daily_minutes": 25,
            },
        )
        assert response.status_code == 200
        saved = response.json()["preferences"]
        assert saved == {
            "display_name": "Test Learner",
            "level": "intermediate",
            "goal": "conversation",
            "daily_minutes": 25,
            "onboarding_completed": True,
        }
        assert http_client.get("/academy/api/preferences").json() == saved
    finally:
        http_client.post("/academy/api/preferences", json=original)


def test_academy_page_is_plan_editor(app_page, page_errors):
    # Post-consolidation, /academy/ is the study-plan editor; the browsable
    # member grid + coach hero live in the english-academy Workspace.
    page = app_page("academy")
    assert page.locator("h1").count() == 1
    assert page.locator("#preferences-form").count() == 1
    # No practice-mode grid here anymore.
    assert page.locator("#practice-modes").count() == 0
    # Links to the Workspace home.
    assert page.locator("a[href*='workspaces']").count() >= 1
    assert page_errors == []


def test_academy_is_in_english_learning_release_tier():
    release_path = Path(__file__).resolve().parent.parent / "release.toml"
    with release_path.open("rb") as handle:
        release = tomllib.load(handle)
    assert "academy" in release["tiers"]["english-learning"]["apps"]
