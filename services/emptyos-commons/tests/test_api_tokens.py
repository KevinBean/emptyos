"""Durable machine API tokens: mint (session) → use (Bearer) → revoke."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from emptyos_commons.app import create_app
from emptyos_commons.repositories import InMemoryCommonsRepository, SqliteCommonsRepository
from emptyos_commons.settings import Settings

ORIGIN = "https://testserver"
MUT = {"Origin": ORIGIN}


@pytest.fixture(params=["memory", "sqlite"])
def app(request, tmp_path):
    repo = InMemoryCommonsRepository() if request.param == "memory" \
        else SqliteCommonsRepository(str(tmp_path / "c.db"))
    settings = Settings(database_url="memory", session_hash_secret="x" * 40,
                        public_origin=ORIGIN, auth_provider="password", session_secure=True)
    return create_app(settings=settings, repository=repo)


def _signed_in(app, email="a@x.com"):
    c = TestClient(app, base_url=ORIGIN)
    c.post("/auth/register", headers=MUT, json={"email": email, "password": "longenough"})
    c.post("/auth/session", headers=MUT, json={"email": email, "password": "longenough"})
    return c


def test_token_mint_use_revoke(app):
    owner = _signed_in(app)
    # mint
    r = owner.post("/api/tokens", headers=MUT, json={"label": "laptop"})
    assert r.status_code == 200
    tok = r.json()["token"]
    tid = r.json()["id"]
    assert tok.startswith("ct_")

    # a cookie-less machine client authenticates with the Bearer token
    machine = TestClient(app, base_url=ORIGIN)
    me = machine.get("/api/me", headers={"Authorization": f"Bearer {tok}"})
    assert me.status_code == 200 and me.json()["email"] == "a@x.com"

    # and can publish as the owner
    pub = machine.post("/api/notes", headers={**MUT, "Authorization": f"Bearer {tok}"},
                       json={"slug": "from-daemon", "body": "x", "visibility": "public"})
    assert pub.status_code == 200
    assert "from-daemon" in {n["slug"] for n in owner.get("/api/notes").json()["notes"]}

    # listed for the owner
    assert any(t["id"] == tid and not t["revoked"] for t in owner.get("/api/tokens").json()["tokens"])

    # revoke → Bearer no longer works
    assert owner.request("DELETE", f"/api/tokens/{tid}", headers=MUT).status_code == 200
    assert machine.get("/api/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_garbage_bearer_rejected(app):
    _signed_in(app)
    machine = TestClient(app, base_url=ORIGIN)
    assert machine.get("/api/me", headers={"Authorization": "Bearer ct_not-a-real-token"}).status_code == 401
    assert machine.get("/api/notes").status_code == 401  # no auth at all


def test_cannot_revoke_another_users_token(app):
    a = _signed_in(app, "a@x.com")
    b = _signed_in(app, "b@x.com")
    tid = a.post("/api/tokens", headers=MUT, json={"label": "a-token"}).json()["id"]
    # B cannot revoke A's token (scoped to own user) -> 404, and A's still works
    assert b.request("DELETE", f"/api/tokens/{tid}", headers=MUT).status_code == 404
    assert any(t["id"] == tid for t in a.get("/api/tokens").json()["tokens"])
