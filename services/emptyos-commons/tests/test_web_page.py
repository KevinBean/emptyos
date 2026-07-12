"""The sign-in + feed page serves, and gates on /api/me."""

from __future__ import annotations

from fastapi.testclient import TestClient

from emptyos_commons.app import create_app
from emptyos_commons.repositories import InMemoryCommonsRepository
from emptyos_commons.settings import Settings

ORIGIN = "https://testserver"


def _app():
    settings = Settings(
        database_url="memory", session_hash_secret="x" * 40, public_origin=ORIGIN,
        auth_provider="password", session_secure=True,
    )
    return create_app(settings=settings, repository=InMemoryCommonsRepository())


def test_index_serves_html():
    c = TestClient(_app(), base_url=ORIGIN)
    r = c.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    # no user data baked in; it fetches /api/me + /auth/config client-side
    assert "/api/me" in body and "/auth/config" in body and "Commons" in body


def test_auth_config_reports_password_mode():
    c = TestClient(_app(), base_url=ORIGIN)
    cfg = c.get("/auth/config").json()
    assert cfg["authProvider"] == "password"
    assert cfg["requiresRegistrationSecret"] is False
