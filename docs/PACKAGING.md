# Packaging EmptyOS distributions

Use this guide when turning an app set into a release or deciding which
visibility mechanism owns a requirement. The short rule is: **tiers decide what
ships; the Store decides what runs for one user.** Runtime code never reads a
release tier.

## Choose the right layer

| Mechanism | What it decides | Use it when |
|---|---|---|
| `release.toml` tiers | Which apps, plugins, skills, and optionally engines enter a packaged snapshot | Defining a distributable product or release boundary |
| App folder track | The source-level release posture: `public/` can ship publicly, `extension/` can ship in named private targets, and `personal/` stays local | Placing first-party code before any tier is resolved |
| Store state | Which installed apps and plugins are enabled for one daemon | Giving a user reversible runtime choice after installation |
| `demo.hide_apps` | Apps hidden from one demo deployment at discovery time | Curating a particular public demo without changing its package |
| `[app] private = true` | An app hidden from every demo and rejected by the public-release safety gate | Defending against accidentally shipping an app that belongs in `personal/`; no shipped app currently relies on this fallback |
| `ENGLISHOS_PUBLIC_APPS` | Control-plane routes that anonymous visitors may reach; the proxy marks them with `X-EOS-Public` | Giving a hosted product a deliberately anonymous face |

These layers compose, but they do not replace one another. A tier can ship an
app that the Store later disables. A demo can hide an app that remains present
in the package. The `[tiers.demo]` and `[tiers.labs]` tables are useful packaging
and documentation definitions, but they are **not runtime modes or gates**; the
kernel never reads them.

## Author a distribution from a Space

A workspaces **Space** is the runtime portal where a goal-oriented app set is
composed. Its relationship to packaging is deliberately one-way: a Space can
derive a tier snippet, but a tier never creates or changes a Space.

1. Compose and verify the Space in `/workspaces/`.
2. Request `GET /workspaces/api/spaces/<slug>/bundle`.
3. Copy the returned `[tiers.<slug>]` snippet into `release.toml` and review the
   derived hero app plus members.
4. Add `[targets.<name>]` only when the distribution has a real destination.
5. Build a daemon snapshot with `python scripts/release.py <target>`, or build
   the copy-based `dist/` package with `python scripts/package-release.py <tier>`.

The workspaces app retains two extension paths that do not yet have first
consumers: manifest opt-in through `[provides.workspace]` and machine config
through `[apps.workspaces]`. They remain available for the first real consumer;
do not promote them into kernel or release-tier behavior.

## Set the distribution homepage

Ship the core `hub` app in every daemon distribution. It is an essential,
contribution-driven surface: when a tier contains fewer apps, the hub naturally
renders fewer panels.

For a branded or goal-specific landing page, set the deployment's configuration
instead of forking hub:

```toml
[os]
home = "/workspaces/#<slug>"
```

A hero app route may be used instead. `hub-life` is not a branding precedent:
its parallel implementation exists because it merges a different panel data
union (`hub-life.panel` plus `hub.panel`).

## Package for multiple users

The EmptyOS daemon remains single-user: one vault, one human, one daemon. Its
`network.auth_token` and `network.password` are an inner network gate, with the
browser password producing the `eos_session` cookie.

A hosted multi-user product belongs at the orchestration layer:

1. The control plane in `services/englishos-control-plane` authenticates a user
   with Firebase, provisions or locates that user's daemon, and proxies to it
   with the inner daemon token.
2. The proxy stamps `X-EOS-Actor` so actions remain attributable.
3. Shared items go through `services/emptyos-commons`, whose owner, visibility,
   ACL, and Postgres RLS checks form the enforceable shared-data boundary.
4. Anonymous faces use manifest `public_routes`; the configured public-app
   allowlist causes the control plane to pass `X-EOS-Public` to those routes.

Do not add a users table or per-user vault paths to the daemon. The complete
design pin and operational rationale are in [AUTH.md](AUTH.md).

The control plane still carries legacy `ENGLISHOS_*` names alongside
`EOS_CONTROL_*` aliases. Completing that generic rename is deferred until the
first non-EnglishOS multi-user product needs it.
