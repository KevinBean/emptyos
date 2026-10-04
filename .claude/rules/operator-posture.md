---
paths:
  - "emptyos/web/**"
  - "emptyos/posture.py"
  - "emptyos/capabilities/**"
  - "apps/**/manifest.toml"
  - "services/englishos-control-plane/**"
  - "demo/emptyos.toml"
  - "englishos-cloud/**"
---
# Operator-vs-User Posture — the browser user is not always the operator

EmptyOS ships editions where the human at the browser is a *user of* the daemon
but not the *operator of* the host: the public demo, the portfolio demo, the
hosted EnglishOS learner (one daemon per learner — we own the image, the code,
the keys, the bill), and the control-plane shared mode. The daemon's auth is a
single network gate (one password/token; `docs/AUTH.md`), so without this it
treats whoever passes the gate as the machine's owner — which in those editions
let a signed-in visitor read source and secrets off the host
(`POST /api/apps/{app}/rpc/{method}` → `BaseApp.read("/proc/self/environ")`,
found 2026-09-29).

**Posture is one fact per deployment, fixed at boot — never a per-request role.**
Per-request roles would be the RBAC `docs/AUTH.md` refuses. `[trust] web =
"operator" | "user"` (env `EOS_TRUST_WEB`) → `Config.trust_web` /
`Config.web_is_operator`. Resolution fails closed: explicit wins; else
`demo.enabled`/`cloud.locked` → `user`; else local/private → `operator`; else
public-unset → **refuse to boot**. Unrecognised → `user`.

## The rule when you add a route

**Every new core `@server.*` route and every new app `@web_route` must be
classified.** `scripts/check_route_posture.py` gates on it — a route that is
neither operator-classified nor in the reviewed user set fails preflight.

- **Operator-shaped** — reads host files, rewrites config, changes the network,
  toggles/installs code, dispatches to arbitrary app methods, spends the
  operator's keys, or opens a host file/URL:
  - a **core** route → add its path to `OPERATOR_ROUTES` in `emptyos/posture.py`.
  - an **app** route → `@web_route(method, path, operator=True)`.
- **User-ok** — the user's own vault/app data, their app preferences, status
  reads:
  - a **core** route → add `(METHOD, path)` to `USER_ROUTES` in
    `scripts/check_route_posture.py`.
  - an **app** route → nothing to do (the default is user-ok).

If a route takes a **caller-supplied filesystem path**, do not rely on posture
alone — the `read`/`write`/`search` capabilities already confine paths to the
vault in user posture (`Capability.confine_to`; `search` also refuses the
`code`/`files` domains), but a route that opens a host file directly (kb
`open-local`) must be `operator=True`.

**Two apps gate INSIDE the handler, not with a route flag**, because their
route is user-ok but one code path within it executes host code (2026-09-30):
- `agent` — the `coding` profile offers the whole tool registry (Bash/Write/
  Edit/Python). `/agent/api/sessions` and the chat WS are user-ok (the demo home
  is agent chat), so in user posture the profile is downgraded to the
  tool-narrowed `chat` profile at both the create route (`posture_downgrade`)
  and the turn (`app.py`, covering already-created/forked coding sessions). A
  native provider that runs its own tools is refused with `chat` in that posture.
- `rooms` — CLI participants spawn `claude-cli`/`codex` on the host; `_dispatch_cli_turn`
  refuses them in user posture. Non-CLI agent participants stay allow-by-list
  (`[DO:]` review gate), so they carry no shell.

**Core apps marked `operator=True`** (2026-09-30, after the hostile
review found them unmarked and shipped in core/standard): `run` (shell
execution — RCE), `providers` (edits the live capability chain — prompt/key
exfil), `git` (host repos, push/pull), `system-log` (syslog audit + system
feed), plus settings (network/product/autopilot), store (install/marketplace),
kb (open-local/source-pdf), and `/settings/api/config` strips host paths + the
plugin list in user posture. These are demo-hidden and outside the learner tier
today, so the markers are defence for a future full-app user build (e.g.
control-plane shared mode). **There is no completeness gate for app-route
markers yet** — a new operator-shaped app route can ship unmarked
(`docs/DEFERRED-WORK.md`, the app-route posture checker). Until it exists, marking
an operator-shaped `@web_route` is a manual discipline.

## The four choke points (don't add a fifth)

| # | What | Where |
|---|---|---|
| C1 | Core route policy (403 on `OPERATOR_ROUTES`) | `PostureMiddleware` in `emptyos/web/routes_auth.py::register_posture`, in front of `AuthMiddleware` |
| C2 | App route flag | `web_route(operator=True)` → enforced in `_add_route` (`emptyos/web/server.py`) |
| C3 | Vault path confinement for `read`/`write` | `Capability.execute` → `_enforce_confinement`; set on the caps in `capabilities/setup.py` |
| C4 | Settings write allowlist | `SettingsApp._refuse_key` / `_settable_keys` (the build's own schema is the allowlist) |

Always-on (independent of posture): `vault_map.get_absolute` confines a stored
value to the vault; the vault read routes return relative, not host-absolute,
paths.

## Defence in depth outside the daemon

- **Control plane, per-learner mode:** forward only an allowlist of paths
  (`shared_paths.allowed`) = the plan's app prefixes (`plans.py::plan_apps`) +
  the fixed daemon list. Today per-learner mode forwards everything.
- **Caddy (demo/os/english VPS):** an `operator_deny` block refuses the operator
  paths at the edge, and access logs are on. This is the same list, restated in
  Caddy — keep the two in sync when you add an operator route.
- **Keys:** a hosted daemon holds no shared key it doesn't need. The learner
  build gets a per-learner capped key, not the account key.

## The honest limit

Posture closes the *known doors*; it does not make the image secret. Any code
that runs **inside** a container can read `/app`. A module that must stay secret
even against that runs server-side as a Lane-1 service (`docs/DEPLOYMENT.md`),
not inside the shipped daemon image. Recorded in `docs/DEFERRED-WORK.md`.

## Cross-references

- `docs/AUTH.md` § Operator vs user — the design pin (single-user data stands;
  posture is orthogonal).
- `.claude/rules/demo-mode.md` — posture is the fourth orthogonal knob beside
  `network.mode`, `demo.enabled`, `.eos-personal`.
- `.claude/rules/public-app-pattern.md` — `is_public_request()` is auth-*presence*
  (anonymous vs owner face); posture is host-*operator* (may this browser reach
  operator routes at all). They compose; neither replaces the other.
- `.claude/rules/store.md` — marketplace install is operator-only; the in-app
  `_marketplace_refusal` is the second layer.
- `scripts/check_route_posture.py` + `tests/test_unit_posture.py` — the gate and
  the pins.
