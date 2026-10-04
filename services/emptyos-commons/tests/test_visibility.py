"""The isolation proof — pure, no DB. A can never read B's private/un-granted
content; public is visible to all; shared is visible only to grantees.

This is the reference semantics the Postgres RLS policy (migrations/0002) and the
repository SQL must agree with."""

from __future__ import annotations

from emptyos_commons.models import AclGrant, CommonsNote
from emptyos_commons.visibility import (
    can_read,
    can_write,
    granted_ids_for,
    granted_write_ids_for,
    visible_notes,
)


def _note(nid, owner, vis):
    return CommonsNote(id=nid, owner_id=owner, slug=nid, title=nid, body="x", visibility=vis)


def test_owner_always_reads_own():
    n = _note("n1", "A", "private")
    assert can_read(n, "A", set())
    assert not can_read(n, "B", set())


def test_public_visible_to_everyone():
    n = _note("n1", "A", "public")
    assert can_read(n, "A", set())
    assert can_read(n, "B", set())
    assert can_read(n, "stranger", set())


def test_private_visible_to_no_one_else():
    n = _note("n1", "A", "private")
    assert not can_read(n, "B", set())
    assert not can_read(n, "B", {"n1"})  # a stray grant must not override private semantics in can_read


def test_shared_visible_only_to_grantees():
    n = _note("n1", "A", "shared")
    acl = [AclGrant(note_id="n1", principal_user_id="B")]
    granted_b = granted_ids_for("B", acl)
    granted_c = granted_ids_for("C", acl)
    assert can_read(n, "B", granted_b)
    assert not can_read(n, "C", granted_c)


def test_visible_notes_filters_a_mixed_corpus():
    notes = [
        _note("pub", "A", "public"),
        _note("a_priv", "A", "private"),
        _note("b_priv", "B", "private"),
        _note("b_shared", "B", "shared"),
    ]
    acl = [AclGrant(note_id="b_shared", principal_user_id="A")]

    a_visible = {n.id for n in visible_notes(notes, "A", acl)}
    assert a_visible == {"pub", "a_priv", "b_shared"}  # own + public + granted
    assert "b_priv" not in a_visible  # B's private is invisible to A

    b_visible = {n.id for n in visible_notes(notes, "B", acl)}
    assert b_visible == {"pub", "b_priv", "b_shared"}  # own + public; not A's private

    stranger_visible = {n.id for n in visible_notes(notes, "C", acl)}
    assert stranger_visible == {"pub"}  # only public


def test_revoking_share_removes_visibility():
    notes = [_note("b_shared", "B", "shared")]
    granted = [AclGrant(note_id="b_shared", principal_user_id="A")]
    assert {n.id for n in visible_notes(notes, "A", granted)} == {"b_shared"}
    assert {n.id for n in visible_notes(notes, "A", [])} == set()


def test_only_owner_or_write_grantee_may_write():
    n = _note("n1", "A", "shared")
    acl = [
        AclGrant(note_id="n1", principal_user_id="B", level="write"),
        AclGrant(note_id="n1", principal_user_id="C", level="read"),
    ]
    assert can_write(n, "A", granted_write_ids_for("A", acl))  # owner
    assert can_write(n, "B", granted_write_ids_for("B", acl))  # write grant
    assert not can_write(n, "C", granted_write_ids_for("C", acl))  # read-only grant
    assert not can_write(n, "D", granted_write_ids_for("D", acl))  # no grant


def test_any_grant_level_implies_read():
    n = _note("n1", "A", "shared")
    for lvl in ("read", "write", "comment"):
        acl = [AclGrant(note_id="n1", principal_user_id="B", level=lvl)]
        assert can_read(n, "B", granted_ids_for("B", acl)), lvl
    # …but only 'write' grants the edit right.
    write_acl = [AclGrant(note_id="n1", principal_user_id="B", level="comment")]
    assert not can_write(n, "B", granted_write_ids_for("B", write_acl))
