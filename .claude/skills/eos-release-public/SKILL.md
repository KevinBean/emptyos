---
description: Ship a release from local → private origin → public emptyos repo → demo VPS. Use when the user says "ship", "publish to public", "snap to public", "push a release", "release v0.X.Y", "deploy to demo", or wants unreleased commits on demo.binbian.net. Wraps the bump → push private → release-public.py → redeploy-demo flow with the smoke-test guard that v0.2.7-0.2.10 cost us. Distinct from /eos-release (that one packages tiered dists into dist/; this one promotes the working tree to the public-facing repo + live VPS).
---

# EmptyOS Release Public

Promote local commits to the public-facing surface: `KevinBean/emptyos` (public source repo, what strangers clone) and `demo.binbian.net` (live VPS, what visitors see). Wraps `scripts/release-public.py` + the VPS redeploy step with the safety gates that have actually mattered in real releases.

**Distinct from `/eos-release`:**
- `/eos-release` cuts a tier-scoped distribution into `dist/` (for handing to another machine, packaging an offline bundle). It's about *what* ships.
- `/eos-release-public` (this one) promotes HEAD to the public repo + VPS. It's about *where* it shows up.

The two compose: package a tier with `/eos-release`, then promote with this skill if the same release should also go live publicly. Most sessions use only this one.

## When to use

- "Ship", "release", "publish v0.X.Y", "push to public", "snap to public"
- "Update demo", "deploy to demo", "demo is behind"
- Two or more unreleased commits on `main` that should be public
- A bug fix or content change the live demo needs

## When NOT to use

