# EmptyOS Commons

The **shared layer** of a collaborative EmptyOS. Each person's personal EmptyOS
daemon stays **single-user** (the private layer, unchanged — "the vault is the
user"). This service holds only content a user has **chosen to publish or share**,
with real accounts and per-content access control.

It is the douban-style model done without breaking the daemon: **private mind
(daemon) + shared brain (commons).**

## The one primitive

Every note carries an **`owner`** and a **`visibility`**:

| Visibility | Who can read it |
|---|---|
| `public` | every authenticated user (the shared commons — e.g. most KB notes) |
| `private` | only the owner (parked here but not shared) |
| `shared` | the owner + the users named in the ACL (selective share) |

This is the one thing the single-user daemon never had. It lives in
`emptyos_commons/visibility.py` (pure, the reference semantics), is enforced in the
repository SQL, and is backstopped at the database by a **restrictive per-user RLS
policy** (`migrations/0002`). All three agree — proven by `tests/`.

## Architecture

Lifted from `services/englishos-control-plane` (the proven multi-tenant spine):
`auth.py` (IdentityVerifier Protocol + opaque sessions), the repository + RLS
pattern, the FastAPI auth assembly. New here: the `commons_notes` / `commons_acl`
content layer and the visibility primitive.

- `commons_instances` is **nullable per user** by design (account↔daemon
  decoupling): a commons-only account has no private daemon. This is what makes a
  later flip to a public login site cheap — open signup + optional per-user daemon
  provisioning, no migration.

## Endpoints

| Method | Path | Who | What |
|---|---|---|---|
| POST | `/auth/register` | anyone (password mode) | create an email+password account (gated by registration secret if set) |
| POST | `/auth/session` | valid ID token, or email+password (password mode) | exchange for a session cookie |
| POST | `/auth/password` | session (password mode) | change own password |
| POST | `/auth/logout` | session | revoke |
| GET | `/api/me` | session | current account |
| POST | `/api/notes` | session | publish a note (`slug`, `title`, `body`, `kind`, `domain`, `tags`, `visibility`, optional `share_with`) |
| GET | `/api/notes` | session | list notes the caller may read (own + public + granted) |
| GET | `/api/notes/{id}` | session | one note (404 if not visible — existence not leaked) |
| POST | `/api/notes/{id}/share` | owner | grant `{principal: email\|subject}` (flips `private`→`shared`) |
| DELETE | `/api/notes/{id}/share` | owner | revoke a grant |
| PATCH | `/api/notes/{id}/visibility` | owner | change visibility |
| DELETE | `/api/notes/{id}` | owner | remove from the commons |

## Storage backends (pick by `COMMONS_DATABASE_URL` scheme)

| URL | Backend | Isolation enforcement | Use |
|---|---|---|---|
| `sqlite:///data/commons.db` | SQLite (stdlib, one file) | app-layer (`visibility.py` + WHERE) | **trusted team — default** |
| `postgresql://…` | Postgres | app-layer **+ RLS backstop** | public / scale |
| `memory` | in-memory | app-layer | tests only (lost on restart) |

SQLite is the trusted-team default: no new infrastructure, on-philosophy ("kernel
state lives in SQLite/JSON"), schema auto-creates on first open. Postgres adds the
database-level RLS backstop (`migrations/0001+0002`) — reserve it for the public
phase where you want the DB to refuse a leak even if the app query is buggy.

## Local run

```bash
cp .env.example .env          # SQLite default; set SESSION_HASH_SECRET, PUBLIC_ORIGIN
pip install -e ".[dev]"
# Postgres only: python migrate.py   (SQLite auto-creates its schema)
# Local without Firebase: COMMONS_AUTH_PROVIDER=dev COMMONS_DEV_AUTH=1
uvicorn emptyos_commons.app:create_app --factory --host 127.0.0.1 --port 9300
```

## Deploy (single VPS, TLS)

SQLite means **no database container** — just the app + Caddy (automatic HTTPS) +
a data volume.

```bash
cp .env.example .env          # set COMMONS_DOMAIN, COMMONS_PUBLIC_ORIGIN (=https://$COMMONS_DOMAIN),
                              #     COMMONS_SESSION_HASH_SECRET (32+), COMMONS_REGISTRATION_SECRET
# point COMMONS_DOMAIN's DNS A record at this host, then:
docker compose up -d --build
```

Caddy fetches a Let's Encrypt cert for `COMMONS_DOMAIN` and reverse-proxies to the
app. The SQLite file lives in the `commons-data` volume; certs persist in
`caddy-data`. Verify: `https://$COMMONS_DOMAIN/health` → `{"status":"ok"}`. First
teammate registers at `https://$COMMONS_DOMAIN/` (gated by the registration secret
if set). Back the `commons-data` volume up to back up all commons content.

## Tests

```bash
python -m pytest tests/ -v
```

- `test_visibility.py` — the pure isolation proof (no DB): A can never read B's
  private/un-granted content; public→all; shared→grantees only.
- `test_commons_api.py` — the same rule through the real endpoints, across two
  distinct human users, on the in-memory repo + dev auth.

A live-Postgres e2e (asserting the RLS backstop) mirrors
`services/englishos-control-plane/tests/test_e2e_docker.py` and is the next test to
add when a database is wired.

## Identity providers (`COMMONS_AUTH_PROVIDER`)

| Mode | What | Use |
|---|---|---|
| `password` | in-service email+password (pbkdf2, stdlib), `/auth/register` + `/auth/session {email,password}` | **trusted-team default** |
| `firebase` | managed IdP via `FirebaseIdentityVerifier` — owns reset/MFA/federation | product-level upgrade |
| `dev` | insecure bearer = `subject:email` (`COMMONS_DEV_AUTH=1`) | local/test only |

Both paths resolve to the same `VerifiedIdentity`, so **switching providers leaves
sessions + content untouched** — the password hashes simply go unused under a token
IdP. Password mode: set `COMMONS_REGISTRATION_SECRET` for a closed team (an invite
secret), or leave blank for open registration on a trusted network. `IdentityVerifier`
(`auth.py`) is the swap point for any other token IdP (Auth0, your own).

## Posture

- **Open signup by default** (`COMMONS_INVITE_ONLY=false`): any verified identity gets
  a commons-only account on first sign-in. Set invite-only for a closed trusted team.
- **Origin-gated** on every mutating request; `__Host-` session cookie.
- The daemon publishes **outbound through its consent gate + leak-scan** (Rule 18/19);
  nothing leaves a private vault without an explicit, gated publish — see the daemon
  `commons` bridge app (Phase 3).

## Status

Phases 0–2 complete and tested (data model + auth spine + content/ACL with the
isolation proof). Next: the daemon `commons` bridge app (Phase 3) and the douban-like
read surface (Phase 4). See the build plan.
