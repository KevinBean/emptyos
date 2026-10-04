"""Managed identity verification and opaque session-token hashing.

Lifted from englishos-control-plane. The IdentityVerifier Protocol is the swap
point: keep FirebaseIdentityVerifier for production, or substitute any IdP
(Auth0, email/password, your own) without touching sessions/endpoints.
DevIdentityVerifier exists so the service runs + tests locally without Firebase.
"""

from __future__ import annotations

import hmac
import os
from hashlib import sha256
from typing import Protocol

from .models import VerifiedIdentity


class IdentityVerifier(Protocol):
    def verify(self, id_token: str) -> VerifiedIdentity: ...


class FirebaseIdentityVerifier:
    """Verify browser-issued Firebase ID tokens with the Admin SDK."""

    def __init__(self, project_id: str):
        if not project_id:
            raise RuntimeError("COMMONS_FIREBASE_PROJECT_ID is required")
        try:
            import firebase_admin
        except ImportError as exc:
            raise RuntimeError(
                "Install service dependencies first: "
                'pip install -e "services/emptyos-commons"'
            ) from exc
        self._firebase_admin = firebase_admin
        self._project_id = project_id
        try:
            self._app = firebase_admin.get_app()
        except ValueError:
            self._app = firebase_admin.initialize_app(options={"projectId": project_id})

    def verify(self, id_token: str) -> VerifiedIdentity:
        from firebase_admin import auth

        claims = auth.verify_id_token(id_token, app=self._app, check_revoked=True)
        subject = str(claims.get("uid") or claims.get("sub") or "").strip()
        if not subject:
            raise ValueError("Firebase token has no uid")
        return VerifiedIdentity(
            subject=subject,
            email=str(claims.get("email") or "").strip(),
        )


class DevIdentityVerifier:
    """Insecure local/test verifier — NEVER enable in production.

    Treats the bearer token as `subject` or `subject:email`. Gated behind
    COMMONS_DEV_AUTH=1 so it can't be selected by accident. Lets the service +
    tests run without a Firebase project.
    """

    def __init__(self):
        if os.environ.get("COMMONS_DEV_AUTH", "").strip().lower() not in {"1", "true", "yes", "on"}:
            raise RuntimeError("DevIdentityVerifier requires COMMONS_DEV_AUTH=1")

    def verify(self, id_token: str) -> VerifiedIdentity:
        raw = (id_token or "").strip()
        if not raw:
            raise ValueError("empty dev token")
        subject, _, email = raw.partition(":")
        subject = subject.strip()
        if not subject:
            raise ValueError("dev token has no subject")
        return VerifiedIdentity(subject=subject, email=email.strip())


def hash_session_token(token: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), token.encode("utf-8"), sha256).hexdigest()
