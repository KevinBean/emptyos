# EmptyOS Commons — Next Steps (MVP → trusted-team deploy)

**Where we are:** the core multi-user ability is built + proven (15 service tests
+ 4 bridge tests + a live daemon↔commons e2e on a sandbox). It runs on dev
scaffolding — in-memory store, dev auth, local uvicorn. This plan turns it into
something a real teammate logs into. Scope = **trusted-team** (a handful of
mutually-trusting people sharing a knowledge commons, e.g. an engineering KB).
Public-scale (open signup, moderation, per-user daemons) is explicitly **out of
scope** here — the schema already leaves that door open.

Ordering: **A → (B+C together) → D**, with **E** in parallel and **Step 0** first.

---

## Step 0 — Commit the baseline (do first)

Get the proven MVP into git before hardening, so there's a clean rollback point.

- New branch off `main` (don't commit to main directly).
- Stage: `services/emptyos-commons/`, `apps/public/standard/commons/`,
  `tests/test_unit_commons_bridge.py`.
- Commit `feat(commons): multi-tenant shared-knowledge service + daemon bridge`.
- **Do NOT** commit `sandbox-9002/` artifacts or the store-state change.
- Acceptance: `git status` clean except the intended files; tests still green.

---

## Milestone A — Persistent store: SQLite-first (Postgres = scale option) ⭐

The MVP ran on the in-memory store (loses data on restart). For a **trusted team**
the right persistent backend is **SQLite** — stdlib, one file, zero new infra,
on-philosophy ("kernel state lives in SQLite/JSON"). Isolation is enforced by the
app-layer visibility check (the unit-tested `visibility.py` + WHERE clauses).
Postgres + RLS is reserved for the **public/scale** phase, where a DB-level
backstop against bugs/strangers earns its keep.

The repository **Protocol** already makes this a drop-in: `InMemory` + `Postgres`
exist; add **`SqliteCommonsRepository`**. `visibility.py` is backend-agnostic.

**Tasks**
1. **`emptyos_commons/repositories.py` → add `SqliteCommonsRepository`** implementing
   the same Protocol over `sqlite3` (stdlib): WAL mode, one `commons.db` file. Same
   visibility semantics as the other two (own + public + granted). No RLS — the
   app-layer check is the boundary (acceptable for a trusted team).
2. **`migrations_sqlite/0001_commons.sql`** — the SQLite schema (no RLS, no
   `gen_random_uuid`/`TEXT[]`/`::jsonb`; use `TEXT` PKs via Python `uuid4`, tags as
   a JSON string column, `created/updated` as ISO text). A tiny `migrate_sqlite.py`
   (or auto-create on first open).
3. **`app.py` factory:** select backend by `COMMONS_DATABASE_URL` — `memory` →
   InMemory, `sqlite:///path` → Sqlite, `postgresql://…` → Postgres. Default for a
   trusted-team deploy = `sqlite:///data/commons.db`.
4. **Tests:** run the existing `test_commons_api` flows against the SQLite repo too
   (parametrize the repo fixture: in-memory + sqlite-tempfile). Add a restart-persistence
   test (write, reopen the .db, read it back).

**Acceptance:** the full content + ACL suite passes on SQLite; data survives a
"restart" (reopen the file). No Postgres needed to run the trusted-team commons.

**Postgres (deferred to the public phase):** `PostgresCommonsRepository` +
`migrations/0001+0002` (RLS backstop) already exist. When going public, write the
`test_e2e_postgres.py` backstop proof (raw unscoped `SELECT` as user B still can't
read A's private row) and switch `COMMONS_DATABASE_URL` to Postgres — same code.

---

## Milestone B — Identity

**DONE — in-service password auth** (chosen over Firebase for "simple for our use
now", with Firebase kept as the upgrade path). `emptyos_commons/passwords.py`
(pbkdf2, stdlib) + `set_password`/`password_hash_for` on all 3 repos + the
`/auth/register`, `/auth/session {email,password}`, `/auth/password` endpoints.
`COMMONS_AUTH_PROVIDER=password`; `COMMONS_REGISTRATION_SECRET` gates signup for a
closed team. 13 tests (hashing + register/login/wrong-pw/duplicate/secret/change/
isolation). Both auth paths resolve to the same `VerifiedIdentity`, so upgrading to
Firebase later is a config flip — sessions + content untouched.

**DONE — browser sign-in + feed page** (`emptyos_commons/web/index.html`, served at
`GET /`): register/login forms gated on `/api/me`, then the commons feed with
compose (title/body/visibility/share), per-note share + withdraw for owners, search.
Vanilla JS, no framework. Cookie name falls back to plain `commons_session` when
`session_secure=false` so local http dev works. Completes the Phase-4 read surface.
Live-verified over HTTP (register → login → post → feed → wrong-pw 401); +2 tests.

**Future upgrade (when product-level):** set `COMMONS_AUTH_PROVIDER=firebase` + the
Firebase config; `FirebaseIdentityVerifier` (already written) takes over; password
tables go unused. No data migration.

---

## Milestone C — Deploy the service (Lane 1, TLS)

**DONE — deploy config written** (SQLite ⇒ no DB container; just app + Caddy + a
volume): `Dockerfile` (python:3.12-slim, uvicorn factory, web/*.html via
package-data), `.dockerignore`, `docker-compose.yml` (commons + caddy + persistent
volumes for the sqlite file and the certs), `Caddyfile` (automatic Let's Encrypt for
`$COMMONS_DOMAIN` → reverse_proxy). `.env.example` + README document the flow.

**Live smoke proof (2026-07-10) — PASS:** real uvicorn + throwaway SQLite proved register → session → private/public note → **private-note read denial** → ACL write grant → machine token → logout, with session replay rejected after logout (18/18 checks).
Re-run: `python services/emptyos-commons/tests/manual_live_smoke.py`.
The read-denial check is load-bearing: a smoke that only observes 200s cannot prove `visibility.can_read` gates anything.

**Remaining — the actual deploy (operator step, needs a host + domain):**
1. On a VPS: `cp .env.example .env`, set `COMMONS_DOMAIN`, `COMMONS_PUBLIC_ORIGIN`
   (=`https://$COMMONS_DOMAIN`), `COMMONS_SESSION_HASH_SECRET` (32+),
   `COMMONS_REGISTRATION_SECRET`. Point DNS A record at the host.
2. `docker compose up -d --build`. Verify `https://$COMMONS_DOMAIN/health` green.
3. `docker compose config` validation was skipped locally (no Docker on the dev
   box) — runs clean on the deploy host.

**Acceptance:** `https://$COMMONS_DOMAIN/health` green over TLS; a teammate registers
+ signs in from a browser; origin gate rejects a forged `Origin`.

---

## Milestone D — Durable daemon→commons auth (machine token) — DONE

Long-lived, revocable machine tokens so a daemon stays connected without a 24h
cookie. Shipped (c37723ae):
- `commons_api_tokens` store (Protocol + Postgres/SQLite/in-memory; migration 0003).
  Only the hash is stored; raw token shown once at mint.
- `principal()` falls back to `Authorization: Bearer <token>` (cookie wins if both).
- `POST/GET/DELETE /api/tokens` (mint / list / revoke, own-user scoped).
- Web UI "Connect a daemon" (mint-once / list / revoke).
- Bridge: `commons.api_token` setting, preferred over the cookie via `_auth_header()`.
- +6 tests (memory+sqlite), live-verified over HTTP (mint → Bearer use → revoke → 401).

---

## Milestone E — Polish

- **Tags fix — DONE.** The bridge now pulls tags from the VaultIndex `get_tags()`
  field (which `vault_get_properties` pops out) before `build_payload`
  (`apps/public/standard/commons/app.py::_note_tags`). Mirrors the proven
  `base_app.py` call site; compiles + bridge unit tests green.
- **Remaining (optional):** `tests/test_sys_commons.py` HTTP smoke of the daemon
  app on a leased sandbox; share-from-feed already exists on the web page; docs-sync
  via `/eos-session-wrapup` when merging.

---

## Explicitly out of scope (the public door — later, by choice)

Open self-serve signup, moderation/trust-and-safety, per-user private-daemon
provisioning (lift the control-plane provisioners), profiles/groups. The
account↔daemon decoupling + scale-invariant RLS already make this a *policy flip +
provisioner wiring* later, not a rebuild. Don't build it until the trusted-team
version is in real use and you've decided to go public (a consumer pivot that
brushes the engineering-pilot / NIW focus).

---

## Rough effort

| Milestone | Effort | Blocking? |
|---|---|---|
| 0 commit baseline | 15 min | do first |
| A SQLite store (+ persistence test) | ~half day | **yes — persistence** |
| B Firebase auth + sign-in page | ~half–1 day | yes |
| C deploy + TLS | ~half day | yes |
| D machine token | ~half day | for sustained use |
| E polish | ongoing | no |

Minimum for "a teammate can log in and share a note": **0 + A + B + C.** D makes the
daemon side durable. E is quality.