- Single-commit work-in-progress that's still being iterated — wait until it's stable
- Changes to `apps/personal/` only (those are gitignored, can't ship publicly)
- Vault content changes (devlog posts, site source) — use `/eos-devlog-publish` and the Publish app instead; this skill ships the *codebase*, not site content
- A failed prior release on the same version — the version must increment

## Architecture context

```
LOCAL                 PRIVATE GIT          PUBLIC GIT          DEMO VPS
D:/emptyos     ──>    KevinBean/    ──>    KevinBean/    ──>   /opt/emptyos
                      emptyos-private      emptyos             (demo.binbian.net)
                      (full history)       (one squashed       (rebuilds containers
                                            commit per          via redeploy-demo.sh)
                                            release + tag)
```

`scripts/release-public.py` does the snapshot. `scripts/redeploy-demo.sh` runs on the VPS to fetch + rebuild. Read `docs/RELEASING.md` once for the full architecture; this skill is the runner that calls into both.

## Process

### Phase 1 — Plan the release

Pull together what will ship:

```bash
git status
git log origin/main..HEAD --oneline
grep '^version' release.toml
```

Show the user:

```
Planned release:
  Current version  : 0.2.X
  Next version     : 0.2.Y    (patch bump unless --minor/--major)
  Commits to ship  :
    <sha> <subject>
    <sha> <subject>
  Files touched    : <count> (Python: <N>, frontend: <N>, docs: <N>)

  Public repo      : KevinBean/emptyos  → tag v0.2.Y, force-push main
  Private repo     : will tag v0.2.Y on HEAD
  VPS              : redeploy step is yours to run (SSH).

Proceed? (y/n)
```

Pick the bump:
- **patch** (default): bug fixes, copy tweaks, single-app changes — `0.2.51 → 0.2.52`
- **minor**: new app, new capability, breaking-but-additive — `0.2.x → 0.3.0`
- **major**: removing or renaming load-bearing surface (manifest format, kernel API) — `0.x → 1.0`

If unsure, default to patch and ask only when the diff suggests otherwise.

### Phase 2 — Pre-flight gates

Each gate is a hard stop. Don't paper over.

#### 2a. Smoke-test imports of changed Python modules

The single most expensive lesson in release history: v0.2.7 through v0.2.10 were each one wasted release because a Python file was edited locally, the working tree compiled fine in the running daemon (modules cached), but the file had a syntax error or undefined import that only surfaced when the public snapshot tried to boot fresh on the VPS. The fix is mechanical:

```bash
# For every Python file changed since the last release:
for f in $(git diff --name-only origin/main..HEAD | grep '\.py$'); do
    mod=$(echo "$f" | sed 's|/|.|g; s|\.py$||')
    python -c "import $mod" 2>&1 | grep -v "^$" && echo "FAIL: $mod" || echo "ok: $mod"
done
```

For changed files that are scripts (not importable modules — e.g. `scripts/foo.py`), do `python -m py_compile <file>` instead.

If any module fails to import: stop. Fix the underlying error, commit, restart Phase 2. Releases are cheap to repeat; broken public releases are expensive to undo.

This step exists because of a hard-won lesson (smoke-test imports before release — 4 wasted releases when skipped). Keep it.

#### 2b. Working tree clean

```bash
git status --short
```

`release-public.py` itself enforces this — it refuses to run with a dirty tree. But surface it earlier so you don't run all the other phases just to fail at the script call.

If there are untracked one-off artifacts (screenshots, scratch outputs) that aren't part of the release:

- Stash them: `git stash push -u -m "scratch — not part of release" -- <paths>` (then `git stash pop` after release)
- Or move/delete them
- Or add them to `.gitignore` if they're a recurring class
- Never `git add .` to clear the slate — that's how secrets ship

#### 2c. On `main`, fast-forward with private origin

```bash
git rev-parse --abbrev-ref HEAD          # must say "main"
git fetch origin && git status -sb       # must say "up to date" or "ahead"
```

If behind: `git pull --ff-only`. If diverged: stop and ask — divergence on `main` means something else pushed; investigate before snapshotting.

#### 2d. Safety scans

```bash
python scripts/check-personal.py
python scripts/check-branding.py
```

Both must report **CLEAN**. `release-public.py` re-runs them inside the snapshot too, so a leak that survived gitignore (e.g. a tracked file that quotes a personal path) is double-caught. If either reports violations, fix at the source — don't add to the ignore list.

### Phase 3 — Bump + commit + push private

```bash
# Edit release.toml [release].version per Phase 1 spec
# (one-line change: 0.2.X → 0.2.Y)

git add release.toml
git commit -m "release: v0.2.Y

<one-line summary of what's in this bump>

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"

git push origin main
```

The commit message body should list what the release carries — useful in `git log` later when triaging which release first introduced something. Keep it terse; the public commit message is generated separately by `release-public.py`.

### Phase 4 — Snapshot to public

```bash
python scripts/release-public.py v0.2.Y
```

What the script does (don't re-implement — trust it):

1. Re-verifies working tree clean
2. Re-runs `check-personal` + `check-branding` against working tree
3. `git archive HEAD` into a temp dir (tracked files only, no `.git/`, no untracked)
4. Defensive cruft sweep: strips `caddy.exe`, `results/`, `dist/`, `build/`, `__pycache__/`, `*.pyc`
5. Re-runs scans against the snapshot
6. Clones the existing public repo
7. Replaces tracked files with the fresh snapshot
8. Commits as `EmptyOS v0.2.Y`, tags `v0.2.Y`, pushes to public main (force-push is intentional — snapshot model)
9. Tags private HEAD with the same version

If it fails partway:
- **Before push** (steps 1–7): no remote state changed, just rerun after fixing
- **After public push, before private tag**: rare; the private tag step is idempotent, just rerun with `--no-tag-private` to skip the public push and only do the private tag

Use `--dry-run` to walk through everything except the actual force-push. Useful when something looks off and you want to see what would ship without committing to it.

### Phase 5 — VPS redeploy

The skill cannot SSH from the user's machine on its own (no automated SSH context). Show the command, let the user run it:

```bash
ssh root@<vps-host> "cd /opt/emptyos && bash scripts/redeploy-demo.sh"
```

The script on the VPS:
1. `git fetch origin && git reset --hard origin/main` (force-sync — handles snapshot force-pushes)
2. `docker compose down`
3. `docker compose build --no-cache` (so dep changes in `pyproject.toml` actually take effect — caching here has bitten us before)
4. `docker compose up -d`
5. Polls `/api/health` for up to 60s
6. Smoke-checks Python deps + greps startup logs

If the user has an SSH alias configured, suggest using that. If they don't have SSH access set up at all, the demo stays on the previous version until they do — surface that clearly rather than pretending the deploy is done.

After the user confirms the redeploy completed, verify the live site:

```bash
curl -sI https://demo.binbian.net | head -5
# Expect 200 OK or 302 → /login
```

If 502 / 503 / connection refused: VPS daemon didn't come back up. Have the user check `docker compose logs` on the VPS. Most common cause is exactly the smoke-test miss from Phase 2a.

### Phase 6 — Report

```
Release v0.2.Y → public

Local
  release.toml        : 0.2.X → 0.2.Y
  Commits             : <N>
  Smoke-test imports  : <N> Python files, all clean
  Safety scans        : check-personal CLEAN, check-branding CLEAN

Private (KevinBean/emptyos-private)
  Pushed              : <commit-sha>
  Tagged              : v0.2.Y

Public (KevinBean/emptyos)
  Snapshot commit     : <commit-sha>
  Tagged              : v0.2.Y
  URL                 : https://github.com/KevinBean/emptyos/releases/tag/v0.2.Y

Demo VPS (demo.binbian.net)
  Redeploy command    : ssh root@<host> "cd /opt/emptyos && bash scripts/redeploy-demo.sh"
  Status              : pending (user runs SSH) | confirmed live (200 OK)
```

If the user explicitly authorized you to run the SSH redeploy via a configured alias and it succeeded, mark Status as confirmed and include the live verify result.

## Safety

- **Never `--force` push to `KevinBean/emptyos-private`.** That's the WIP repo with full history; force-push is for the public snapshot only and `release-public.py` is the only thing allowed to do it.
- **Never bump the version on a failed release.** If Phase 4 fails, fix the cause and retry the same version. If Phase 4 succeeded but Phase 5 reveals a runtime bug, the right move is a *new* release with the fix, not a re-cut of the same tag.
- **Never skip Phase 2a.** It exists because we paid for it four times in a row.
- **Never include vault content in this flow.** Site posts, devlog drafts, demo seed data live in the vault and ship via `/eos-devlog-publish` + the Publish app. This skill ships the codebase only.
- **Never auto-SSH without explicit user authorization.** Even with an alias configured, the redeploy step is theirs to trigger — it touches a public service. Show the command, wait for confirmation.
- **The public force-push is reversible only by another release.** Public history regenerates with each snapshot, so a bad release can't be `git revert`-ed cleanly. The recovery is always: fix locally, bump version, run again.
- **Don't release on red tests.** If `/eos-release` was supposed to run beforehand and reported failures, treat them the same way you would there — fix or explicitly waive with the user, don't ignore.

## Relationship to other skills

- **Before ship**: `/eos-simplify` on recent commits catches convention slips that would otherwise ride along publicly. `/eos-system-check-and-fix --check` finds structural drift.
- **Around ship**: `/eos-release` packages a tier-scoped `dist/` if a downloadable bundle is also wanted. Compose them when both are needed; usually only this skill runs.
- **After ship**: `/eos-session-wrapup` records the release in the devlog (and, for public-worth sessions, `/eos-devlog-publish` promotes it to eos.binbian.net afterwards).

## Known gaps

- No automatic version-bump-from-conventional-commits — the user picks patch/minor/major manually. The diff usually makes it obvious; if it doesn't, surface the ambiguity in Phase 1 rather than guessing.
- VPS health verification is curl-only. Deeper checks (specific app endpoints, capability probes) would catch the regression class where the daemon boots but a specific feature is broken — defer until that bites in real life.
- The redeploy script doesn't roll back on failure. If the new container fails to start, the previous one stays down. This is intentional (the failure is loud and the fix is forward, not back) but means a bad release means a brief demo outage. Mitigated by Phase 2a but not eliminated.
