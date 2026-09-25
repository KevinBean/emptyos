# eos-session-wrapup — docs-sync + data-gathering commands

Read this file when running Step 1 (Docs Sync), Step 4's data gather, or Step 5
(Site Sync). The spine (`SKILL.md`) owns the sequence + safety rules; this file
owns the exact commands and patch patterns.

---

## Step 1a — Scan manifests

```bash
# Count all apps (core + personal)
find apps/ apps/personal/ -name "manifest.toml" -not -path "*/_retired/*" 2>/dev/null | wc -l

# Count plugins — exclude _retired/ like the apps line above, or this reports a
# phantom extra plugin every wrapup (plugins/_retired/obsidian-cli/manifest.toml)
# and tempts the next session to "fix" a correct count in plugins.md.
find plugins/ -maxdepth 2 -name "manifest.toml" -not -path "*/_retired/*" 2>/dev/null | wc -l

# Count endpoints (web_route decorators)
grep -r "@web_route" apps/ apps/personal/ plugins/ --include="*.py" 2>/dev/null | wc -l

# Count apps with custom UI
find apps/ apps/personal/ -name "index.html" -path "*/pages/*" -not -path "*/_retired/*" 2>/dev/null | wc -l

# Count topology edges (app-to-app dependencies)
grep -r "^apps = " apps/ apps/personal/ --include="*.toml" -not -path "*/_retired/*" 2>/dev/null
```

## Step 1b — Build app table

For each manifest.toml, extract:

```python
import tomllib
with open(manifest_path, "rb") as f:
    m = tomllib.load(f)
app_id = m["app"]["id"]
app_name = m["app"]["name"]
has_ui = (manifest_path.parent / "pages" / "index.html").exists()
```

## Step 1c — Sync release manifest

If apps or plugins changed, update `release.toml` tier lists:

```bash
# Check if new community apps exist that aren't in any tier
for d in apps/*/; do
    id=$(basename "$d")
    grep -q "\"$id\"" release.toml || echo "NOT IN RELEASE: $id"
done
```

New community apps should be added to the appropriate tier in `release.toml`:

- **core**: infrastructure essentials (capture, note, task, search, link, settings, system-log, run)
- **standard**: everything else that's generic and community-ready

Run `python scripts/package-release.py --check` to verify tiers resolve correctly.

## Step 1d — Sync public docs

If the session changed architecture, capabilities, or app inventory:

- **README.md**: update app counts, tier table, capability list if changed
- **docs/GETTING-STARTED.md**: update if config format or setup flow changed
- **docs/APP-DEVELOPMENT.md**: update if SDK API signatures, decorators, or manifest format changed
- **docs/APPS.md**: regenerate via `python scripts/generate_apps_doc.py` if apps changed
- **docs/SKILLS.md**: regenerate via `python scripts/generate_skills_doc.py` if skills changed

Only update sections with factual changes (counts, lists, API signatures). Don't rewrite prose.

## Step 1e — Patch CLAUDE.md

Use exact string replacement on these patterns:

1. **Architecture box**: `Apps (N, ALL first-class` → update N
2. **Section header**: `## N Apps` → update N
3. **Topology**: `Live dependency graph (N nodes, M edges)` → update N, M
4. **What's Done header**: `### N Apps (M with custom UI pages), K endpoints` → update all
5. **Plugin header**: `### N Plugins` → update N
6. **App list** (under "What's Done"): regenerate full comma-separated list

---

## Step 3 (Class B) — KB integrity scanners

Pure file I/O, no daemon, safe while the daemons are up:

```bash
python scripts/kb_claim_audit.py   # every implemented_in: path/symbol still resolves
python scripts/kb_link_audit.py    # every related:/wikilink still resolves
```

---

## Step 4 — Gather session data

```bash
# Get today's commits
git log --oneline --since="midnight" --no-merges

# Get files changed today
git diff --stat HEAD~$(git log --oneline --since="midnight" --no-merges | wc -l) HEAD 2>/dev/null || git diff --stat HEAD~1 HEAD

# Get lines added/removed
git diff --shortstat HEAD~$(git log --oneline --since="midnight" --no-merges | wc -l) HEAD 2>/dev/null
```

If no commits today, check unstaged changes:

```bash
git diff --stat
git status --short
```

