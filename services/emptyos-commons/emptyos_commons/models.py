"""Shared commons record types.

Identity/session records are lifted verbatim from the englishos-control-plane
(the proven multi-tenant spine). The CommonsNote / AclGrant records are new —
they carry the one primitive the EmptyOS daemon never had: per-content `owner`
+ `visibility`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The three visibility levels — the douban primitive.
#   public  → every authenticated user may read it (the shared commons)
#   private → only the owner may read it (parked in the commons but not shared)
#   shared  → the owner + the users named in commons_acl may read it
VISIBILITIES = ("public", "private", "shared")


@dataclass(frozen=True)
class VerifiedIdentity:
    subject: str
    email: str = ""


@dataclass(frozen=True)
class UserRecord:
    id: str
    oidc_subject: str
    email: str = ""
    # display handle for the douban-style author byline; falls back to email
    # local-part when empty. Never used for auth.
    handle: str = ""


@dataclass(frozen=True)
class InstanceRecord:
    """A user's OPTIONAL private daemon (the premium tier).

    Nullable per user by design (account↔daemon decoupling): a commons-only
    account has no instance. Kept here so a later flip to provisioned private
    daemons is additive, not a migration.
    """

    id: str
    user_id: str
    routing_key: str
    status: str
    upstream_url: str
    inner_token_env: str


@dataclass(frozen=True)
class SessionPrincipal:
    user: UserRecord
    instance: InstanceRecord | None


@dataclass(frozen=True)
class CommonsNote:
    """A note a user has published into the commons.

    Mirrors the daemon's KB note shape (kind/title/domain/tags/body/author) so a
    note round-trips cleanly between a private vault and the shared commons.
    `owner_id` + `visibility` are the new fields the vault never had.
    """

    id: str
    owner_id: str
    slug: str
    title: str
    body: str
    visibility: str = "private"
    kind: str = "concept"
    domain: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)
    author: str = "user"  # user | ai | both (authorship-boundary, daemon-side)
    created: str = ""
    updated: str = ""


@dataclass(frozen=True)
class AclGrant:
    note_id: str
    principal_user_id: str
    # read = view; comment = view + annotate; write = view + edit. Any grant
    # level implies read. See emptyos_commons.visibility.ACL_LEVELS.
    level: str = "read"
