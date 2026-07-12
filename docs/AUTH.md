# Auth — Design Pin

The EmptyOS **daemon** is single-user by design. This document pins that decision
so future edits don't accidentally drift toward a multi-tenant users-table inside
the daemon.

**Don't conflate three different things** (this is the most common confusion):

1. **Single-user daemon** — the constraint pinned here. One daemon serves one
   human, one vault, in *every* `network.mode`. This never changes.
2. **Multi-user collaboration** — *realized*, and orthogonal to #1. It lives in a
   **separate `commons` service** (`services/emptyos-commons`), not in the daemon.
   Each user keeps their own single-user daemon + vault; sharing happens by
   publishing *outbound* to the commons (which holds an `owner` + `visibility` +
   ACL per item). Works in any `network.mode` because it's an outbound connection.
3. **Multi-tenant product** — many users, hosted. Achieved by **daemon-per-user +
   the control-plane** (`services/englishos-control-plane`, which provisions and
   routes per-user daemons) **+ the commons** as the shared layer. Multi-tenancy
   lives in the *orchestration layer*; the daemon (#1) is still single-user even
   here.

The model in one line: **1 vault = 1 user; N users = N vaults = N daemons; the
commons is the one shared, non-vault store.** "Multi-user" is never "many people in
one vault" — that's the thing this pin forbids.

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

## Multi-user — realized (commons + per-user daemons)

The decision (Path A, pinned 2026-04): **multi-user means multi-instance, not
multi-tenant-inside-one-daemon.** As of 2026-06 this is no longer a future path —
it's built. The reverse-proxy sketch below is realized by
`services/englishos-control-plane` (authenticates the human, routes to *their*
daemon), and the **shared layer** that the sketch lacked is
`services/emptyos-commons` (a separate store holding only published content +
per-item `owner`/`visibility`/ACL). Each user still gets their own single-user
daemon + vault; collaboration is publishing *out* to the commons.

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

How it works (and what stays true):

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

The deepest reason, though, is **the vault is plaintext markdown**. A
`visibility: private` line in a `.md` file is a *label, not a lock* — anyone
who can read the files reads all of them, tag or no tag. So per-note access
control over a shared plaintext vault is **impossible** without either not
giving people the files or encrypting per-recipient (which would destroy the
human-readable-vault principle). That forces the whole design:

- **Private content** stays in your single-user vault. Confidentiality at rest
  is your machine's job (OS permissions + full-disk encryption), exactly like any
  personal file folder — and that's fine, because it's all *yours*.
- **Shared content** lives in the **commons**, which is enforceable precisely
  because it *never hands out the store*: it gates each read through the service
  (auth + RLS) and returns only the bytes you're allowed. "Private to A" there
  means B never receives the bytes.

Two consequences worth pinning:

- **Portability.** Access control lives in the commons DB, not in the daemon
  (swappable) or the plaintext vault (unenforceable). So it survives re-pairing:
  connect a new daemon to your vault, or a new vault to your daemon — the
  who-can-see-what is untouched, because it was never in either of them.
- **Mode-independence.** The commons is reached *outbound*, so multi-user
  collaboration works in any `network.mode` — even a hardened, no-inbound,
  loopback-only `local` daemon collaborates by reaching out. `network.mode`
  governs who can reach *in*; the commons governs sharing. They're orthogonal.

## Operational notes

- **Setting credentials:** edit `emptyos.toml` directly. There is no
  in-app password change UI. Restart the daemon after editing.
- **Rotating credentials:** change the value in `emptyos.toml`, restart.
  All existing cookies invalidate (cookie value still equals the old
  token; middleware compare fails on next request).
- **Sharing access with a second human:** don't share the daemon or vault —
  stand up a second daemon (the vault is theirs, not yours). To *collaborate*,
  both daemons publish to the **commons** (`services/emptyos-commons`); shared
  content lives there with per-user access control, private vaults stay separate.
- **Read-only public landing pages** (e.g. published article on a
  demo deployment) use `[provides.web].public_routes` in the app's
  manifest — those bypass the auth gate without weakening it for the
  rest of the daemon. See `apps/radio/` for an example.

## See also

- `emptyos/web/server.py` — the auth middleware itself (~80 lines).
- `emptyos/kernel/config.py` — `auth_token`, `auth_required`, `network_mode`.
- `services/emptyos-commons/` — the multi-user **shared layer** (accounts,
  per-item `owner`/`visibility`/ACL, RLS). Keeps the daemon single-user; the
  daemon's `commons` bridge app publishes *outbound* to it.
- `services/englishos-control-plane/` — the **per-user-daemon orchestration**
  (auth → route to the user's own daemon). The realized form of the reverse-proxy
  sketch above; the foundation for a multi-tenant *product*.
- `.claude/rules/public-app-pattern.md` — auth-presence branching (anonymous vs
  authenticated) within one daemon, and the control-plane passthrough.
- `.claude/rules/demo-mode.md` — how `demo.enabled` interacts with the
  network gate (it doesn't — they're orthogonal).
- `docs/DEPLOYMENT.md` — Lane 2 (single-tenant daemon) carries a vault; a
  multi-tenant product is Lane 2 daemon-per-user (control-plane) + the commons
  as a Lane-1 shared service.
