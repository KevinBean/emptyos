# Auth — Design Pin

EmptyOS is single-user by design. This document pins that decision so future
edits don't accidentally drift toward a multi-tenant users-table inside the
daemon.

## The shape we keep

A daemon process serves **one human**. Authentication is a network gate, not
an identity system. There are exactly two credentials:

| Credential | Audience | Where it lives | What it gates |
|---|---|---|---|
| `network.auth_token` | Machines (CLI, API clients, deep links) | `emptyos.toml` (gitignored) | `Authorization: Bearer …` + `?token=…` deep links |
| `network.password` | Humans (browser login form) | `emptyos.toml` (gitignored) | `POST /login` → `eos_session` cookie |

Both are accepted by the same middleware. Both gate the whole daemon equally.
Neither is stronger than the other — they're alternate input shapes for the
same single trust boundary.

When `network.mode = "private"` or `"public"`, at least one of these MUST be
set. Mode `"local"` skips the gate entirely (loopback only).

## What this is NOT

- **Not a users table.** There is no `users` table, `User` model, `roles`
  field, or per-user vault path. The vault is the daemon's hard drive; the
  daemon has one vault.
- **Not RBAC.** No permissions, no per-route ACLs, no admin/viewer split.
- **Not OAuth.** No identity providers, no OIDC, no third-party login.
- **Not session management beyond a 30-day cookie.** No active-sessions
  list, no per-device tracking, no forced logout-everywhere. If you need
  to revoke, rotate the secret in `emptyos.toml` and restart.
- **Not TOTP / 2FA / passkeys.** A single password + a long random token
  is the entire credential surface.

If you find yourself wanting any of the above for *the daemon itself*, stop
and re-read the multi-tenant section below — that's the signal you're
solving the wrong problem at the wrong layer.

## Multi-user — the path we will take

The decision (Path A, pinned 2026-04 in memory): **multi-user means
multi-instance, not multi-tenant-inside-one-daemon.**

```
┌─────────────────────────────────────────────────────────┐
│  Reverse proxy (Caddy / Traefik / Authentik)            │
│  - Terminates TLS                                       │
│  - Authenticates the human (Clerk / Auth0 / Authentik)  │
│  - Injects `X-Tenant: <id>` header                      │
│  - Routes /tenant/<id>/* → that tenant's container      │
└─────────────────────────────────────────────────────────┘
        │                    │                    │
        ▼                    ▼                    ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  daemon A    │    │  daemon B    │    │  daemon C    │
│  vault A     │    │  vault B     │    │  vault C     │
│  data/A      │    │  data/B      │    │  data/C      │
│  one human   │    │  one human   │    │  one human   │
└──────────────┘    └──────────────┘    └──────────────┘
```

When that day comes:

1. The daemon **does not change**. It still serves one human, has one vault,
   has one set of credentials. The `X-Tenant` header is consumed by the
   proxy/orchestrator, never by app code.
2. The daemon trusts the proxy to have authenticated the user — typically
   by a shared secret in a header (`X-EmptyOS-Trust: <secret>`) or by
   binding to a Unix socket the proxy alone can reach.
3. The auth provider (Clerk / Auth0 / Authentik / your own) owns user
   accounts, password resets, MFA, audit logs. **We never roll our own.**
4. Tenant lifecycle (create / destroy / suspend) is a deploy-layer
   concern: spin up a container, mount a vault, route a subdomain. Not a
   daemon concern.

The single-user `auth_token` and `password` stay in place as the daemon's
local trust boundary even when fronted by a proxy, because the proxy might
be misconfigured. They're the **inner gate**. The proxy is the **outer
gate**. Defense in depth.

## What we will refuse to add

When future-you (or a contributor) opens a PR that does any of these,
this document is the reason to push back:

- A `users` table, `User` SDK class, or `current_user()` helper.
- Per-user permissions inside any app (`if user.is_admin: …`).
- A login form that creates accounts or resets passwords.
- Per-user vault subdirectories (`vault/<user_id>/journal/…`).
- An "invite a friend" flow inside the daemon.
- API key issuance to third-party apps (use the proxy for that too).

The rule of thumb: if a feature only makes sense in a world where the
daemon serves >1 person, it doesn't belong in the daemon. It belongs in
the proxy, the orchestrator, or the bundled product profile.

## Why this shape

EmptyOS's identity model is "the vault is the user". One vault, one user,
one daemon. Adding a users table inside the daemon would mean either:

- One vault for many users — collapses the "vault is your hard drive"
  metaphor. Whose journal is `50_Journal/2026-05-10.md`? Who owns the
  capture inbox? The data model breaks down immediately.
- Many vaults for many users — but then you have N daemons-worth of
  state inside one process. You've reinvented multi-instance, badly,
  with all the privacy bugs of shared address space.

Multi-instance keeps the data model honest: each user gets their own
EmptyOS, end of story. The cost is one container per user. That cost is
the right cost.

## Operational notes

- **Setting credentials:** edit `emptyos.toml` directly. There is no
  in-app password change UI. Restart the daemon after editing.
- **Rotating credentials:** change the value in `emptyos.toml`, restart.
  All existing cookies invalidate (cookie value still equals the old
  token; middleware compare fails on next request).
- **Sharing access with a second human:** don't. Stand up a second
  daemon. The vault is theirs, not yours.
- **Read-only public landing pages** (e.g. published article on a
  demo deployment) use `[provides.web].public_routes` in the app's
  manifest — those bypass the auth gate without weakening it for the
  rest of the daemon. See `apps/radio/` for an example.

## See also

- `emptyos/web/server.py` — the auth middleware itself (~80 lines).
- `emptyos/kernel/config.py` — `auth_token`, `auth_required`, `network_mode`.
- `.claude/rules/demo-mode.md` — how `demo.enabled` interacts with the
  network gate (it doesn't — they're orthogonal).
- `docs/DEPLOYMENT.md` — Lane 2 (single-tenant daemon) is the only
  vault-bearing deployment shape today.
