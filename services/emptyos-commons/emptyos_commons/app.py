"""EmptyOS Commons — authenticated shared-content service.

Auth spine (sessions, cookie, origin gate) lifted from englishos-control-plane.
The learner proxy is replaced by content endpoints over commons_notes + the
owner/visibility/ACL primitive. No per-user daemon provisioning in this phase
(trusted-team posture); commons_instances stays nullable for a later flip.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

WEB_DIR = Path(__file__).resolve().parent / "web"

from .auth import (
    DevIdentityVerifier,
    FirebaseIdentityVerifier,
    IdentityVerifier,
    hash_session_token,
)
from .models import VISIBILITIES, CommonsNote, SessionPrincipal, VerifiedIdentity
from .passwords import hash_password, verify_password
from .repositories import (
    EDITABLE_NOTE_FIELDS,
    CommonsRepository,
    InMemoryCommonsRepository,
    PostgresCommonsRepository,
    SqliteCommonsRepository,
)
from .settings import Settings
from .visibility import (
    ACL_LEVELS,
    can_read,
    can_share,
    can_write,
    granted_ids_for,
    granted_write_ids_for,
)

COOKIE_NAME = "__Host-commons_session"
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _bearer_token(authorization: str) -> str:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="ID token required")
    return token.strip()


def _build_repository(database_url: str) -> CommonsRepository:
    """Pick the backend by URL:
      - `memory`            → in-memory (tests / ephemeral; loses data on restart)
      - `sqlite:///path`    → SQLite file (trusted-team default — stdlib, no infra)
      - `postgresql://…`    → Postgres + RLS backstop (public / scale)
    """
    if database_url == "memory":
        return InMemoryCommonsRepository()
    if database_url.startswith("sqlite:"):
        # Strip the scheme, then one leading slash (SQLAlchemy convention):
        #   sqlite:///rel/x.db   -> rel/x.db        (relative)
        #   sqlite:////abs/x.db  -> /abs/x.db       (absolute, POSIX)
        #   sqlite:///C:/x.db    -> C:/x.db         (absolute, Windows drive)
        #   sqlite:///:memory:   -> :memory:
        rest = database_url[len("sqlite://"):]
        path = rest[1:] if rest.startswith("/") else rest
        if path.endswith(":memory:"):
            path = ":memory:"
        return SqliteCommonsRepository(path)
    return PostgresCommonsRepository(database_url)


def _build_verifier(settings: Settings) -> IdentityVerifier | None:
    if settings.auth_provider == "password":
        return None  # password mode resolves identity from the request body
    if settings.auth_provider == "dev":
        return DevIdentityVerifier()
    return FirebaseIdentityVerifier(settings.firebase_project_id)


def _note_json(note: CommonsNote, *, owner: bool) -> dict:
    return {
        "id": note.id,
        "slug": note.slug,
        "title": note.title,
        "body": note.body,
        "kind": note.kind,
        "domain": note.domain,
        "tags": list(note.tags),
        "author": note.author,
        "visibility": note.visibility,
        "created": note.created,
        "updated": note.updated,
        "is_owner": owner,
    }


def create_app(
    *,
    settings: Settings | None = None,
    repository: CommonsRepository | None = None,
    identity_verifier: IdentityVerifier | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.validate()
    if repository is None:
        repository = _build_repository(settings.database_url)
    identity_verifier = identity_verifier or _build_verifier(settings)

    server = FastAPI(title="EmptyOS Commons", version="0.1.0")

    # __Host- cookies require the Secure attribute (HTTPS). Fall back to a plain
    # name when running insecure (local http dev) so the browser still stores it.
    cookie_name = COOKIE_NAME if settings.session_secure else "commons_session"

    def enforce_origin(request: Request) -> None:
        if request.method not in MUTATING_METHODS:
            return
        origin = request.headers.get("origin", "").rstrip("/")
        if origin != settings.public_origin:
            raise HTTPException(status_code=403, detail="origin rejected")

    def principal_for_token(raw_token: str) -> SessionPrincipal:
        if not raw_token:
            raise HTTPException(status_code=401, detail="sign in required")
        token_hash = hash_session_token(raw_token, settings.session_hash_secret)
        found = repository.principal_for_session(token_hash, datetime.now(UTC))
        if not found:
            raise HTTPException(status_code=401, detail="session expired")
        return found

    def principal_via_api_token(token: str) -> SessionPrincipal | None:
        token_hash = hash_session_token(token, settings.session_hash_secret)
        user = repository.user_for_api_token(token_hash)
        if not user:
            return None
        return SessionPrincipal(user=user, instance=repository.instance_for_user(user.id))

    def principal(request: Request) -> SessionPrincipal:
        # Humans: session cookie. Machine clients (the daemon bridge): a durable
        # Bearer API token. Cookie wins when both are present.
        raw = request.cookies.get(cookie_name, "")
        if raw:
            return principal_for_token(raw)
        authz = request.headers.get("authorization", "")
        scheme, _, token = authz.partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            found = principal_via_api_token(token.strip())
            if found:
                return found
        raise HTTPException(status_code=401, detail="sign in required")

    def _resolve_principal_user(ref: str):
        """Resolve a share target (email or oidc_subject) to an existing user."""
        ref = (ref or "").strip()
        if not ref:
            return None
        return repository.user_by_email(ref) or repository.user_by_subject(ref)

    # ----- web -----
    @server.get("/", response_class=HTMLResponse)
    async def index():
        # Public page: register/login, then the commons feed. Client-side gated
        # on /api/me; no user data baked into the static HTML.
        return HTMLResponse((WEB_DIR / "index.html").read_text(encoding="utf-8"))

    # ----- health -----
    @server.get("/health")
    async def health():
        return {"status": "ok", "service": "emptyos-commons"}

    @server.get("/ready")
    async def ready():
        repository.healthcheck()
        return {"status": "ok"}

    @server.get("/auth/config")
    async def auth_config():
        return {
            "apiKey": settings.firebase_web_api_key,
            "authDomain": settings.firebase_auth_domain
            or (f"{settings.firebase_project_id}.firebaseapp.com" if settings.firebase_project_id else ""),
            "projectId": settings.firebase_project_id,
            "productName": settings.product_name,
            "productTagline": settings.product_tagline,
            "authProvider": settings.auth_provider,
            "requiresRegistrationSecret": bool(settings.registration_secret),
        }

    # ----- auth -----
    async def _resolve_identity(request: Request, authorization: str) -> VerifiedIdentity:
        """Resolve the caller to a VerifiedIdentity. Password mode reads
        {email,password} from the body; token IdPs verify the bearer token."""
        if settings.auth_provider == "password":
            body = await request.json()
            email = (body.get("email") or "").strip().lower()
            password = body.get("password") or ""
            user = repository.user_by_email(email)
            stored = repository.password_hash_for(user.id) if user else None
            if user is None or not stored or not verify_password(password, stored):
                raise HTTPException(status_code=401, detail="invalid email or password")
            return VerifiedIdentity(subject=user.oidc_subject, email=user.email)
        if identity_verifier is None:
            raise HTTPException(status_code=500, detail="no identity verifier configured")
        try:
            return identity_verifier.verify(_bearer_token(authorization))
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=401, detail="invalid ID token") from exc

    @server.post("/auth/register")
    async def register(request: Request):
        """Create a password account. Password mode only. Gated by
        registration_secret when set (a shared invite secret for a closed team)."""
        enforce_origin(request)
        if settings.auth_provider != "password":
            raise HTTPException(status_code=404, detail="registration is only available in password mode")
        body = await request.json()
        email = (body.get("email") or "").strip().lower()
        password = body.get("password") or ""
        if not email or "@" not in email:
            raise HTTPException(status_code=400, detail="a valid email is required")
        if len(password) < settings.min_password_len:
            raise HTTPException(status_code=400, detail=f"password must be at least {settings.min_password_len} characters")
        if settings.registration_secret and body.get("secret") != settings.registration_secret:
            raise HTTPException(status_code=403, detail="registration secret required")
        if repository.user_by_email(email) is not None:
            raise HTTPException(status_code=409, detail="that email is already registered")
        user = repository.ensure_user(VerifiedIdentity(subject=email, email=email))
        repository.set_password(user.id, hash_password(password))
        repository.audit(user.id, "user.registered", {"email": email})
        return {"ok": True}

    @server.post("/auth/session")
    async def create_session(
        request: Request,
        response: Response,
        authorization: str = Header(default=""),
    ):
        enforce_origin(request)
        identity = await _resolve_identity(request, authorization)

        # Invite-only gate (token IdPs): a verified identity with no pre-created
        # account is refused. In password mode, registration is the gate, so this
        # is skipped. Default posture is OPEN signup.
        if (
            settings.auth_provider != "password"
            and settings.invite_only
            and repository.user_by_subject(identity.subject) is None
        ):
            raise HTTPException(status_code=403, detail="invite required")

        user = repository.ensure_user(identity)
        instance = repository.instance_for_user(user.id)
        raw_session = secrets.token_urlsafe(32)
        token_hash = hash_session_token(raw_session, settings.session_hash_secret)
        expires_at = datetime.now(UTC) + timedelta(seconds=settings.session_ttl_seconds)
        repository.create_session(user.id, token_hash, expires_at)
        repository.audit(user.id, "session.created", {"has_instance": instance is not None})
        response.set_cookie(
            cookie_name,
            raw_session,
            max_age=settings.session_ttl_seconds,
            httponly=True,
            secure=settings.session_secure,
            samesite="strict",
            path="/",
        )
        return {"ok": True, "has_instance": instance is not None}

    @server.post("/auth/password")
    async def change_password(request: Request):
        """Change the signed-in user's password. Password mode only."""
        enforce_origin(request)
        if settings.auth_provider != "password":
            raise HTTPException(status_code=404, detail="password change is only available in password mode")
        found = principal(request)
        body = await request.json()
        old = body.get("old") or ""
        new = body.get("new") or ""
        stored = repository.password_hash_for(found.user.id)
        if not stored or not verify_password(old, stored):
            raise HTTPException(status_code=403, detail="current password is incorrect")
        if len(new) < settings.min_password_len:
            raise HTTPException(status_code=400, detail=f"password must be at least {settings.min_password_len} characters")
        repository.set_password(found.user.id, hash_password(new))
        repository.audit(found.user.id, "password.changed", {})
        return {"ok": True}

    @server.post("/auth/logout")
    async def logout(request: Request, response: Response):
        enforce_origin(request)
        raw_token = request.cookies.get(cookie_name, "")
        if raw_token:
            repository.revoke_session(hash_session_token(raw_token, settings.session_hash_secret))
        response.delete_cookie(cookie_name, path="/")
        return {"ok": True}

    @server.get("/api/me")
    async def me(request: Request):
        found = principal(request)
        return {
            "subject": found.user.oidc_subject,
            "email": found.user.email,
            "handle": found.user.handle,
            "has_instance": found.instance is not None,
        }

    # ----- machine tokens (durable daemon auth) -----
    @server.post("/api/tokens")
    async def mint_token(request: Request):
        """Mint a durable API token for a daemon. The raw token is returned ONCE
        and never stored in the clear — only its hash is kept."""
        enforce_origin(request)
        found = principal(request)
        body = await request.json()
        label = ((body.get("label") or "daemon").strip()[:80]) or "daemon"
        raw = "ct_" + secrets.token_urlsafe(32)
        token_hash = hash_session_token(raw, settings.session_hash_secret)
        tid = repository.create_api_token(found.user.id, token_hash, label)
        repository.audit(found.user.id, "api_token.created", {"id": tid, "label": label})
        return {"ok": True, "id": tid, "label": label, "token": raw}

    @server.get("/api/tokens")
    async def list_tokens(request: Request):
        found = principal(request)
        return {"tokens": repository.list_api_tokens(found.user.id)}

    @server.delete("/api/tokens/{token_id}")
    async def revoke_token(request: Request, token_id: str):
        enforce_origin(request)
        found = principal(request)
        if not repository.revoke_api_token(found.user.id, token_id):
            raise HTTPException(status_code=404, detail="token not found")
        repository.audit(found.user.id, "api_token.revoked", {"id": token_id})
        return {"ok": True}

    # ----- content -----
    @server.post("/api/notes")
    async def publish_note(request: Request):
        enforce_origin(request)
        found = principal(request)
        payload = await request.json()
        slug = (payload.get("slug") or "").strip()
        if not slug:
            raise HTTPException(status_code=400, detail="slug is required")
        visibility = (payload.get("visibility") or "private").strip()
        if visibility not in VISIBILITIES:
            raise HTTPException(status_code=400, detail=f"visibility must be one of {VISIBILITIES}")
        note = repository.publish_note(
            found.user.id,
            {
                "slug": slug,
                "title": payload.get("title", ""),
                "body": payload.get("body", ""),
                "kind": payload.get("kind", "concept"),
                "domain": payload.get("domain", ""),
                "tags": payload.get("tags", []),
                "author": payload.get("author", "user"),
            },
            visibility,
        )
        # Optional inline selective-share: share_with = [email|subject, ...].
        shared = []
        unresolved = []
        for ref in payload.get("share_with", []) or []:
            target = _resolve_principal_user(ref)
            if target is None:
                unresolved.append(ref)
                continue
            repository.share_note(note.id, target.id)
            shared.append(ref)
        if shared and visibility == "private":
            repository.set_visibility(note.id, "shared")
            note = repository.get_note(note.id) or note
        repository.audit(found.user.id, "note.published", {"slug": slug, "visibility": note.visibility})
        return {"ok": True, "note": _note_json(note, owner=True), "shared": shared, "unresolved": unresolved}

    @server.get("/api/notes")
    async def list_notes(request: Request):
        found = principal(request)
        notes = repository.list_visible_notes(found.user.id)
        return {
            "notes": [_note_json(n, owner=(n.owner_id == found.user.id)) for n in notes]
        }

    @server.get("/api/notes/{note_id}")
    async def get_note(request: Request, note_id: str):
        found = principal(request)
        note = repository.get_note(note_id)
        if note is None:
            raise HTTPException(status_code=404, detail="note not found")
        granted = granted_ids_for(found.user.id, repository.acl_for_note(note_id))
        if not can_read(note, found.user.id, granted):
            # Don't leak existence of notes the caller can't see.
            raise HTTPException(status_code=404, detail="note not found")
        return {"note": _note_json(note, owner=(note.owner_id == found.user.id))}

    def _require_owned(request: Request, note_id: str):
        found = principal(request)
        note = repository.get_note(note_id)
        if note is None:
            raise HTTPException(status_code=404, detail="note not found")
        if not can_share(note, found.user.id):
            raise HTTPException(status_code=403, detail="only the owner may do that")
        return found, note

    @server.get("/api/notes/{note_id}/acl")
    async def note_acl(request: Request, note_id: str):
        # Owner-only view of who a note is shared with. Read-only (no
        # enforce_origin — GETs don't mutate); principals resolved to their
        # display identity (email/handle), never the internal user id alone.
        _found, note = _require_owned(request, note_id)
        grants = []
        for grant in repository.acl_for_note(note.id):
            target = repository.user_by_id(grant.principal_user_id)
            grants.append({
                "principal": (target.email or target.handle) if target else "",
                "handle": target.handle if target else "",
                "level": grant.level,
            })
        return {"ok": True, "visibility": note.visibility, "grants": grants}

    @server.post("/api/notes/{note_id}/share")
    async def share(request: Request, note_id: str):
        enforce_origin(request)
        _found, note = _require_owned(request, note_id)
        payload = await request.json()
        target = _resolve_principal_user(payload.get("principal", ""))
        if target is None:
            raise HTTPException(status_code=404, detail="no such commons user")
        level = (payload.get("level") or "read").strip()
        if level not in ACL_LEVELS:
            raise HTTPException(status_code=400, detail=f"level must be one of {ACL_LEVELS}")
        repository.share_note(note.id, target.id, level)
        if note.visibility == "private":
            repository.set_visibility(note.id, "shared")
        repository.audit(
            note.owner_id, "note.shared",
            {"note_id": note.id, "principal": target.id, "level": level},
        )
        return {"ok": True, "level": level}

    @server.patch("/api/notes/{note_id}")
    async def update_note_endpoint(request: Request, note_id: str):
        # Edit a note's title/body/tags. The owner always may; a non-owner needs
        # an explicit write grant (can_write). read/comment grantees get 403.
        enforce_origin(request)
        found = principal(request)
        note = repository.get_note(note_id)
        if note is None:
            raise HTTPException(status_code=404, detail="note not found")
        write_granted = granted_write_ids_for(found.user.id, repository.acl_for_note(note_id))
        if not can_write(note, found.user.id, write_granted):
            # Don't leak existence to a caller who can't even read it.
            granted = granted_ids_for(found.user.id, repository.acl_for_note(note_id))
            if not can_read(note, found.user.id, granted):
                raise HTTPException(status_code=404, detail="note not found")
            raise HTTPException(status_code=403, detail="no write access to this note")
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="body must be an object")
        fields = {k: payload[k] for k in EDITABLE_NOTE_FIELDS if k in payload}
        if not fields:
            raise HTTPException(
                status_code=400, detail=f"provide at least one of {EDITABLE_NOTE_FIELDS}"
            )
        updated = repository.update_note(note_id, fields)
        repository.audit(found.user.id, "note.updated", {"note_id": note_id})
        return {"ok": True, "note": _note_json(updated, owner=(updated.owner_id == found.user.id))}

    @server.delete("/api/notes/{note_id}/share")
    async def unshare(request: Request, note_id: str):
        enforce_origin(request)
        _found, note = _require_owned(request, note_id)
        payload = await request.json()
        target = _resolve_principal_user(payload.get("principal", ""))
        if target is None:
            raise HTTPException(status_code=404, detail="no such commons user")
        repository.unshare_note(note.id, target.id)
        return {"ok": True}

    @server.patch("/api/notes/{note_id}/visibility")
    async def set_visibility(request: Request, note_id: str):
        enforce_origin(request)
        _found, note = _require_owned(request, note_id)
        payload = await request.json()
        visibility = (payload.get("visibility") or "").strip()
        if visibility not in VISIBILITIES:
            raise HTTPException(status_code=400, detail=f"visibility must be one of {VISIBILITIES}")
        repository.set_visibility(note.id, visibility)
        return {"ok": True}

    @server.delete("/api/notes/{note_id}")
    async def delete_note(request: Request, note_id: str):
        enforce_origin(request)
        _found, note = _require_owned(request, note_id)
        repository.delete_note(note.id)
        repository.audit(note.owner_id, "note.revoked", {"note_id": note.id})
        return {"ok": True}

    return server
