# Releasing — Private → Public → VPS

The canonical flow for shipping a change. Three locations, three commands.

## Architecture

```
LOCAL                       PRIVATE GIT                PUBLIC GIT             DEMO VPS
D:/emptyos          ──>     KevinBean/         ──>     KevinBean/      ──>    /opt/emptyos
(working tree)              emptyos-private            emptyos                (demo.binbian.net)
                            (full WIP history)         (one commit per        (visitors land here)
                                                        release; tags too)
```

| Repo | Visibility | History | Purpose |
|---|---|---|---|
| `emptyos-private` | private | Full WIP — every commit | Your scratch + backup |
| `emptyos` | public | Curated — one squashed commit per release, accumulates over time | What strangers see |
| `/opt/emptyos` on VPS | public clone | Same as `emptyos` | What the demo serves |

Why two repos: avoids leaking past mistakes (early commits had a Google Maps API key, personal paths). Lets you make rough commits without polishing them for public.

## Daily flow — making a change

1. Work in `D:/emptyos`. Local origin points at `emptyos-private`.
2. Commit freely. Imperfect messages are fine; the public never sees them.
3. Push to private when convenient: `git push origin main`

Nothing special. This is just normal git.

## Release flow — when you want a change public

Three steps. **Always in this order.**

### Step 1 — Bump the version

Edit `release.toml`:

```toml
[release]
version = "0.X.Y"   # bump from previous
```

Versioning: patch for bug fixes (0.2.5 → 0.2.6), minor for breaking changes (0.2.x → 0.3.0).

### Step 2 — Commit + push to private

```bash
cd D:/emptyos
git add release.toml <other-changes>
git commit -m "<your message>

Co-Authored-By: ... (if Claude Code helped)"
git push origin main
```

### Step 3 — Snapshot to public

```bash
python scripts/release-public.py v0.X.Y
```

Script does:
1. Verifies working tree is clean (refuses dirty)
2. Runs `check-personal.py` + `check-branding.py` against the working tree
3. Runs the same scans against the snapshot
4. Strips known-cruft (caddy.exe, results/, dist/, __pycache__, *.pyc)
5. Clones the existing public repo
6. Replaces tracked files with the fresh snapshot
7. Commits ("EmptyOS v0.X.Y"), tags v0.X.Y, pushes to public main
8. Tags the private HEAD with the same version

Always test with `--dry-run` first when in doubt: `python scripts/release-public.py v0.X.Y --dry-run`

## Deploy flow — when you want the live demo updated

After the release lands on public, the VPS needs to fetch + rebuild:

On the VPS via SSH:

```bash
cd /opt/emptyos && bash scripts/redeploy-demo.sh
```

Script does:
1. `git fetch origin && git reset --hard origin/main` (force-sync; handles divergence)
2. `docker compose down`
3. `docker compose build --no-cache` (so dep changes in pyproject.toml take effect)
4. `docker compose up -d`
5. Polls `/api/health` for up to 60s
6. Smoke-checks critical Python deps (fastapi, edge_tts, etc.)
7. Greps startup logs for known-good / known-bad lines

If it errors, the script exits cleanly with the failing message — paste that for diagnosis. Does **not** wipe the data volume; user-created vault content survives across rebuilds (only the daily reset cron clears it).

## Pre-release checklist

Before bumping the version, verify locally:

- [ ] `git status` is clean (or all WIP intentional)
- [ ] No personal data in changes: `python scripts/check-personal.py`
- [ ] No third-party brand names in user-facing text: `python scripts/check-branding.py`
- [ ] If touching imports: `python -c "from emptyos.<module> import <Class>"` succeeds
- [ ] If touching a daemon-loaded path: boot the daemon locally to catch `NameError`/`ImportError` BEFORE shipping. The release-public.py runs scans but doesn't boot a kernel — many of v0.2.7-v0.2.10's churn was "ship → fail to boot on VPS → patch → re-ship."
- [ ] If touching demo config or docker-compose.demo.yml: re-read the file end-to-end before commit
- [ ] If adding a Python dep: confirm it's in `pyproject.toml` (not just installed locally)

## Pitfalls + recovery

### "Working tree dirty" — release script refuses

Either commit the pending changes (good) or stash them (`git stash` → release → `git stash pop`).

The script exits before any push, so you can fix and rerun safely.

### "ImportError" on the VPS after redeploy

You shipped a Python file that doesn't import cleanly. Symptom: `bash scripts/redeploy-demo.sh` reports `daemon didn't respond in 60s` and the smoke check shows missing modules or stack traces.

Fix:
1. Find the import error in `docker logs emptyos-demo`
2. Patch locally
3. Bump patch version (v0.X.Y → v0.X.Y+1)
4. Release + redeploy

Avoid by smoke-testing imports locally before release: `python -c "from emptyos.<changed-module> import <Class>"`.

### "Dep not installed in container" after redeploy

You added a Python dep in pyproject.toml but the rebuild used a stale Docker layer cache. Symptom: `pip show <pkg>` returns "not found" inside the container even though the dep is in pyproject.toml.

Fix: `bash scripts/redeploy-demo.sh` always uses `--no-cache` to prevent this. If you ran `docker compose up -d --build` manually instead, that may use the cache. Always use the script.

### "Public repo history diverged from private"

If the public repo got force-pushed at some point (early in the project lifetime this happened), the VPS's local clone has the old branch and `git pull` may stall. The redeploy script's `git fetch + git reset --hard origin/main` handles this.

### "I shipped a secret to public"

Damage-control sequence:
1. **Rotate the secret immediately** at the provider (don't just delete from code; the value is permanently in the public history)
2. Patch the code (replace the value with an env-var read or remove it)
3. Bump version + release as normal
4. Optionally `git filter-repo` the public repo to remove the secret from history. Strangers may have already cloned, but new clones won't see it.

The release-public.py runs `check-personal.py` + `check-branding.py` **on every release** to prevent this. Add new patterns to `.eos-personal` / `.eos-branding` whenever you find a new class of secret to guard against.

## When NOT to release

- The local daemon doesn't boot — fix locally first
- Tests are red on the changed app — fix or document the regression
- You haven't actually committed the change to private — release-public.py will refuse on dirty tree
- It's not actually ready — bump the version when there's something users will care about, not on every commit

## Reference: the four scripts

| Script | What | When |
|---|---|---|
| `scripts/check-personal.py` | Scans tracked files for personal-data patterns | Every commit (CI) + every release |
| `scripts/check-branding.py` | Scans tracked files for forbidden brand-name leaks in user-facing text | Every commit (CI) + every release |
| `scripts/release-public.py vX.Y.Z` | Snapshot working tree → public repo | Each release |
| `scripts/redeploy-demo.sh` | Pull + rebuild + verify on the VPS | Each VPS update |

All four are idempotent and safe to re-run.
