# Sandbox Usage — lease a pool member, never taskkill it

When a Claude session needs to test a code change against a running daemon,
it goes through the **sandbox-pool plugin** — a small pool of throwaway
EmptyOS daemons on `:9002` and up (default 2 members, configurable to 3).
Each pool member has its own `data_dir` + `vault/` + `emptyos.toml`, so
recycling one can't corrupt the user's `:9000` SQLite WAL or interfere
with the dogfood `:9001` sidecar.

**Why this rule exists.** Before the plugin shipped, the only "Claude can
restart this" daemon was a one-off `:9002` configured by `sandbox/emptyos.toml`
and operated via a `taskkill` Bash recipe. The auto-classifier refused to
authorize `taskkill` even with CIM evidence — "it might be the user's
daemon" is a category-level concern it can't dismiss from a PID alone.
The plugin closes the gap: every pool member is spawned by `plugins/sandbox-pool/plugin.py`,
which writes the PID to its own state file (`data/sandbox/pool.json`)
and only ever terminates handles it owns. A kill request goes through
`POST /sandbox/api/lease/{lease_id}/restart`, which the host daemon
executes with full knowledge of which PID it owns — no `taskkill`
required.

The `.claude/rules/daemon-handling.md` rule **still applies** to `:9000`
(main daemon) and `:9001` (dogfood sidecar). Sandbox-pool ports
(`:9002`+) are the exception.

## The contract

| Step | API | Purpose |
|---|---|---|
| Before any sandbox work | `POST /sandbox/api/lease` body `{"purpose": "<short tag>"}` | Acquire an exclusive lease on one pool member; returns `{lease_id, port, host, expires_at}` |
| Long-running work | `POST /sandbox/api/lease/{lease_id}/touch` | Bump expiry; do this every ~2 min on work that runs over 5 min |
| After code edits | `POST /sandbox/api/lease/{lease_id}/restart` | Recycle the daemon so it loads patched code. Returns when the new daemon answers `/api/health` |
| When done | `DELETE /sandbox/api/lease/{lease_id}` | Release the slot back to the pool |
| Anytime | `GET /sandbox/api/status` | Inspect the pool — who's leased what, who's reachable |
| Debugging a wedge | `GET /sandbox/api/lease/{lease_id}/log?tail=200` | Tail the leased member's daemon log |

All POST/DELETE endpoints return `{"ok": true, ...}` on success and
`{"ok": false, "error": "<short_code>"}` on failure. Common error codes:

| `error` | Means |
|---|---|
| `pool_full` | Every member is leased. The response includes `members[]` with each lease's purpose + expiry so you can decide whether to wait or ask the user |
| `no_such_lease` | The `lease_id` doesn't match any active lease — typically because it expired and was reaped |
| `lease_expired` | The lease was real but TTL elapsed; re-lease and retry |
| `disabled_in_config` | The user has set `[plugins.sandbox-pool] enabled = false`; bail out and surface the diagnosis |
| `inside_pool_member` | We're running INSIDE a pool member — recursion guard refused. Stop |
| `sandbox-pool plugin not loaded` | The plugin failed to load (e.g. removed from the install set). Surface diagnosis + stop |
| `spawn_failed` | The subprocess died during boot. Read `data/daemon.err.log` under the member's dir |

## When the plugin is offline

If `GET /sandbox/api/status` returns 404 or `sandbox-pool plugin not loaded`:

- The store install state may have the plugin disabled — surface to the
  user and ask if they want to flip it on.
- Otherwise, do **NOT** fall back to Bash `taskkill` or `python -m emptyos start`.
  That was the failure mode this plugin replaces. Surface the diagnosis;
  let the user act.

## What pool members ARE and AREN'T for

**Are for:**

- Verifying scaffolding skills (`/eos-new-app`, `/eos-new-plugin`) end-to-end.
- Smoke-testing route shapes + hub-panel/voice-intent contributions after
  a code edit, without paying for a full :9000 restart cycle.
- Running dogfood-agent UI-walk presets via their `daemon_url` argument.
- Validating that import-time changes don't break the boot path.

**Aren't for:**

- Running the user's real apps against the user's real vault — pool
  members have throwaway vaults seeded with one `README.md`.
- LLM work — by default pool members boot with
  `[capabilities.think] providers = ["human"]` so they fail closed for
  cloud calls. Tests must explicitly enable cloud.
- Anything that needs the user's real store install state — pool members
  see every discovered app, gated only by their own `data/store/`.
- Mutating shared infrastructure (the user's vault, demo VPS deploys).

## Configuration

In `emptyos.toml`:

```toml
[plugins.sandbox-pool]
enabled = true          # default true; set false to gate the pool out entirely
pool_size = 2           # 1..3 (clamped); default 2
base_port = 9002        # default 9002 → pool occupies 9002, 9003, (9004)
lease_ttl_s = 300       # default 300s (5 min); per-lease overridable via API
boot_timeout_s = 60     # how long to wait for a fresh member to answer /api/health
autostart = true        # spawn members at boot (vs lazy on first lease)
autoboot_members = false  # eagerly spawn the whole pool at daemon boot (≈300MB RAM)
vault_template = ""     # optional path to a directory whose contents seed each member's vault/
```

Each pool member's config + state lives at `<repo-root>/sandbox-{port}/` (relative to the EmptyOS project root):
- `emptyos.toml` (auto-generated on first boot, NEVER overwritten)
- `data/` (independent SQLite + store state)
- `vault/` (throwaway notes; optionally seeded from `vault_template`)

Pool state (which member holds which lease) persists at
`data/sandbox/pool.json` so a main-daemon restart re-attaches to running
members instead of orphaning them.

## A typical Claude session

```text
Edit some files…

# 1. Lease a sandbox.
POST /sandbox/api/lease {"purpose": "verify app-foo route"}
→ {"ok": true, "lease_id": "lease-ab12cd34…", "port": 9002,
   "host": "http://127.0.0.1:9002", "expires_at": 1234567890.0}

# 2. Restart it so it picks up the edits.
POST /sandbox/api/lease/lease-ab12cd34…/restart
→ {"ok": true, "stage": "start", "port": 9002,
   "host": "http://127.0.0.1:9002", "expires_at": …}

# 3. Probe the route under test.
GET http://127.0.0.1:9002/app-foo/api/status

# 4. If the work runs long, bump the lease.
POST /sandbox/api/lease/lease-ab12cd34…/touch

# 5. Release when done.
DELETE /sandbox/api/lease/lease-ab12cd34…
→ {"ok": true}
```

If at step 1 the response is `{"ok": false, "error": "pool_full"}`,
surface to the user — never wait-loop indefinitely. The body's `members[]`
shows what's holding each slot so the user can decide whether to release
something or just wait.
