---
paths:
  - "plugins/sandbox-pool/**"
  - "apps/extension/dev/sandbox/**"
  - "tests/fixtures/sandbox/**"
  - ".claude/skills/eos-sandbox-verify/**"
---

# Sandbox-Driven Testing — Claude's restart loop without touching `:9000`

Companion to `.claude/rules/sandbox-usage.md` (the HTTP API spec) and
`.claude/rules/daemon-handling.md` (the hands-off rule for `:9000`/`:9001`).
This file is the **operator workflow** — when Claude needs to verify a
code change end-to-end, this is the sequence.

The user's main daemon on `:9000` is **off-limits** for restarts.
`restart.bat` kills every `python.exe`, including the user's actively-used
daemon. Asking the user to restart is friction Claude should avoid.

The sandbox-pool plugin owns daemons on `:9002+`. They share the live
source tree, so Claude's edits propagate to the leased member on
**restart of that member**, while the user's daemon stays untouched.

## Start of a test session

```
GET  http://127.0.0.1:9000/sandbox/api/status
```
Confirm at least one member is `idle` (or `dead`, which auto-boots on
lease).

```
POST http://127.0.0.1:9000/sandbox/api/lease
body: {"purpose":"<short tag>", "ttl_s": 1800, "think_providers": ["openai-mini"]}
```
Returns `{lease_id, port, host}`. **`think_providers`** is the crucial
override — default is `["human"]`, which fails closed on every LLM call.
Pass `["openai-mini", "human"]` (or `["claude-cli", "human"]`) for any
test that exercises `app.think()`. Override is applied + member restarts
inline; the call returns only after `/api/health` is back up.

**Tip — bind the lease to a stable `session_id`** so re-leasing returns the
*same* member (idempotent reacquire) and you can `restart`/`release` it by
session without tracking the `lease_id` (`POST /sandbox/api/lease/_/restart`
with `{"session_id": ...}`). The binding survives a `:9000` restart. See
`.claude/rules/sandbox-usage.md` § "Session binding". (Note the
`think_providers`-wipe gotcha below is about a bare `/restart` rebooting from
base config — re-issue the lease with `think_providers` to re-apply it,
session binding or not.)

> **Gotcha — a later `/restart` wipes the `think_providers` override.** The
> override is applied at *lease* time (which restarts the member to take
> effect). If you then call `POST /lease/{id}/restart` again — e.g. out of
> habit "to load my code edit" — the member reboots from its **base config**
> (`think = ["human"]`) and every `think()` silently fails with
> `No available provider for capability 'think'` (and `generate`/`summarize`
> return `ok: False`). When the member was dead, or `think_providers` changed
> its providers, the lease *already* restarted with fresh code, so a second
> restart is both redundant and harmful. If you genuinely need to restart later
> (you leased an already-running member and then edited code), re-apply the
> override by **re-issuing the lease with `think_providers`** — a bare
> `/restart` won't carry it. Always re-confirm with the `/api/capabilities`
> check below after any restart.

> **Gotcha — a lease can hand you a live member without restarting it.** If
> the member is already running and its toml already lists the `think_providers`
> you pass, the override is a no-op and **nothing restarts** — so an edit to the
> member's `emptyos.toml` (a feature flag) or to Python made after that process
> started is **not loaded**. Measured 2026-09-21: a flag added to
> `sandbox-9002/emptyos.toml` never reached the running member, and a whole
> verification batch ran with the gate under test switched off. Before trusting
> a run, compare the member's process start time with your last edit
> (`Get-NetTCPConnection -LocalPort <port> -State Listen` → `Get-Process -Id … |
> Select StartTime`); if the process is older, restart it and re-check
> `/api/capabilities`.

```
GET  <host>/api/capabilities | jq '.think[].name'
```
Confirm the override stuck. Expect the requested provider listed first,
not `human` — **and `"available": true`**, not just listed.

> **Gotcha — the override alone may not register the provider (2026-06-11).**
> `think_providers=["openai-mini"]` only patches the *providers list*; the
> generated member toml carries **no `[capabilities.think.openai-mini]`
> section**, so capability setup never instantiates the provider and the
> chain collapses to `human`-only. And even once registered, the member's
> **cloud-consent policy defaults to `ask`** — headless, nobody answers the
> modal, every cloud call waits out the consent timeout and is skipped
> (downstream `select()` silently returns its default; `propose_*` returns
> `[]`). Fix both by appending to `sandbox-{port}/emptyos.toml` (the plugin
> never overwrites an existing toml) before/after the lease, then restarting:
>
> ```toml
> [capabilities.think.openai-mini]
> host = "https://api.openai.com"
> api_key_env = "OPENAI_API_KEY"
>
> [cloud]
> consent = "always"   # sandbox member only — never the main daemon
> ```
>
> Root fix (not done yet): the sandbox-pool template/`_apply_capability_override`
> should write the provider section + member-local consent itself.

> **Gotcha — `kill_failed` on an old member.** If a member predates the
> current `:9000` daemon (re-attached pid), the pool's own `taskkill` can
> fail (`override_stop_failed` / `kill_failed`) and retries don't help.
> Don't raw-taskkill it (the auto-classifier refuses anyway). Workaround:
> **park** the stuck member with a plain no-override lease so the next
> `think_providers` lease falls through to a *dead* member (fresh spawn, no
> kill needed), then release the park lease. The stuck member clears on the
> user's next `restart.bat`.

