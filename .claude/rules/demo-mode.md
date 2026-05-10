# Demo Mode + Privacy Contract

EmptyOS has three orthogonal knobs that together decide what's deployed, what's gated, and what's never shipped. They are independent — combine them as needed; don't conflate them in code or docs.

| Knob | Layer | Decides | Set in |
|---|---|---|---|
| `network.mode` | network / runtime | Where the daemon listens + whether auth is required | `[network] mode = "local" \| "private" \| "public"` |
| `demo.enabled` | runtime UX | Whether the daemon presents itself as a demo (banner, BYOK, app filtering, cloud auto-consent) | `[demo] enabled = true` |
| `.eos-personal` + `[app] private = true` | release-time / runtime | What never reaches the public snapshot | `.eos-personal` (regex), manifest flag (whole app) |

Self-hosting on a VPS = `network.mode = "public"` *without* `demo.enabled`. The public live demo at `demo.binbian.net` = both. A laptop = `local`, both off.

## What `demo.enabled` does

When `demo.enabled = true`, the kernel:

1. **Pre-approves cloud providers** — `cloud_consent` policy is forced to `"always"`. Visitors opt in by entering their own BYOK key in Settings; the consent prompt is replaced by per-key entry. (`emptyos/kernel/__init__.py`)
2. **Filters apps** — apps listed in `demo.hide_apps` *or* declaring `[app] private = true` in their manifest are removed at discovery time. They're invisible to nav, dependency resolution, and routing. (`emptyos/kernel/app_loader.py`)
3. **Surfaces a demo banner** in the boot response so the UI can render the "this is a demo" chrome. (`emptyos/web/server.py`)
4. **Wipes runtime state on restart** when `demo.reset_on_restart = true` — everything under `data/` is removed before the kernel opens any SQLite handle, except `data/secrets/` (BYOK key cache). (`Kernel._demo_reset_state`)
5. **Seeds sample content** when `demo.seed_on_boot = true` — for each running app, runs `apps/<id>/demo/seed.py`'s async `seed(app)` function. Per-app failures are logged to syslog and never break the boot. (`Kernel._demo_seed_apps`)
6. **Toggles client-side affordances** via `eos.js` feature detection.

## What `.eos-personal` does

A regex list at the repo root, one pattern per line. `scripts/check-personal.py` reads it and refuses to release / commit if any matches a tracked file.

- **Pre-commit (opt-in):** `python scripts/check-personal.py --install-hook` writes a `.git/hooks/pre-commit` that runs `--staged` on every commit. Existing hooks are preserved as `.pre-eos.bak`.
- **Release (mandatory):** `scripts/release-public.py` calls it twice — once against the working tree, once inside the snapshot tarball — and aborts on any match.
- Patterns cover personal identity, paths, coordinates, dates. Update `.eos-personal` when you find a new shape of leak.

## What `[app] private = true` does

A manifest flag in `apps/<id>/manifest.toml`:

```toml
[app]
id = "myapp"
private = true   # don't show in demo, don't ship to public
```

Two enforcement points:

- **Runtime:** when `demo.enabled = true`, `app_loader.discover()` skips the app entirely. Same effect as listing it in `demo.hide_apps`, but the app self-declares so every demo deployment honours it without operator intervention.
- **Release:** `release-public.py` calls `check_no_private_apps()` against the working tree and the snapshot. If any `apps/**/manifest.toml` has `private = true`, the release aborts. The expectation is that genuinely private apps live in `apps/personal/` (gitignored) — the flag is a safety net for apps that ended up under `apps/` but shouldn't ship.

## Decision table

| Want | Use |
|---|---|
| Hide an app from one specific demo deployment | `demo.hide_apps = [...]` in that deployment's `emptyos.toml` |
| Hide an app from every demo (anywhere) | `[app] private = true` in its manifest |
| App is personal-only and should never be in the repo at all | Put it under `apps/personal/` (gitignored) |
| Block a content pattern (PII, paths, dates) from ever being committed | Add a regex to `.eos-personal` |
| Block third-party brand names from user-facing strings | Add a regex to `.eos-branding` |

## Reset + seed deployment

The demo container (`docker-compose.demo.yml`) sets `EOS_DEMO_ENABLED=true`. The mounted `demo/emptyos.toml` enables `reset_on_restart` and `seed_on_boot`. Combined: every container restart wipes per-visitor state and re-populates sample content, so the demo presents a known-clean environment to every new visitor without external scripts or cron jobs.

For a per-app seed, drop a file at `apps/<id>/demo/seed.py`:

```python
async def seed(app):
    await app.add_entry("Welcome to the demo", mood="curious")
```

Seed scripts run after app autostart. Use the app's normal SDK methods — don't reach into `data/` directly. Failures are isolated per app.

## What demo mode is NOT

- **Not the same as `network.mode = "public"`.** A self-hoster running EmptyOS on their own VPS uses public mode without demo. They keep their full app set, real cloud-consent prompts, and persistent state.
- **Not a security boundary on its own.** `auth_token` (mandatory in private/public modes) is the access gate. Demo mode is a UX layer on top.
- **Not where you put privacy patterns.** That's `.eos-personal`. Demo mode hides apps; `.eos-personal` strips content.

See also: `CLAUDE.md` § Deployment, `docs/DEPLOYMENT.md`, `.eos-personal`, `.eos-branding`.
