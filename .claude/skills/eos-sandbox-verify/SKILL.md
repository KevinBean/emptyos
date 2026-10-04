---
name: eos-sandbox-verify
description: Verify a Python change end-to-end against a real running daemon without touching the user's :9000 — lease a sandbox pool member, configure it, exercise write paths against its throwaway vault, release it. Use when a change needs a live daemon to prove and a read-only probe of :9000 is not enough (a write path, a route that mutates a note, a capability chain). NOT for offline-provable logic (engines, SDK helpers, pure functions — just run pytest), NOT for read-only probes of :9000 (curl it directly), and never a licence to restart or taskkill :9000 / :9001.
---

# Sandbox-Verify

`:9000` is the user's daemon on the user's real vault. You may probe it and you
may read its logs; you may not restart it, and you must not exercise a **write**
path against it. A sandbox pool member is the carve-out: Claude-owned, its own
throwaway vault, restartable through an API that knows which PID it owns.

`.claude/rules/sandbox-usage.md` is the API contract and
`.claude/rules/sandbox-driven-testing.md` is the operator rationale. This skill
is neither — it is the sequence, plus the five things that cost time every run
and are not obvious from either document.


## Prerequisites

The daemon on `:9000` must be up, because the sandbox pool is an app of it —
`curl http://127.0.0.1:9000/sandbox/api/status` and confirm at least one member is
idle or dead. In `network.mode = private` every `/sandbox/api/*` call needs the bearer
token from `emptyos.toml`; without it the lease silently returns `null` and every later
call looks like a broken route (`.claude/rules/sandbox-usage.md`).

## When to reach for it

Only when a live daemon is genuinely required:

- a **write** path (a POST that creates or mutates a note),
- a route whose behaviour depends on app settings or a capability chain,
- an end-to-end check that the daemon returns what a page expects.

If the logic is provable offline — an engine, an SDK helper, a pure function, a
`test_unit_*` — run pytest instead. A sandbox costs a lease, a boot, and a slot.

## The sequence

Every call to `:9000` needs the bearer token in private mode. Read it from
config; never paste it into a file, a commit, or a report.

```bash
TOK=$(python -c "import tomllib;print(tomllib.load(open('emptyos.toml','rb'))['network'].get('auth_token',''))")
A="Authorization: Bearer $TOK"

# 1. Lease. `session_id` makes re-leasing idempotent — the same member comes
#    back, so you never have to track the lease_id.
curl -s -X POST -H "$A" -H 'Content-Type: application/json' \
  -d '{"purpose":"<short tag>","ttl_s":1800,"session_id":"<stable id>"}' \
  http://127.0.0.1:9000/sandbox/api/lease

# 2. Restart so the member loads your edits (skip if the lease just booted it).
#    READ THE REPLY: anything but "ok": true means nothing restarted. On
#    "lease_expired", re-issue step 1 and restart again.
curl -s -X POST -H "$A" http://127.0.0.1:9000/sandbox/api/lease/<id>/restart

# 2b. Prove the member runs your code: its process must be newer than your
#     last edit. (PowerShell; <port> and <edited file> are yours to fill in.)
#     $c = Get-NetTCPConnection -LocalPort <port> -State Listen
#     (Get-Process -Id $c.OwningProcess).StartTime
#     (Get-Item <edited file>).LastWriteTime
#     Older process = old code. Restart again before testing anything.

# 3. Exercise the change against the MEMBER, never :9000. HOST is the `host`
#    field of the lease reply — base_port is configurable, so don't assume 9002.
curl -s -X POST -H 'Content-Type: application/json' -d '{...}' "$HOST/<app>/api/<route>"

# 4. Release.
curl -s -X DELETE -H "$A" http://127.0.0.1:9000/sandbox/api/lease/<id>
```

Note the split: the sandbox **API** lives on `:9000` (it is an app of the main
daemon) and needs auth; the **routes under test** live on the member and do not.
Reading from one and writing to the other invalidates the test.

## The five that cost time