## Seed scenario fixtures

Each sandbox member has its own `<repo-root>/sandbox-{port}/vault/` +
`data/`. Vault persists across restarts of the same member — once seeded,
state sticks until the directory is deleted.

Bootstrap a known scenario with the matching fixture under
`tests/fixtures/sandbox/`:

```
python tests/fixtures/sandbox/orgs_marketing.py <host>
```

Fixtures are **idempotent** — safe to re-run; they only create what's
missing. They hit real `@web_route` handlers, not a parallel seed API.

When writing a new fixture, follow the convention in
`tests/fixtures/sandbox/__init__.py`. One file per scenario; exports
`seed(host: str) -> dict`; runnable as a CLI.

## Edit cycle

For each code change:

| Change | Action |
|---|---|
| Python file under `apps/`, `engines/`, `plugins/`, `emptyos/` | `POST <main>/sandbox/api/lease/{id}/restart` (sync, returns when `/api/health` is up) |
| HTML / CSS / JS under `apps/*/pages/` or `emptyos/web/static/` | No restart — the daemon serves static files from disk per request. Just refresh the page |
| Fixture data only | No restart, no fixture-rerun — the vault already holds the state |

Probe the change:
```
curl <host>/<app>/api/<endpoint>
```

If the test exceeds the lease TTL (default 30 min), touch the lease
before it expires:
```
POST <main>/sandbox/api/lease/{id}/touch  body: {"ttl_s": 1800}
```

## End of session

```
DELETE http://127.0.0.1:9000/sandbox/api/lease/{lease_id}
```
The vault and data directories are preserved for the next session's
fixture-seed to inherit. The slot returns to the `idle` pool.

## What Claude must NEVER do

- Run `restart.bat`, `stop.bat`, or `python -m emptyos start` from any
  Bash/PowerShell tool. The main daemon is the user's, full stop.
- `taskkill` any `python.exe` — even when something appears wedged. Diagnose
  via `GET <main>/sandbox/api/status` + `GET <main>/sandbox/api/lease/{id}/log`
  and let the user act if the sandbox itself misbehaves.
- Touch `data/*.db*` files in any directory.
- Skip the `DELETE /lease/{id}` step at session end — burns a pool slot.
- Pour test data into the user's real vault (the `notes.path` from
  `emptyos.toml`). Always probe `host` (the sandbox URL), never
  `localhost:9000` for writes.
- Mix probe surfaces: the sandbox API itself lives on `:9000` (it's an
  app of the main daemon). The app routes under test live on the leased
  member's `<host>` (e.g. `:9002`). Reading from one and writing to the
  other corrupts the test.

## When the sandbox can't help

Real cases where Claude should ask the user to restart `:9000` instead
of using the sandbox:

- **The change touches the kernel boot path** (`emptyos/kernel/__init__.py`,
  capability registration, provider chain). The sandbox WILL test this —
  but if it crashes the member, you've burned a pool slot rather than
  the user's working environment, which is the point. Use the sandbox.
- **The change touches `sandbox-pool` plugin itself**. Restarting the
  member won't reload the plugin (the plugin lives in `:9000`). Ask
  the user to `restart.bat`. This rule's first cycle is one such case.
- **The test genuinely needs the user's real vault content** (e.g.
  reproducing a vault-data bug). Defer to user. Don't try to copy.
- **The test exercises Windows-specific tooling** (global-hotkey,
  system-tray, voice-api, ComfyUI). These services aren't in the sandbox
  config and aren't worth wiring in for a one-off test.

Everything else: use the sandbox.

## Quick reference

The sandbox API is an app of the **main** daemon, so in `network.mode = "private"`
every `/sandbox/api/*` call needs the bearer token. The member's own routes do
not. Omitting it fails badly rather than loudly: `curl -s` swallows the
`{"error":"unauthorized"}`, `jq -r .host` then yields the string `null`, and
every later call in the script reports something that looks like a broken route.

```
# Session bootstrap
TOK=$(python -c "import tomllib;print(tomllib.load(open('emptyos.toml','rb'))['network'].get('auth_token',''))")
A="Authorization: Bearer $TOK"

status=$(curl -s -H "$A" http://127.0.0.1:9000/sandbox/api/status)
lease=$(curl -s -X POST -H "$A" http://127.0.0.1:9000/sandbox/api/lease \
  -H 'Content-Type: application/json' \
  -d '{"purpose":"my-test","ttl_s":1800,"think_providers":["openai-mini"]}')
host=$(echo "$lease" | jq -r .host)
lease_id=$(echo "$lease" | jq -r .lease_id)
[ "$host" = "null" ] && { echo "lease failed: $lease"; exit 1; }
python tests/fixtures/sandbox/orgs_marketing.py "$host"

# Edit cycle
# (edit files in D:/emptyos/...)
curl -s -X POST -H "$A" "http://127.0.0.1:9000/sandbox/api/lease/$lease_id/restart"
curl -s "$host/orgs/api/orgs"          # the member — no auth header

# Session end
curl -s -X DELETE -H "$A" "http://127.0.0.1:9000/sandbox/api/lease/$lease_id"
```

Reference impl: `plugins/sandbox-pool/plugin.py` (lease + override +
restart), `apps/extension/dev/sandbox/app.py` (HTTP routes),
`tests/fixtures/sandbox/orgs_marketing.py` (first fixture).
