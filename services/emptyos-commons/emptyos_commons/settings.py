"""Environment-backed commons configuration. Env prefix: COMMONS_."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _as_bool(value: str, default: bool) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


@dataclass(frozen=True)
class Settings:
    database_url: str
    session_hash_secret: str
    public_origin: str
    # Identity provider:
    #   "password" — simple in-service email+password (trusted-team default)
    #   "firebase" — managed IdP (product-level; the upgrade path)
    #   "dev"      — insecure local/test (requires COMMONS_DEV_AUTH=1)
    # The IdentityVerifier Protocol + the password path are interchangeable: both
    # resolve to a VerifiedIdentity, so switching providers leaves sessions +
    # content untouched.
    auth_provider: str = "firebase"
    # password mode only: if set, /auth/register requires `secret` in the body
    # (a shared invite secret for a closed team). Empty = open registration.
    registration_secret: str = ""
    # password mode only: minimum acceptable password length.
    min_password_len: int = 8
    firebase_project_id: str = ""
    session_secure: bool = True
    session_ttl_seconds: int = 86400
    # Public Firebase web config for the sign-in page (NOT secret — ships to
    # the browser).
    firebase_web_api_key: str = ""
    firebase_auth_domain: str = ""
    product_name: str = "EmptyOS Commons"
    product_tagline: str = "A shared knowledge space — public, private, and shared on purpose."
    # When false (the default trusted-team posture), signup is OPEN: any verified
    # identity gets a commons-only account on first sign-in. Set true to require
    # an out-of-band invite (a pre-created user row) before a login is accepted.
    invite_only: bool = False

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database_url=os.environ.get("COMMONS_DATABASE_URL", "").strip(),
            session_hash_secret=os.environ.get("COMMONS_SESSION_HASH_SECRET", "").strip(),
            public_origin=os.environ.get("COMMONS_PUBLIC_ORIGIN", "").strip().rstrip("/"),
            auth_provider=os.environ.get("COMMONS_AUTH_PROVIDER", "firebase").strip().lower()
            or "firebase",
            firebase_project_id=os.environ.get("COMMONS_FIREBASE_PROJECT_ID", "").strip(),
            registration_secret=os.environ.get("COMMONS_REGISTRATION_SECRET", "").strip(),
            min_password_len=int(os.environ.get("COMMONS_MIN_PASSWORD_LEN", "8")),
            session_secure=_as_bool(os.environ.get("COMMONS_SESSION_SECURE", "true"), True),
            session_ttl_seconds=int(os.environ.get("COMMONS_SESSION_TTL_SECONDS", "86400")),
            firebase_web_api_key=os.environ.get("COMMONS_FIREBASE_WEB_API_KEY", "").strip(),
            firebase_auth_domain=os.environ.get("COMMONS_FIREBASE_AUTH_DOMAIN", "").strip(),
            product_name=os.environ.get("COMMONS_PRODUCT_NAME", "EmptyOS Commons").strip()
            or "EmptyOS Commons",
            product_tagline=os.environ.get(
                "COMMONS_PRODUCT_TAGLINE",
                "A shared knowledge space — public, private, and shared on purpose.",
            ).strip(),
            invite_only=_as_bool(os.environ.get("COMMONS_INVITE_ONLY", "false"), False),
        )

    def validate(self) -> None:
        missing = []
        if not self.database_url:
            missing.append("COMMONS_DATABASE_URL")
        if len(self.session_hash_secret) < 32:
            missing.append("COMMONS_SESSION_HASH_SECRET (32+ characters)")
        if not self.public_origin:
            missing.append("COMMONS_PUBLIC_ORIGIN")
        if self.auth_provider == "firebase" and not self.firebase_project_id:
            missing.append("COMMONS_FIREBASE_PROJECT_ID (auth_provider=firebase)")
        if missing:
            raise RuntimeError("Missing required settings: " + ", ".join(missing))
