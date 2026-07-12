# Intent — Vault Backup

> Living design doc for `apps/public/core/vault-backup/`. Edit as the app evolves.
> Born: 2026-05-30.
> Changelog: `10_Projects/emptyos/log/app-development.md`.

## Why
Backup belongs *inside* EmptyOS: the daemon already mounts the vault
(`notes.path`), has a scheduler, and can surface staleness. A platform-native
mechanism is OS-agnostic (Docker-bootable, rule 20), self-monitoring (hub panel
warns when stale), and ships for every user instead of relying on a per-machine
Task Scheduler entry. Pairs with git-tracking as a second recovery path —
untracked files (e.g. leaked test artifacts) are snapshot-only.

## Relationship to the external `D:\Vault-Backups` robocopy strategy
Decided 2026-05-30: this app does **not** replace the existing
`backup-vault.bat` robocopy mirror — they run **independently** to different
destinations (the `.bat` → `D:\Vault-Backups`; this app → its own
`<vault-parent>/<vault-name>-backups` by default). Two independent backup
systems is a feature (defence in depth), not duplication. The app's mechanism
must be **same-or-better** than robocopy: it is — `incremental` mode does
hardlink + link-dest snapshots (baseline + deltas, each snapshot a complete
browsable tree), which is more disk-efficient than robocopy's full-copy-per-
dated-folder, while adding scheduling + staleness monitoring the `.bat` lacked.

## Snapshot modes (`vault-backup.mode`)
- `incremental` (default) — `<date>/` folder; files unchanged since the most
  recent prior snapshot are **hardlinked** (0 extra bytes), only changed/new
  files copied. Best for large/binary-heavy vaults (the real vault is ~3.8 GB,
  mostly incompressible attachments → zip gives ~1.0x, so dedup beats
  compression). Falls back to per-file copy when the dest can't hardlink.
- `zip` — single `<date>.zip`. Good for text-heavy vaults. Clamps pre-1980
  mtimes (ZIP/DOS-epoch limit).
- `folder` — plain full copy.

## Relationships
**Calls into** (`self.call_app(...)`):
- (none) — self-contained.

**SDK primitive:**
- `emptyos.sdk.fs_snapshot` — `snapshot_tree` / `prune_snapshots` / `list_snapshots`. The raw `shutil` filesystem work lives in the SDK (not this app) so the app layer stays orchestration-only and the primitive is unit-testable without the daemon (`tests/test_sdk_fs_snapshot.py`). Second consumer would import the same module rather than re-rolling tree-copy.

**Emits:**
- `vault-backup:completed` — after a successful snapshot; payload `{files, snapshot}`. Reactor/other apps may breadcrumb it.
- `vault-backup:failed` — on snapshot error; payload `{error}`.

**Listens for** (`@on_event`):
- (none)

**Scheduler:**
- Registers cron job `vault-backup-snapshot` in `setup()` via `add_cron_job_logged`; default `0 2 * * *` (local time). Re-registered by `POST /api/reschedule` after a settings change.

## Open questions
- Off-machine target (rsync/S3/rclone) for true off-site backup — v1 is
  same-disk-other-folder. Hardlinks don't survive an S3 push, so off-site would
  need its own dedup strategy (or just zip per snapshot for upload).
- Restore flow — snapshots are plain folders the user copies back by hand. A
  guided "restore snapshot N" verb is a candidate.
- Hardlink fallback is silent per-file (degrades to copy on FAT/network dest);
  surface a one-time "this dest can't hardlink — snapshots will be full copies"
  warning in status when `linked == 0` on a run that had a prior snapshot.

## Future
- Off-site target adapter (rclone/S3) behind config.
- `/system` integration so backup staleness shows in the capability inspector.
- Verify-after-copy (count/size parity check) recorded in the run record.
