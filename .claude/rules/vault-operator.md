---
paths:
  - "scripts/check_vault_*.py"
  - "scripts/kb_*.py"
  - "tests/conftest.py"
  - "emptyos/runtime/vault_*.py"
  - ".claude/vault-connection.json"
  - "tests/test_unit_vault_structure.py"
---

# Vault Operator — External Vault Connection

EmptyOS mounts an external markdown vault (Obsidian/Logseq) as its "hard drive".
The vault path is configured in `emptyos.toml` under `[notes] path = "..."`.

## Vault Connection State

A file at `.claude/vault-connection.json` tracks the current connection:

```json
{
  "connected": true,
  "vault_path": "/path/to/your/vault",
  "connected_at": "2026-04-07T10:00:00",
  "vault_claude_md": "/path/to/your/vault/CLAUDE.md"
}
```

When **connected**, you can:
- Read/write vault files using absolute paths from the connection file
- Call EmptyOS APIs at `localhost:9000` if the daemon is running
- Use vault_config paths from `_vault-map.toml` for app-specific data locations

When **disconnected**, only operate on the EmptyOS codebase itself.

## How to Read Vault Path

```python
# From emptyos.toml
import tomllib
with open("emptyos.toml", "rb") as f:
    config = tomllib.load(f)
vault_path = config.get("notes", {}).get("path", "")
```

Or read `.claude/vault-connection.json` for the cached connection state.

## Vault Structure (PARA method)

```
{vault}/
├── 00_Inbox/          ← captures, unsorted
├── 10_Projects/       ← active projects with deadlines
├── 20_Areas/          ← ongoing responsibilities (Career, Health, Finances)
├── 30_Resources/      ← reference material (People, Books, Learning)
├── 40_Archive/        ← completed/inactive projects
├── 50_Journal/        ← daily notes: {year}/{YYYY-MM-DD}.md
└── 30_Resources/EmptyOS/
    ├── _vault-map.toml  ← app data path mappings
    └── {app}/           ← per-app vault storage
```

## Test-fixture leak guard

EmptyOS's E2E suite runs against the live daemon on `:9000`, which is mounted on
the **real** vault (`notes.path`). Every test that creates vault-backed data
writes a `TEST_PREFIX` (`PLAYWRIGHT-TEST-`) fixture into that real vault and
relies on the per-app cleanup sweep in `tests/conftest.py::cleanup_after_all` to
remove it. That sweep is best-effort and per-app — apps it doesn't cover (cad
`outputs/`, journal/capture entries appended to real dailies, any new app) leak
silently and **accumulate**. A 2026-05-30 vault sort purged **6,954** such
artifacts; 4,307 were under `30_Resources/EmptyOS/` app-data dirs.

The guard so it can't recur is two pieces:

- **`scripts/check_vault_test_leak.py`** — scans a vault for `TEST_PREFIX` hits
  and classifies each by shape (pure file I/O, no kernel import, safe while the
  daemon is up):

  | Shape | Detection | Action |
  |---|---|---|
  | `owned` | prefix in filename or a frontmatter identity field (`id`/`name`/`title`/`slug`/`cad_id`/…); under `outputs/<id>/` the whole artifact dir | delete (auto) |
  | `strip` | a single self-contained list line carrying the prefix (`- [ ] …`, `- **05:46** 🙂 …`, `1. …`) appended into a real note | strip the line (auto) |
  | `review` | prefix on a **non-list** line — heading, prose, or a `>` quote / indented-insight pair under a clean bullet header (KB "## Reader notes" pollution) | report only, **never auto-edited** |

  Two exclusions, and the second is a safety boundary rather than a filter.
  Incident documentation under `10_Projects/emptyos/log/` (devlogs + session
  briefs that *describe* the leak) is excluded entirely. And **every dot-dir is
  skipped** — `.stversions` above all, Syncthing's version archive. A finding in
  a restore point is unactionable by construction (editing it corrupts what you
  would restore from), so it could only pin the exit code permanently non-zero
  and get the gate switched off; and `--purge` runs off the same walk, so an
  archived copy of a real note a test polluted would have its `- [ ]` line
  **rewritten** — the backup edited by the sweep, unattended via the conftest
  backstop below. Skipping by class (any leading dot) rather than by name
  matches `check_vault_structure.py` and covers the next hidden tooling dir
  without another commit. Consequence worth knowing: **nothing in EmptyOS
  reports on `.stversions` at all** — Syncthing's own versioning-cleanup config
  is what bounds it, so don't read a clean exit as "the archive is clean".

  The skip is matched against the **vault-relative** path. Matching the absolute
  one meant a vault living under any path with a dot or `node_modules` component
  scanned zero files and printed "no leaks" with exit 0 — the whole guard off,
  reporting success.

  Run modes: `--purge` (delete owned + strip lines), `--owned-only` (delete
  files, leave real notes), `--json`. Exit code = count of `review` items, so a
  release/CI gate can treat "needs a human" as a hard failure.

- **conftest session-end backstop** — the tail of `cleanup_after_all` runs the
  scanner against the daemon's vault after the per-app sweep and auto-purges
  `owned` + `strip` leaks (~1.3s over a 12k-note vault), so the vault never
  accumulates regardless of which app forgot a cleanup block. `review` items
  print a loud warning naming the notes. `EOS_SKIP_LEAK_GUARD=1` skips it for
  fast single-file iteration; `EOS_LEAK_GATE_STRICT=1` fails the session on any
  `review` leftover (use in CI). Net effect: a missing per-app cleanup surfaces
  as one swept artifact, not 4,307.

