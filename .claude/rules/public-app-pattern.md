---
paths:
  - "apps/**"
  - "emptyos/sdk/base_app.py"
  - "services/**"
---
# Public-App Pattern — one app, two faces (anonymous vs authenticated)

A general, reusable architecture for serving an app **differently before and
after login** — a public/teaser face to anonymous visitors and the full app to
authenticated owners — from **one codebase, one daemon**, with **no kernel
changes** and **no per-request user identity** (which the single-user pin in
`docs/AUTH.md` forbids). This is auth-**presence** branching (*is there a
credential?*), never identity (*which user?*).

**Reference consumer:** `apps/public/labs/radio` (`_is_public_request`,
`[provides.web] public_routes`). **SDK core:** `BaseApp.is_public_request()` +
the pure `_request_is_public()` in `emptyos/sdk/base_app.py`. **Cloud wiring:**
`services/englishos-control-plane` public-app passthrough.

## The two topologies (and why a header is needed)

The same app must work in both deployment shapes, which detect "anonymous"
differently:

| Topology | How a daemon is reached | How "anonymous" is detected |
|---|---|---|
| **Direct daemon** (kiosk, shared public deploy, `radio` at `/radio/live`) | Browser → daemon. Auth middleware lets `public_routes` through without creds. | The auth middleware **validated** any cookie/bearer and recorded its verdict on `request.state.public_face`; `_request_is_public` honours that. Bearer *presence* alone is NOT the signal — a forged `Authorization: Bearer <garbage>` would otherwise pass as authenticated (fixed 2026-07-10, commit `926c7d85`). |
| **Behind the control plane** (multi-user cloud) | Browser → control plane → daemon, and the proxy **always injects its own inner-token bearer**. | Credential-absence is invisible at the daemon, so the control plane sets **`X-EOS-Public: 1`** to request the public face. |

`BaseApp.is_public_request()` handles both: True on the `X-EOS-Public` marker
**or** (direct topology) auth-configured + no-credentials. With no auth
configured (local mode) everything is already open, so it returns False (serve
the full face).

**Why trusting `X-EOS-Public` is safe:** the public face is always a *subset* of
the full face. The header can only ever *restrict* a caller to less access,
never escalate. A browser cannot forge "more access" with it. The control-plane
proxy also never forwards arbitrary browser headers — it only sets the marker
itself (`proxy.forward(extra_headers=...)`) — so the daemon sees `X-EOS-Public`
solely when the control plane decided the request is anonymous public-app
traffic.

## How an app adopts it (3 steps)

### 1. Declare the public routes (manifest)
```toml
[provides.web]
prefix = "/myapp"
public_routes = ["/live", "/api/feed", "/api/audio"]  # read-only surfaces only
```
The daemon auth middleware (`emptyos/web/server.py`, the `public_routes` loop)
appends each to the app prefix and lets unauthenticated requests reach *only*
those routes. **List read-only routes only** — never a write/admin route.

### 2. Branch handlers on `self.is_public_request(request)`
```python
@web_route("GET", "/api/feed")
async def api_feed(self, request):
    if self.is_public_request(request):
        return {"items": self._public_sample()}   # teaser / read-only
    return {"items": self._full_feed()}            # owner's full data
```
Pass the same flag down into any method that filters content (an app may have a
`public`-flagged subset of items, like `radio`'s channels/personas). Keep
app-specific public markers (e.g. a `?kiosk=1` query, a `/api/live/` path) in the
app and delegate the credential/header core to `self.is_public_request`:
```python
def _is_public_request(self, request) -> bool:
    if not self._share_enabled():            # app's own master switch
        return False
    if request.query_params.get("kiosk") == "1":
        return True
    return self.is_public_request(request)   # shared core
```

### 3. (Cloud only) mark the app public on the control plane
```
ENGLISHOS_PUBLIC_APPS=myapp,radio        # csv of app prefixes
ENGLISHOS_PUBLIC_UPSTREAM=public-demo    # routing_key of a shared public daemon
```
The control-plane catch-all (`app.py` `learner`) then routes **anonymous**
traffic for a public app to the shared public daemon with `X-EOS-Public: 1`;
**authenticated** users still hit their own daemon (full face); anonymous
traffic to a non-public app still gets `401`. Provision one shared public daemon
(`register_instance.py`, periodically reset via `learner_lifecycle`) as the
upstream — public traffic never touches a per-user daemon.

## When to use it / when not to

**Use it** for an app with a genuinely shareable read-only surface: a public
stream/feed, a sample lesson, a share-link view, an SEO landing. The value is a
no-login on-ramp without a second codebase.

**Don't use it** for:
- Apps with no meaningful anonymous view (settings, admin, anything write-first).
  Just leave them out of `public_routes` / `ENGLISHOS_PUBLIC_APPS` — they stay
  login-gated by default.
- The free *trial* flow. That is a different mechanism: anonymous **accounts**
  (Firebase anonymous auth → own persistent daemon) give a full, persistent,
  upgradeable experience. Public-app is for *no-account* read-only surfaces.
- Anything needing per-user logic inside one daemon — that's the refused kernel
  rewrite. Different users = different daemons. For genuine **multi-user content
  sharing** (per-user accounts + owner/visibility/ACL), don't reach for this
  pattern — that lives in the separate `commons` service (`services/emptyos-commons`),
  which each single-user daemon publishes to *outbound*. This pattern is only the
  anonymous read-only *public face* of one daemon; the commons is the multi-user
  *shared layer*. See `docs/AUTH.md`.

## Security checklist

Both 2026-07-10 vulnerabilities (F9, F10 in the SaaS audit) were failures of one
of the first two invariants. An anonymous route is auth-exempt; the app alone
owns what a caller may read (F9) or write (F10).

- **Gate on the validated face signal, never credential presence.** Read the
  anonymous/owner branch from `is_public_request()` (which honours the
  middleware's *validated* `request.state.public_face`), not from "is there a
  bearer/cookie". Presence is forgeable; validity is not. (F9)
- **Escape every anonymous value for the format it lands in.** A `public_routes`
  route MAY be a write (bookme's booking form is one; boards' public Form view —
  `apps/public/standard/boards/public_form.py`, 2026-08-22 — is the second: it
  strips any submitted key that isn't a real fillable column on that board
  before it ever reaches `coerce()`, so a stranger can't smuggle a `tags`/
  `created`/computed-field value through), but attacker text then
  flows into sinks — vault frontmatter (`vault_index._serialize_fm` and
  `emptyos/frontmatter.py` are line-based → newline/`---` injection; the
  block-end scan is line-anchored, so a `---` *inside* a value no longer
  truncates the block, but an injected `---` on its own line still ends it),
  `.ics` (CRLF injection), HTML, SQL.
  Escape at each sink AND reject control chars at the write boundary. (F10)
- Handlers must **default to the public (lesser) face** and only widen when
  `is_public_request` is False — fail closed.
- Don't put owner-only data behind a `public_routes` path even in a method that
  *usually* checks the flag — every code path on a public route must assume an
  anonymous caller is possible.
- The shared public daemon holds **no real user data** — seed it with sample
  content and reset it on a schedule.

## SDK extraction status (CLAUDE.md rule 9)

`radio` was consumer #1 (inline `_is_public_request`). The generic core was
extracted to `BaseApp.is_public_request()` / `_request_is_public()` when the
second consumer (the control-plane public-app passthrough + this general
architecture) arrived. Radio now delegates to it. A third consumer should reuse
`self.is_public_request(request)` directly — do not re-implement the credential
sniffing.
