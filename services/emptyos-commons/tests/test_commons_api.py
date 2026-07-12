"""End-to-end content + ACL flow through the real FastAPI endpoints, using the
in-memory repo + dev auth (no Postgres, no Firebase). Proves the service applies
the same visibility rule as test_visibility, across two distinct human users."""

from __future__ import annotations

import os

import pytest

os.environ["COMMONS_DEV_AUTH"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from emptyos_commons.app import create_app  # noqa: E402
from emptyos_commons.auth import DevIdentityVerifier  # noqa: E402
from emptyos_commons.repositories import (  # noqa: E402
    InMemoryCommonsRepository,
    SqliteCommonsRepository,
)
from emptyos_commons.settings import Settings  # noqa: E402

ORIGIN = "https://testserver"
MUT = {"Origin": ORIGIN}


# Run every API flow against BOTH backends so the trusted-team SQLite store is
# held to the same isolation guarantees as the in-memory one.
@pytest.fixture(params=["memory", "sqlite"])
def repo(request, tmp_path):
    if request.param == "memory":
        return InMemoryCommonsRepository()
    return SqliteCommonsRepository(str(tmp_path / "commons.db"))


@pytest.fixture
def app(repo):
    settings = Settings(
        database_url="memory",
        session_hash_secret="x" * 40,
        public_origin=ORIGIN,
        auth_provider="dev",
        session_secure=True,
    )
    return create_app(
        settings=settings,
        repository=repo,
        identity_verifier=DevIdentityVerifier(),
    )


def _login(app, subject, email):
    client = TestClient(app, base_url=ORIGIN)
    r = client.post("/auth/session", headers={**MUT, "Authorization": f"Bearer {subject}:{email}"})
    assert r.status_code == 200, r.text
    return client


def _slugs(client):
    r = client.get("/api/notes")
    assert r.status_code == 200, r.text
    return {n["slug"] for n in r.json()["notes"]}


def test_private_note_invisible_to_others(app):
    a = _login(app, "userA", "a@x.com")
    b = _login(app, "userB", "b@x.com")
    r = a.post("/api/notes", headers=MUT, json={"slug": "secret", "title": "S", "body": "x", "visibility": "private"})
    assert r.status_code == 200, r.text
    assert "secret" in _slugs(a)
    assert "secret" not in _slugs(b)


def test_public_note_visible_to_all(app):
    a = _login(app, "userA", "a@x.com")
    b = _login(app, "userB", "b@x.com")
    a.post("/api/notes", headers=MUT, json={"slug": "kb-ohms-law", "body": "V=IR", "visibility": "public"})
    assert "kb-ohms-law" in _slugs(b)


def test_selective_share_visible_only_to_grantee(app):
    a = _login(app, "userA", "a@x.com")
    b = _login(app, "userB", "b@x.com")
    c = _login(app, "userC", "c@x.com")
    # publish private, then share with B by email
    r = a.post("/api/notes", headers=MUT, json={"slug": "draft", "body": "x", "visibility": "private"})
    note_id = r.json()["note"]["id"]
    r = a.post(f"/api/notes/{note_id}/share", headers=MUT, json={"principal": "b@x.com"})
    assert r.status_code == 200, r.text
    assert "draft" in _slugs(b)       # grantee sees it
    assert "draft" not in _slugs(c)   # non-grantee does not
    # sharing flips visibility private -> shared
    assert a.get(f"/api/notes/{note_id}").json()["note"]["visibility"] == "shared"


def test_inline_share_on_publish(app):
    a = _login(app, "userA", "a@x.com")
    b = _login(app, "userB", "b@x.com")
    r = a.post("/api/notes", headers=MUT, json={
        "slug": "plan", "body": "x", "visibility": "private", "share_with": ["b@x.com", "ghost@x.com"],
    })
    body = r.json()
    assert body["shared"] == ["b@x.com"]
    assert body["unresolved"] == ["ghost@x.com"]
    assert "plan" in _slugs(b)


def test_non_owner_cannot_delete_or_share(app):
    a = _login(app, "userA", "a@x.com")
    b = _login(app, "userB", "b@x.com")
    r = a.post("/api/notes", headers=MUT, json={"slug": "mine", "body": "x", "visibility": "public"})
    note_id = r.json()["note"]["id"]
    assert b.delete(f"/api/notes/{note_id}", headers=MUT).status_code == 403
    assert b.post(f"/api/notes/{note_id}/share", headers=MUT, json={"principal": "b@x.com"}).status_code == 403


def test_revoke_removes_for_everyone(app):
    a = _login(app, "userA", "a@x.com")
    b = _login(app, "userB", "b@x.com")
    r = a.post("/api/notes", headers=MUT, json={"slug": "temp", "body": "x", "visibility": "public"})
    note_id = r.json()["note"]["id"]
    assert "temp" in _slugs(b)
    assert a.delete(f"/api/notes/{note_id}", headers=MUT).status_code == 200
    assert "temp" not in _slugs(b)
    assert "temp" not in _slugs(a)


def test_get_hidden_note_is_404_not_403(app):
    a = _login(app, "userA", "a@x.com")
    b = _login(app, "userB", "b@x.com")
    r = a.post("/api/notes", headers=MUT, json={"slug": "hidden", "body": "x", "visibility": "private"})
    note_id = r.json()["note"]["id"]
    # B must not even learn the note exists
    assert b.get(f"/api/notes/{note_id}").status_code == 404


def test_unauthenticated_rejected(app):
    client = TestClient(app, base_url=ORIGIN)
    assert client.get("/api/notes").status_code == 401


def test_origin_enforced_on_mutations(app):
    a = _login(app, "userA", "a@x.com")
    # wrong Origin on a mutating request -> 403
    r = a.post("/api/notes", headers={"Origin": "https://evil.example"}, json={"slug": "x", "body": "y"})
    assert r.status_code == 403


def _make_note(client, slug="draft", body="orig", visibility="private"):
    r = client.post(
        "/api/notes", headers=MUT,
        json={"slug": slug, "body": body, "visibility": visibility},
    )
    assert r.status_code == 200, r.text
    return r.json()["note"]["id"]


def test_write_grantee_can_edit_shared_note(app):
    a = _login(app, "userA", "a@x.com")
    b = _login(app, "userB", "b@x.com")
    note_id = _make_note(a)
    r = a.post(
        f"/api/notes/{note_id}/share", headers=MUT,
        json={"principal": "b@x.com", "level": "write"},
    )
    assert r.status_code == 200 and r.json()["level"] == "write"
    assert "draft" in _slugs(b)  # write implies read
    r = b.patch(f"/api/notes/{note_id}", headers=MUT, json={"body": "edited by B"})
    assert r.status_code == 200, r.text
    assert r.json()["note"]["body"] == "edited by B"
    # the owner sees B's edit
    assert a.get(f"/api/notes/{note_id}").json()["note"]["body"] == "edited by B"


def test_read_grantee_cannot_edit(app):
    a = _login(app, "userA", "a@x.com")
    b = _login(app, "userB", "b@x.com")
    note_id = _make_note(a)
    a.post(f"/api/notes/{note_id}/share", headers=MUT, json={"principal": "b@x.com"})  # read
    r = b.patch(f"/api/notes/{note_id}", headers=MUT, json={"body": "nope"})
    assert r.status_code == 403  # can read, cannot write


def test_stranger_cannot_edit_or_discover(app):
    a = _login(app, "userA", "a@x.com")
    c = _login(app, "userC", "c@x.com")
    note_id = _make_note(a)
    r = c.patch(f"/api/notes/{note_id}", headers=MUT, json={"body": "nope"})
    assert r.status_code == 404  # no read access -> existence not leaked


def test_share_rejects_unknown_level(app):
    a = _login(app, "userA", "a@x.com")
    _login(app, "userB", "b@x.com")
    note_id = _make_note(a)
    r = a.post(
        f"/api/notes/{note_id}/share", headers=MUT,
        json={"principal": "b@x.com", "level": "admin"},
    )
    assert r.status_code == 400


def test_owner_can_edit_own_note(app):
    a = _login(app, "userA", "a@x.com")
    note_id = _make_note(a, body="v1")
    r = a.patch(f"/api/notes/{note_id}", headers=MUT, json={"title": "New", "tags": ["x", "y"]})
    assert r.status_code == 200, r.text
    note = r.json()["note"]
    assert note["title"] == "New" and note["tags"] == ["x", "y"]


# ---- ACL view (owner-only, read-only) --------------------------------------

def test_owner_sees_acl_grants(app):
    a = _login(app, "userA", "a@x.com")
    _login(app, "userB", "b@x.com")
    note_id = _make_note(a)
    a.post(f"/api/notes/{note_id}/share", headers=MUT,
           json={"principal": "b@x.com", "level": "comment"})
    r = a.get(f"/api/notes/{note_id}/acl")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["visibility"] == "shared"  # sharing flipped private -> shared
    assert body["grants"] == [{"principal": "b@x.com", "handle": "", "level": "comment"}]


def test_acl_view_is_owner_only(app):
    a = _login(app, "userA", "a@x.com")
    b = _login(app, "userB", "b@x.com")
    note_id = _make_note(a)
    a.post(f"/api/notes/{note_id}/share", headers=MUT, json={"principal": "b@x.com"})
    r = b.get(f"/api/notes/{note_id}/acl")
    assert r.status_code == 403  # grantee can read the note, not its ACL


def test_acl_shrinks_after_unshare(app):
    a = _login(app, "userA", "a@x.com")
    _login(app, "userB", "b@x.com")
    note_id = _make_note(a)
    a.post(f"/api/notes/{note_id}/share", headers=MUT, json={"principal": "b@x.com"})
    r = a.request("DELETE", f"/api/notes/{note_id}/share", headers=MUT,
                  json={"principal": "b@x.com"})
    assert r.status_code == 200, r.text
    r = a.get(f"/api/notes/{note_id}/acl")
    assert r.status_code == 200
    assert r.json()["grants"] == []
