"""The owner + visibility + ACL primitive — pure, no I/O.

This is the one genuinely new piece versus the single-user daemon, and the
load-bearing design. It is deliberately a pure function so the isolation
guarantee ("A can never read B's private/un-granted content") is provable in a
unit test without a database. The Postgres RLS policy in migrations/ is the
production backstop that enforces the SAME rule at the DB layer; this function
is what the service code and tests reason about.

A note is visible to a user iff ANY of:
  - they own it                         (always see your own)
  - it is public                        (the shared commons)
  - it is shared AND granted to them    (selective share)
A private note is visible to nobody but its owner.
"""

from __future__ import annotations

from collections.abc import Iterable

from .models import AclGrant, CommonsNote


def can_read(note: CommonsNote, user_id: str, granted_note_ids: set[str]) -> bool:
    """True iff *user_id* may read *note*. `granted_note_ids` is the set of note
    ids explicitly shared with this user (from the ACL)."""
    if note.owner_id == user_id:
        return True
    if note.visibility == "public":
        return True
    if note.visibility == "shared" and note.id in granted_note_ids:
        return True
    return False


# ACL grant levels. read = view; comment = view + annotate (no body edit);
# write = view + edit. Any grant level implies read.
ACL_LEVELS = ("read", "write", "comment")


def granted_ids_for(user_id: str, acl: Iterable[AclGrant]) -> set[str]:
    """Note ids *user_id* may READ via the ACL. Any grant level implies read."""
    return {
        g.note_id for g in acl
        if g.principal_user_id == user_id and g.level in ACL_LEVELS
    }


def granted_write_ids_for(user_id: str, acl: Iterable[AclGrant]) -> set[str]:
    """Note ids *user_id* may EDIT via the ACL (an explicit ``write`` grant)."""
    return {
        g.note_id for g in acl
        if g.principal_user_id == user_id and g.level == "write"
    }


def can_write(
    note: CommonsNote, user_id: str, write_granted_note_ids: set[str]
) -> bool:
    """True iff *user_id* may edit *note* — its owner, or a write-grantee.

    Symmetric to :func:`can_read`. The grant itself is the authorization, so a
    write grant lets a non-owner edit regardless of the note's visibility
    (the owner always may; read/comment grantees may not)."""
    if note.owner_id == user_id:
        return True
    return note.id in write_granted_note_ids


def visible_notes(
    notes: Iterable[CommonsNote],
    user_id: str,
    acl: Iterable[AclGrant],
) -> list[CommonsNote]:
    """Filter *notes* to exactly those *user_id* may read, newest-updated first.

    This is the reference semantics the repository SQL and the RLS policy must
    match. Tests assert all three implementations agree.
    """
    granted = granted_ids_for(user_id, acl)
    out = [n for n in notes if can_read(n, user_id, granted)]
    out.sort(key=lambda n: (n.updated or n.created or "", n.id), reverse=True)
    return out


def can_share(note: CommonsNote, actor_user_id: str) -> bool:
    """Only the owner may grant/revoke shares or change visibility."""
    return note.owner_id == actor_user_id