Maintenance: when you add a new vault-writing app, still add its per-app cleanup
block to `cleanup_after_all` (the backstop is a safety net, not a substitute —
API-level deletes emit the right events; the filesystem sweep doesn't). Run
`python scripts/check_vault_test_leak.py` from `/preflight` or before a release
to catch `review`-shape pollution the auto-sweep deliberately won't touch.

If `TEST_PREFIX` leaks keep recurring *despite* the backstop, escalate to a
write-boundary refusal in `BaseApp`'s vault write helpers (reject a write whose
identity carries `TEST_PREFIX` unless the daemon is on a sandbox/demo vault) —
deferred today because the leak is a test-harness problem and belongs in test
infra, not the live SDK write path.

## Vault structure guard

The sibling of the test-leak guard for **general structural drift** (built
2026-07-03 after 750 empty dirs + 200 sync-conflict dups + 5 undeclared
top-level folders accumulated with nothing watching):

- **Contract** — `{vault}/30_Resources/EmptyOS/_vault-structure.toml` declares
  what "clean" means (allowed top-level folders AND root files, junk patterns,
  empty-dir keep-globs, advisory thresholds). Auto-generated from the PARA
  canon on first run — never from the current tree, which would bless drift.
  User-editable; declaring a new top-level folder is a contract edit, not a
  scanner change.
- **Scanner** — `scripts/check_vault_structure.py` (pure file I/O, no kernel;
  same classification discipline as the leak guard): **safe** = content-lossless
  (empty dirs to fixpoint, byte-identical "name 2.md"/Syncthing conflict copies,
  `.tmp`, aged zero-byte incl. Windows `nul` reserved-name artifacts via `\\?\`
  deletion) auto-purgeable under `--purge` (fs_snapshot into the vault-backup
  pool first, abort on failure); **review** = divergent dup copies, non-empty
  `Untitled*.md`, undeclared top-level entries — never auto-touched; **advisory**
  = inbox backlog, >150KB notes, app dirs >1500 files. Exit code = review count.
- **Where it runs** — `/preflight --scope vault` (advisory row next to the leak
  guard) + a weekly report-only cron in app-analytics
  (`[apps.app-analytics] vault_structure_sweep`, surfaces via `proactive_notify`).
  Purge is always a manual, human-run act.
- **Retention TTL** — the contract's `[retention]` table ("dir" = days) marks
  designated backup/scratch dirs (e.g. `99_Attachments/temp-backup` = 90):
  files older than the TTL are safe-purgeable (`retention_expired`); inside a
  retention dir the TTL is the ONLY rule (backups are never dup/untitled-flagged).
- **Embedded envs** — venv/node_modules/site-packages dirs found in the vault
  surface as an `embedded_env` advisory (never purged) so a 500MB regenerable
  env can't creep in invisibly.
- **Prevention side** — projects app creates `docs/assets/log` subfolders
  **lazily on first write** (never eagerly), so scaffolding and empty-dir
  pruning don't fight.

## KB integrity scans

Two static scans (pure file I/O, no daemon) keep the engineering KB's claims
consistent with the codebase ground truth. Run from `/preflight` when a session
touches the KB, or before relying on the KB:

- `scripts/kb_claim_audit.py` — every `implemented_in:` path (incl. `::symbol`)
  resolves to a real code path; every `verified_against:` anchor resolves to a
  real KB note.
- `scripts/kb_link_audit.py` — every `related:` slug + body `[[wikilink]]`
  resolves. Both read the vault root from `emptyos.toml [notes].path`.

Both accept `--root <path>` (absolute, or relative to the vault) to scan a
different corpus; shared root resolution lives in `scripts/kb_paths.py`. The
personal engineering KB at `30_Resources/KB/` is registered in preflight's
`kb` scope as the `kb_claim_audit (personal)` / `kb_link_audit (personal)`
rows, so both corpora run from one `/preflight --scope kb`. Parsers normalize
corpus dialects: `"[[slug]]"`-wrapped `related:`/`verified_against:` entries,
`path:`/`method:`-prefixed `implemented_in` items (`method:` ids are skipped —
they need the daemon), and dotted `::Class.method` symbols.

Triage per `.claude/rules/audits.md`: `[[slug]]`-in-`related:` (the kb app's
`_related_targets` strips `[]`) and "(to be created)" links are *intentional*,
not findings. Notes under `kb/outputs/` are AI-authored reports, not KB notes —
both scripts skip that subdir.

## Rules When Operating on Vault

1. **Never delete vault files** without explicit user confirmation
2. **Respect frontmatter** — don't strip or reformat YAML frontmatter in notes
3. **Use forward slashes** in paths (Windows compat: `D:/Vault/note.md`)
4. **Prefer APIs** over direct file access when EmptyOS daemon is running — APIs apply vault_config, validation, and emit events
5. **Read vault CLAUDE.md** if it exists at `{vault}/CLAUDE.md` — it may contain personal preferences, vault conventions, or project context
6. **Vault map** at `{vault}/30_Resources/EmptyOS/_vault-map.toml` tells you where each app's data lives
