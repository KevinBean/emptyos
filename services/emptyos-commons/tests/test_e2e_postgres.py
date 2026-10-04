"""Postgres backend + the RLS backstop — gated, opt-in.

Runs only when COMMONS_TEST_DATABASE_URL points at a throwaway Postgres (CI or a
local docker). Skipped otherwise, so the default suite needs no database.

    COMMONS_TEST_DATABASE_URL=postgresql://commons:commons@127.0.0.1:5432/commons_test \
        python -m pytest tests/test_e2e_postgres.py -v

It proves two things the in-memory/SQLite suites can't:
  1. The Postgres repo enforces the same visibility as visibility.py.
  2. The RESTRICTIVE per-user RLS policy (migration 0002) refuses to leak A's
     private row to B AT THE DATABASE — even on a deliberately unscoped query
     that bypasses the application WHERE clause.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

DB = os.environ.get("COMMONS_TEST_DATABASE_URL", "").strip()
pytestmark = pytest.mark.skipif(not DB, reason="set COMMONS_TEST_DATABASE_URL to run")

SERVICE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVICE_DIR))


def _fresh_db():
    """Apply all migrations onto an empty schema (drops first for a clean run)."""
    import psycopg

    from migrate import apply_migrations, discover_migrations

    with psycopg.connect(DB) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        c.commit()
    apply_migrations(DB, discover_migrations())


@pytest.fixture
def repo():
    from emptyos_commons.repositories import PostgresCommonsRepository

    _fresh_db()
    return PostgresCommonsRepository(DB)


def _user(repo, subject, email):
    from emptyos_commons.models import VerifiedIdentity

    return repo.ensure_user(VerifiedIdentity(subject=subject, email=email))


def test_postgres_visibility_matches_reference(repo):
    a, b = _user(repo, "A", "a@x.com"), _user(repo, "B", "b@x.com")
    repo.publish_note(a.id, {"slug": "pub", "body": "x"}, "public")
    repo.publish_note(a.id, {"slug": "priv", "body": "x"}, "private")
    shared = repo.publish_note(a.id, {"slug": "shared", "body": "x"}, "shared")
    repo.share_note(shared.id, b.id)

    assert {n.slug for n in repo.list_visible_notes(a.id)} == {"pub", "priv", "shared"}
    assert {n.slug for n in repo.list_visible_notes(b.id)} == {"pub", "shared"}


def test_rls_backstop_refuses_unscoped_leak(repo):
    """The whole point of the restrictive policy: even a query with NO WHERE
    clause, run as user B, cannot return A's private row."""
    import psycopg

    a, b = _user(repo, "A", "a@x.com"), _user(repo, "B", "b@x.com")
    repo.publish_note(a.id, {"slug": "a-public", "body": "x"}, "public")
    repo.publish_note(a.id, {"slug": "a-private", "body": "secret"}, "private")

    with psycopg.connect(DB) as c:
        with c.transaction():
            c.execute("SET LOCAL commons.app = 'on'")
            c.execute("SET LOCAL commons.current_user = %s", (b.id,))
            # deliberately unscoped — the app WHERE clause is NOT applied here
            rows = c.execute("SELECT slug, visibility FROM commons_notes").fetchall()
    slugs = {r[0] for r in rows}
    assert "a-public" in slugs           # public is readable by B
    assert "a-private" not in slugs      # DB itself refuses A's private to B


def test_rls_backstop_owner_sees_own_private(repo):
    import psycopg

    a = _user(repo, "A", "a@x.com")
    repo.publish_note(a.id, {"slug": "mine", "body": "x"}, "private")
    with psycopg.connect(DB) as c:
        with c.transaction():
            c.execute("SET LOCAL commons.app = 'on'")
            c.execute("SET LOCAL commons.current_user = %s", (a.id,))
            rows = c.execute("SELECT slug FROM commons_notes").fetchall()
    assert {r[0] for r in rows} == {"mine"}  # owner sees own private under RLS
