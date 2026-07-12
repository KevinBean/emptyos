"""SQLite backend: data survives a restart, and visibility holds at the repo
layer (the trusted-team backend has no RLS, so the repo query IS the boundary)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from emptyos_commons.models import VerifiedIdentity
from emptyos_commons.repositories import SqliteCommonsRepository


def _user(repo, subject, email):
    return repo.ensure_user(VerifiedIdentity(subject=subject, email=email))


def test_data_survives_reopen(tmp_path):
    path = str(tmp_path / "commons.db")
    repo = SqliteCommonsRepository(path)
    a = _user(repo, "userA", "a@x.com")
    repo.publish_note(a.id, {"slug": "kept", "title": "Kept", "body": "x"}, "public")
    del repo  # close the process's handle

    # reopen the same file — a fresh repo over the persisted db
    repo2 = SqliteCommonsRepository(path)
    a2 = repo2.user_by_subject("userA")
    assert a2 is not None and a2.id == a.id            # user persisted
    notes = repo2.list_visible_notes(a2.id)
    assert [n.slug for n in notes] == ["kept"]          # note persisted
    assert notes[0].title == "Kept"


def test_visibility_at_repo_layer(tmp_path):
    repo = SqliteCommonsRepository(str(tmp_path / "c.db"))
    a = _user(repo, "A", "a@x.com")
    b = _user(repo, "B", "b@x.com")
    repo.publish_note(a.id, {"slug": "pub", "body": "x"}, "public")
    repo.publish_note(a.id, {"slug": "priv", "body": "x"}, "private")
    shared = repo.publish_note(a.id, {"slug": "shared", "body": "x"}, "shared")
    repo.share_note(shared.id, b.id)

    a_slugs = {n.slug for n in repo.list_visible_notes(a.id)}
    b_slugs = {n.slug for n in repo.list_visible_notes(b.id)}
    assert a_slugs == {"pub", "priv", "shared"}         # owner sees all own
    assert b_slugs == {"pub", "shared"}                 # public + granted, NOT priv

    # revoke the share -> B loses it
    repo.unshare_note(shared.id, b.id)
    assert {n.slug for n in repo.list_visible_notes(b.id)} == {"pub"}


def test_session_roundtrip_and_expiry(tmp_path):
    repo = SqliteCommonsRepository(str(tmp_path / "c.db"))
    a = _user(repo, "A", "a@x.com")
    now = datetime.now(UTC)
    repo.create_session(a.id, "hash-live", now + timedelta(hours=1))
    repo.create_session(a.id, "hash-dead", now - timedelta(hours=1))
    assert repo.principal_for_session("hash-live", now) is not None
    assert repo.principal_for_session("hash-dead", now) is None     # expired
    repo.revoke_session("hash-live")
    assert repo.principal_for_session("hash-live", now) is None     # revoked


def test_upsert_by_owner_slug_keeps_created(tmp_path):
    repo = SqliteCommonsRepository(str(tmp_path / "c.db"))
    a = _user(repo, "A", "a@x.com")
    n1 = repo.publish_note(a.id, {"slug": "doc", "body": "v1"}, "private")
    n2 = repo.publish_note(a.id, {"slug": "doc", "body": "v2"}, "public")
    assert n1.id == n2.id                 # same note, not a duplicate
    assert n2.body == "v2" and n2.visibility == "public"
    assert n2.created == n1.created       # created preserved across re-publish