Four were hit and measured; the `think_providers` one is
inherited from `sandbox-driven-testing.md` and has **not** been re-verified here.
Marked per row, so a later reader knows which claims have evidence behind them
and which are a restatement.

**A test that ran on old code, with no error anywhere.** *(measured 2026-09-24,
twice in one session)* Two ways in, same result:

- The lease expired during a long session. The restart then answered
  `{"ok":false,"error":"lease_expired"}` and restarted nothing, while the member
  kept serving the previous code. Nothing downstream fails: every probe answers,
  just from code you have since changed.
- A lease hands back a live member without restarting it when nothing in its
  config changed, so an edit made after that process started is not loaded.

Both are closed by step 2's reply check and step 2b's start-time comparison.
Do both on every restart, not only the first; this bit after several
successful rounds.

**Auth, or a bare `{"error":"unauthorized"}`.** *(measured)* In `network.mode = "private"`
every `/sandbox/api/*` call needs the bearer. The member's own routes do not.

**A later `/restart` wipes `think_providers`.** *(inherited, not re-verified)* The override is applied at
*lease* time, which itself restarts the member — so a second restart "to pick up
my edit" reboots from base config, `think` collapses to `human`, and every LLM
call fails closed. Re-issue the **lease** with `think_providers` to re-apply it;
a bare restart will not carry it. Confirm with
`curl <host>/api/capabilities | jq '.think[].name'` — and check `available` is
true, not merely that the provider is listed.

**App settings files are read per call, so writing one needs no restart.** *(measured)* Each
member has its own `sandbox-<port>/data/apps/<app>/`. Dropping a settings JSON
there takes effect on the next request. This is how you switch off an expensive
path — an enrichment step, a cloud call — so the verification stays fast, free,
and deterministic:

```bash
# <port> comes from the lease reply — base_port is configurable, so don't
# assume 9002.
python -c "
import json, pathlib
p = pathlib.Path('sandbox-<port>/data/apps/<app>')
p.mkdir(parents=True, exist_ok=True)
(p / '<settings-file>.json').write_text(json.dumps({'<expensive_flag>': False}), encoding='utf-8')"
```

**A rejected fixture reads like a broken route.** *(measured)* The member's vault is empty, so
seed through the app's own API rather than writing notes by hand — and if a
create returns an error, suspect your fixture before the code. Input validators
are stricter than they look (the dictionary's word regex is letters-only, so
`obviate2` is refused and every later call in the chain reports `None`). Assert
the seed succeeded before reading anything into the result.

## Cross-checks worth running

- `GET /sandbox/api/status` before leasing — a dead member auto-boots on lease;
  `pool_full` means say so and stop, never wait-loop.
- `GET /sandbox/api/lease/{id}/log?tail=200` when a member misbehaves.
- Compare against `:9000` **read-only** when you want to know whether a
  behaviour is a code change or a data difference.

## What this never authorises

Restarting, `taskkill`-ing, or deleting `data/*.db*` for `:9000` or `:9001`;
running `restart.bat` or `python -m emptyos start`; or pouring fixtures into the
user's real vault. If a member is wedged, read its log and surface the
diagnosis — do not kill it by PID; the pool API owns that
(`.claude/rules/daemon-handling.md`).

Also: a sandbox member shares the live git tree, so never exercise
`git add`/`commit` there, and GPU work can contend with the main daemon's
ComfyUI — verify that on `:9000`'s own terms instead
(`.claude/rules/sandbox-driven-testing.md`).

## Cross-references

- `.claude/rules/sandbox-usage.md` — lease/touch/restart/release API, error
  codes, `session_id` binding, `source_root` worktree mode.
- `.claude/rules/sandbox-driven-testing.md` — when the sandbox cannot help.
- `.claude/rules/daemon-handling.md` — the hands-off rule for `:9000`/`:9001`.
- `.claude/rules/environment.md` — `127.0.0.1` not `localhost` from Python, and
  the PowerShell `-UseBasicParsing` hang.
- `tests/fixtures/sandbox/` — reusable scenario seeds, idempotent by convention.