### Check existing log

Check if today's log already exists at `{vault}/10_Projects/emptyos/log/YYYY-MM-DD.md`.
If it exists, **append** a new session section rather than overwriting.

---

## Step 5 — Site sync commands

```bash
# Dry-run first to preview changes
python scripts/generate_emptyos_site.py --dry-run

# Apply if changes detected
python scripts/generate_emptyos_site.py
```

The script:

- Scans `apps/` + `apps/personal/` manifests → regenerates `apps.md` (full app catalog)
- Scans `plugins/` manifests → regenerates `plugins.md`
- Generates `capabilities.md` (capabilities + provider chains)
- Injects live counts into `index.md` (between `<!-- stats:start/end -->` markers)
- Updates app count in `architecture.md` ASCII art

**After running**, if the EmptyOS daemon is running, trigger a site rebuild.
**Name the site** — `publish` serves several (`default` = the personal site,
`portfolio`, `emptyos`), and a bare `build` rebuilds `default`, so the EmptyOS
site silently stays on its old inventory:

```bash
TOK=$(python -c "import tomllib;print(tomllib.load(open('emptyos.toml','rb')).get('network',{}).get('auth_token') or '')")
curl -s -X POST -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" \
  -d '{"site_id":"emptyos"}' http://127.0.0.1:9000/publish/api/build
```

The site is named by **`site_id` in the JSON body** — `api_build` reads it via
`_site_from_request`. A `?site=` query param is silently ignored and builds
`default` instead: measured 2026-08-17, `?site=emptyos` returned 4 pages,
`{"site_id":"emptyos"}` returned 80.

A correct rebuild reports ~80 pages. If it reports 4, it built `default`.
(`127.0.0.1`, not `localhost`: local mode binds IPv4 only.)

If the daemon is not running, note in the report: "Site source updated — rebuild when daemon starts."

**Do not auto-push.** The script updates local site source. Publishing to
`eos.binbian.net` is a deliberate user action via the Publish app UI or
`/publish/api/deploy`.

**Note on session content:** `generate_emptyos_site.py` only regenerates
inventory pages (apps/plugins/capabilities/stats). It does **not** turn the
session devlog into a public post. If the session is worth surfacing publicly,
suggest `/eos-devlog-publish` as a follow-up step — it reads the log written in
Step 4, writes a `type: post` note to the EmptyOS site source, checks
discrepancies vs already-published sessions, and triggers a rebuild (never
auto-deploys).

---

## Step 7 — Commit & push commands

```bash
git status --short          # review every change first
git add <path1> <path2> ...  # stage explicitly, by path, only what THIS session touched
git commit -m "<type>(<scope>): <concise summary>"
git push -u origin "$(git rev-parse --abbrev-ref HEAD)"   # -u sets upstream on first push
git log --oneline -1                                      # re-confirm HEAD is YOUR commit

python scripts/check_unpushed.py   # ...and did anything NOT leave the machine?
```

The Bash tool is Git Bash (POSIX sh), **NOT** PowerShell — use `git commit -m`,
never a PowerShell here-string (`@'…'@`). For a multi-line body use a POSIX
heredoc or repeated `-m`. End the message with the `Co-Authored-By` trailer.

### Push every repo you committed to, not just this one

`git push` covers the repo you are standing in. **`apps/personal/` is a separate
repo with its own remote**, and because the parent gitignores it, a wrapup that
only pushes the parent leaves that work on the machine with nothing complaining.
Four studio commits — including the one adding the whole System Icon Library —
sat there overnight on 2026-09-01 for exactly this reason.

`check_unpushed.py` is the backstop: it walks every repo this account owns and
reports anything unpushed past 12h. Run it at the end of Step 7, and push what
it names or say why you are holding it. A push there is normally a plain
fast-forward — the dirty working tree does not block it.

---

## Step 8 — Live-verify commands

```bash
curl -s http://127.0.0.1:9000/api/health || echo "OFFLINE"
```

Use `127.0.0.1`, not `localhost` (Python/loopback gotcha). HTML/CSS/JS-only
changes need **no** restart — the daemon serves `pages/` from disk per request,
so just probe the page.

Pure SDK/engine changes can verify offline with `python -m pytest tests/test_sdk_*.py`
(no daemon needed).
