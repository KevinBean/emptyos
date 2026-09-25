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

**One exception, and its fences: a shared daemon that holds no per-user data.**
A public demo already serves many anonymous visitors from one daemon. The
control plane's *shared mode* (`services/englishos-control-plane/README.md`
§ Shared mode, added 2026-09-25 for the hosted Cable Pulling+ site) does the
same behind a sign-in: visitors are identified in the control plane, never in
the daemon, and nothing a visitor does is stored per visitor. It stays inside
this pin only while all of these hold:

- the product keeps no per-user state (the hosted Cable Pulling+ build refuses
  saved projects when the hosted switch is on);
- the control plane forwards only an allowlist of paths (`SHARED_PATHS`,
  required), because every visitor rides the daemon's inner token;
- every forward carries `X-EOS-Public: 1`, so an app that branches on
  `is_public_request()` serves its restricted face.

A product whose users need saved state of their own is daemon-per-user (#3), not this.

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

### The shell login exchange — a scoped third secret, and what it is worth

The desktop shell (`products/_shared/`) already holds `auth_token`: it reads
`emptyos.toml` to talk to the daemon. The webview it opens does not, so in
private mode the user is asked to log in **by hand, per webview profile**, to a
daemon running as them on their own machine. `emptyos/web/auth_exchange.py`
closes that, and it is worth being precise about what it does and does not add:

- It adds a **third secret, deliberately scoped**: an exchange code. Calling it
  "not a credential" would be word-play — it is independently presentable, has
  its own store and TTL, and redeeming it yields a session. What keeps the
  two-credential model honest is that a code cannot be *obtained* without one of
  the two (mint is bearer-only) and cannot be *spent* from another machine.
- **A redeemed code yields the same cookie `/login` would**, whose value is the
  `auth_token` itself, for 30 days. Be clear-eyed about that: the code is
  short-lived, but what it buys is not. That is why redeeming is restricted to
  the loopback socket — and why the locality check reads `request.client.host`
  and never `X-Forwarded-For`, which the caller sets.
- **Mint** (`POST /api/auth/shell-exchange`) is **bearer-only and refuses a
  session cookie**, even though the middleware would admit one to any `/api/`
  route. A cookie is what a *page* holds; an XSS anywhere in the daemon could
  otherwise mint a code and carry a working login off the machine.
- **Redeem** (`GET /auth/shell-exchange?code=…&next=…`) is auth-exempt by
  necessity — it is the route that makes you authenticated — so it carries its
  own gate: the **peer socket** must be loopback (strictly this machine, not
  the private LAN, and never a forwarded-for header), the code must be
  unexpired and unused, `next` is clamped to a path on this daemon, and a wrong
  code counts against the same per-IP limit as `/login`. A request carrying
  **no** code answers 400 without counting: otherwise any page the user visits
  could lock them out of `/login` with five `<img src=…>`.
- A **code, not the token**, because the token is permanent and would land in
  the address bar, history, and any log that records a URL. A code is worth 60
  seconds, once.

Dark behind `[network] feature.shell-exchange.enabled`, and not registered at
all without an `auth_token` — there would be nothing to exchange. When the
routes are absent the shell falls back to the ordinary one-time `/login`, which
is why every failure in `shell_core.mint_exchange_code` answers `None` rather
than raising.

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
  rest of the daemon. See `apps/extension/english-learning/radio/` for an example.

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
