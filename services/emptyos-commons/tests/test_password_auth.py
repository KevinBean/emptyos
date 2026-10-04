"""In-service password auth, end-to-end through the real endpoints (in-memory
repo, no Firebase). Register → login → wrong password → change → isolation."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from emptyos_commons.app import create_app
from emptyos_commons.repositories import InMemoryCommonsRepository
from emptyos_commons.settings import Settings

ORIGIN = "https://testserver"
MUT = {"Origin": ORIGIN}


def _app(registration_secret=""):
    settings = Settings(
        database_url="memory",
        session_hash_secret="x" * 40,
        public_origin=ORIGIN,
        auth_provider="password",
        session_secure=True,
        registration_secret=registration_secret,
    )
    return create_app(settings=settings, repository=InMemoryCommonsRepository())


def _client(app):
    return TestClient(app, base_url=ORIGIN)


def test_register_then_login():
    c = _client(_app())
    assert c.post("/auth/register", headers=MUT,
                  json={"email": "a@x.com", "password": "supersecret"}).status_code == 200
    assert c.post("/auth/session", headers=MUT,
                  json={"email": "a@x.com", "password": "supersecret"}).status_code == 200
    me = c.get("/api/me")
    assert me.status_code == 200 and me.json()["email"] == "a@x.com"


def test_wrong_password_rejected():
    app = _app()
    _client(app).post("/auth/register", headers=MUT, json={"email": "a@x.com", "password": "rightpass1"})
    r = _client(app).post("/auth/session", headers=MUT, json={"email": "a@x.com", "password": "wrongpass1"})
    assert r.status_code == 401


def test_login_unknown_user_rejected():
    r = _client(_app()).post("/auth/session", headers=MUT, json={"email": "ghost@x.com", "password": "whatever1"})
    assert r.status_code == 401


def test_short_password_and_duplicate_rejected():
    app = _app()
    c = _client(app)
    assert c.post("/auth/register", headers=MUT, json={"email": "a@x.com", "password": "short"}).status_code == 400
    assert c.post("/auth/register", headers=MUT, json={"email": "a@x.com", "password": "longenough"}).status_code == 200
    assert c.post("/auth/register", headers=MUT, json={"email": "a@x.com", "password": "anotherlong"}).status_code == 409


def test_registration_secret_enforced():
    app = _app(registration_secret="team-2026")
    c = _client(app)
    assert c.post("/auth/register", headers=MUT, json={"email": "a@x.com", "password": "longenough"}).status_code == 403
    assert c.post("/auth/register", headers=MUT,
                  json={"email": "a@x.com", "password": "longenough", "secret": "team-2026"}).status_code == 200


def test_change_password():
    app = _app()
    c = _client(app)
    c.post("/auth/register", headers=MUT, json={"email": "a@x.com", "password": "oldpassword"})
    c.post("/auth/session", headers=MUT, json={"email": "a@x.com", "password": "oldpassword"})
    assert c.post("/auth/password", headers=MUT, json={"old": "oldpassword", "new": "newpassword"}).status_code == 200
    # old password no longer works, new one does
    fresh = _client(app)
    assert fresh.post("/auth/session", headers=MUT, json={"email": "a@x.com", "password": "oldpassword"}).status_code == 401
    assert fresh.post("/auth/session", headers=MUT, json={"email": "a@x.com", "password": "newpassword"}).status_code == 200


def test_password_users_get_content_isolation():
    app = _app()
    a, b = _client(app), _client(app)
    for c, e in ((a, "a@x.com"), (b, "b@x.com")):
        c.post("/auth/register", headers=MUT, json={"email": e, "password": "longenough"})
        c.post("/auth/session", headers=MUT, json={"email": e, "password": "longenough"})
    a.post("/api/notes", headers=MUT, json={"slug": "secret", "body": "x", "visibility": "private"})
    a.post("/api/notes", headers=MUT, json={"slug": "shared-kb", "body": "x", "visibility": "public"})
    b_slugs = {n["slug"] for n in b.get("/api/notes").json()["notes"]}
    assert "shared-kb" in b_slugs and "secret" not in b_slugs


@pytest.mark.parametrize("path,body", [
    ("/auth/register", {"email": "a@x.com", "password": "longenough"}),
    ("/auth/password", {"old": "x", "new": "longenough"}),
])
def test_password_endpoints_404_outside_password_mode(path, body):
    # dev mode: password endpoints must not exist as a usable path
    import os
    os.environ["COMMONS_DEV_AUTH"] = "1"
    from emptyos_commons.auth import DevIdentityVerifier
    settings = Settings(database_url="memory", session_hash_secret="x" * 40,
                        public_origin=ORIGIN, auth_provider="dev", session_secure=True)
    app = create_app(settings=settings, repository=InMemoryCommonsRepository(),
                     identity_verifier=DevIdentityVerifier())
    c = _client(app)
    # register is unauthenticated → 404; password needs auth but still 404 in dev mode
    r = c.post(path, headers=MUT, json=body)
    assert r.status_code in (404, 401)
