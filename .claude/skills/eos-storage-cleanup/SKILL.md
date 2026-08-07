---
name: eos-storage-cleanup
description: Audit a Windows machine's disk usage and reclaim space safely — tiered scan (never a whole-drive recurse), classify every finding as safe / judgment / never-touch, then propose→preview→confirm before a single byte is deleted. Catches the reserves a directory scan structurally cannot see (pagefile, hiberfil, VSS shadow copies) and the under-reporting that makes a profile scan lie. Use when the user says "clean up storage", "disk is full", "free up space", "what's using my disk", "storage cleanup", or a drive is low on free space. NOT for pruning git worktrees (use eos-worktree-gc), vault structure drift (scripts/check_vault_structure.py), or test-fixture leaks (scripts/check_vault_test_leak.py).
---

# EmptyOS Storage Cleanup

Find where the disk went, and reclaim it **without deleting anything the user can't get back**. The skill is a measurement discipline first and a deletion tool a distant second: it **never deletes on its own**, it produces a ranked, classified proposal the user approves item by item.

This is the machine-level sibling of the repo-level cleanups (`eos-worktree-gc`, `check_vault_structure.py`). Those know one tree's semantics; this one knows a whole Windows disk's.

## The one hard rule

**Measure → classify → propose → confirm → verify.** No deletion happens before an explicit user confirmation of that specific item. A "clean up my disk" request is *not* blanket authorization to delete — it authorizes the audit. This is `.claude/rules/proposed-action.md` in impact-shape: the preview is the file list + reclaim estimate, the staleness check is a re-measure at apply time.

Deleting a 40 GB model cache the user is mid-project on costs more than the disk space is worth. When a call is close, leave it in the judgment tier and let the human decide.

## Prerequisites

- **Windows PowerShell 5.1** (the `PowerShell` tool). No daemon, no Playwright, no Python — this skill is pure filesystem measurement and runs with `:9000` down.
- **Keep `scan_storage.ps1` 7-bit ASCII.** PowerShell 5.1 reads a BOM-less `.ps1` as cp1252, so a UTF-8 em-dash or arrow decodes into stray quote bytes that terminate strings and cascade into dozens of bogus parse errors. Verify after editing:
  ```powershell
  $errs=$null; [System.Management.Automation.Language.Parser]::ParseFile('<path>',[ref]$null,[ref]$errs); $errs.Count
  Select-String -Path '<path>' -Pattern '[^\x00-\x7F]'
  ```
- **Elevation is optional but changes coverage.** `vssadmin list shadowstorage` needs an elevated shell; without it VSS shadow-copy usage is unmeasurable and the scanner says so explicitly. If the reconciliation gap stays large and unexplained, that gap is usually shadow copies — ask the user to re-run elevated rather than guessing.
- **Budget minutes, not seconds.** A multi-TB drive takes several minutes per tier. Run the scanner with `run_in_background: true` and do other work while it walks.

### Windows landmines that will bite you mid-run

| Symptom | Cause | Fix |
|---|---|---|
| `Get-ChildItem` throws *"wildcard character pattern is not valid"* and a whole subtree silently reports 0 GB | A directory name contains `[` or `]` (extremely common in media/download archives). `-Recurse` treats them as wildcards. | Use `-LiteralPath` **everywhere**, not just at the top call. A missed one under-reports without erroring. |
| Your own task-output and scratchpad files vanish mid-session | The agent harness writes under `%LOCALAPPDATA%\Temp\claude\...`, so clearing user Temp deletes them. | Expected, not a failure. Re-publish artifacts from a rewritten file. Warn the user their scratch outputs will reset before clearing Temp. |
| A native tool "fails" with `NativeCommandError` but clearly worked | PowerShell 5.1 surfaces any native stderr as an error record even on exit 0. | Read the actual message. `uv`/`ollama` print progress to stderr. Don't retry a command that already succeeded. |
| `pixi` refuses to run: *"found pyproject.toml without tool.pixi section"* | pixi inspects the **current directory**. | `Push-Location $env:USERPROFILE` first. |
| `Remove-Item` blocked by a path-safety hook on a harmless command | A format string like `"{0:N2}" -f ($b/1GB)` reads as a path `/1GB`. | Assign `$GB = 1073741824` and divide by the variable. |
| DISM / pagefile / `vssadmin` fail | Not elevated. | These four need admin. Hand the user the exact commands rather than half-doing it. |
| `Get-Item -Force 'C:\pagefile.sys'` returns nothing, so you conclude the file is gone | `Get-Item` opens a handle; locked system files refuse it. `Get-ChildItem -Force` only reads directory metadata and lists them fine. | Always enumerate reserve files with `Get-ChildItem`, never probe them with `Get-Item`. Concluding "the pagefile is gone" from this is a false negative. |
| A resized pagefile still shows its old size on disk | `Win32_PageFileSetting` is the **pending** config; the file only shrinks on reboot. `Win32_PageFileUsage` reports the **running** allocation. | Report the setting and the on-disk size separately, and say a reboot is required. Never claim reclaimed space before the reboot. |
| Loose files in a drive root are missed entirely | Top-level scans enumerate *directories*. | Sum `Get-ChildItem <root> -File -Force` too — this found 63 GB at `D:\` (a pagefile, a 19.5 GB zip, a stray 10.9 GB archive). |

## Phase 1 — Scan (tiered, never whole-drive)

Run the scanner:

```powershell
powershell -File .claude/skills/eos-storage-cleanup/scan_storage.ps1
powershell -File .claude/skills/eos-storage-cleanup/scan_storage.ps1 -Drives C,D -Top 20 -DrillTop 6
```

It writes a markdown report to the scratchpad and prints a summary. Three findings-classes come out of it, and the **third is the one hand-rolled scans always miss**.

### Three traps this phase exists to avoid

1. **Never `Get-ChildItem -Recurse` a whole drive.** It walks millions of files and times out (600 s+ on a 1.8 TB drive) having produced nothing. Always: size top-level dirs first, then drill only the top N. Depth is bought with measurements, not one giant call.

2. **A profile scan silently under-reports.** `C:\Users` scanned as 2.2 GB on a profile that was really ~490 GB — reparse points (OneDrive placeholders, junctions) and permission-denied subtrees vanish under `-ErrorAction SilentlyContinue`, and the number *looks* plausible. **The guard is the reconciliation check**: sum the measured dirs and compare to the drive's actual `Used`. Unaccounted space >10% means the scan lied — rescan excluding reparse points, and treat the gap as a finding in its own right. The scanner does this automatically and prints `UNACCOUNTED`.

   Treat the reconciliation gap as **a finding to chase, not noise to tolerate**. On the reference D: drive it was 256 GB — every readable directory summed to 1557 GB against 1814 GB used. Close it by elimination: check root loose files, per-SID recycle bins, File History / `WindowsImageBackup`, and re-measure suspect trees with `robocopy <dir> NULL /L /S /NJH /NC /NFL /NDL /BYTES` (long-path safe, unlike `Get-ChildItem`). Whatever remains unreadable *is* the answer — there it was `System Volume Information`, i.e. shadow copies.

   Cloud-sync folders need their own check: a fully-downloaded OneDrive tree and a fully-dehydrated one report **identical** logical sizes while occupying wildly different disk. Count the offline/recall attributes before proposing anything:

   ```powershell
   $_.Attributes -match 'Offline|RecallOnDataAccess|RecallOnOpen'   # true = cloud-only, ~0 bytes on disk
   ```

3. **The biggest single item is often invisible to any directory scan.** `pagefile.sys`, `hiberfil.sys`, `swapfile.sys`, and VSS shadow copies (`System Volume Information`) are not returned by a normal enumeration. On the reference machine the pagefile alone was **128 GB** — larger than every application combined. Always query these explicitly (the scanner does).

## Phase 2 — Classify

Every finding lands in exactly one tier. **The tiering is the deliverable**, not the byte count.

### Tier A — Safe (regenerable, no user data, tool-managed eviction)

Reclaim freely once confirmed. Prefer the tool's own purge command over `Remove-Item` — it keeps the tool's index consistent.

| Finding | Reclaim via |
|---|---|
| pip cache | `pip cache purge` |
| uv cache | `uv cache clean` |
| conda / rattler cache | `conda clean --all` |
| npm cache | `npm cache clean --force` |
| Recycle Bin | `Clear-RecycleBin` |
| `%LOCALAPPDATA%\Temp`, `C:\Windows\Temp` | delete contents (skip in-use) |
| WinSxS component store | `DISM /Online /Cleanup-Image /StartComponentCleanup` — **never** delete by hand |
| Playwright / puppeteer browser caches | re-downloaded on next run |
| Old Windows Update downloads | `SoftwareDistribution\Download` contents |

### Tier B — Judgment (regenerable but expensive, or user-owned)

Propose with a **cost-to-restore** estimate, and let the user choose. Never batch-approve these.

- **Model caches** — HuggingFace, ollama blobs, LM Studio, torch. Regenerable *only if* the model is still published and the user has bandwidth. Rank by last-access and cross-check against what's actually wired up (`ollama list` vs the configured default model) before proposing a removal.
- **Oversized pagefile** — reclaimable, but it is a *performance* setting, not junk. Propose a cap with reasoning, never silently resize.
- **Hibernation file** — `powercfg /h off` reclaims it but disables Fast Startup and hibernate. `powercfg /h /size 40` is the middle path.
- **Duplicate large files** — propose only after **hash verification** (name+size is a candidate, not proof). Prefer a hardlink over a delete when both paths are load-bearing.
- **Stale installers / downloads** — rank by age; a 3-year-old installer is near-certainly dead, last month's is not.
- **Backup retention** — apply a keep-N policy, never "delete backups".
- **Games, media, archives** — pure user call. Report size and last-played/last-modified; propose nothing.

### Classify by contents, never by name

**The single most dangerous moment in this skill is believing a folder name.** On the reference run, `.gemini\antigravity-ide` and `antigravity-backup` (19 GB each) were staged for deletion as "reinstallable IDE copies". Opening them showed `conversations/`, `brain/`, `knowledge/`, `context_state/` — agent memory and chat history, not a binary. A reinstall would **not** have restored it.

The same look also revealed the better move: 99.4% of all three trees was `browser_recordings/` (863,701 files, 19 GB each) while the irreplaceable data was ~0.08 GB. Deleting recordings from *all three* would have freed more (~57 GB) at zero risk to user data.

So before deleting any tree over a few GB:

```powershell
Get-ChildItem $p -Force | Select-Object -First 10          # what kind of thing is this?
# then size each subdir - the split between bulk and value is usually extreme
```

If a tree mixes bulk with irreplaceable data, **preserve the small half first** — it is almost free:

```powershell
# 320 conversation/brain files preserved for 0.16 GB before deleting 38 GB
Copy-Item "$src\conversations\*","$src\brain\*" $preserved -Recurse -Force
```

Then delete. A 0.16 GB insurance premium against losing chat history is always worth paying.

### Reported reclaim is not actual reclaim

Package caches (uv, pixi) **hardlink** into project venvs, so a tool reporting "Removed 17.9 GiB" may free far less — the bytes survive behind other links. On the reference run, tools reported ~43 GB of C: cache removal and the drive gained 28 GB.

Never quote the tool's number as the result. Always diff `Get-PSDrive` free space before and after, and report that. Prefer hardlinking over deleting for verified-identical large files: it reclaims the same space and cannot break a path.

### Tier C — Never touch

State these explicitly in the report so the user knows they were considered and deliberately excluded:

- `C:\Windows\Installer` — MSI/MSP cache. Deleting it breaks uninstall, repair, and future updates of every installed program. It *looks* like junk. It is not.
- `WinSxS` by hand — only DISM may evict from it; manual deletion corrupts servicing.
- `System Volume Information` by hand — use `vssadmin` to manage shadow copies.
- The vault (`notes.path`), any `.git` directory, `data/*.db*` (see `.claude/rules/daemon-handling.md`), source trees, `data/secrets/`.
- Anything under an active process's working set — check before touching a running tool's cache.
- **Cloud-sync folders (OneDrive, Dropbox, iCloud) — do not propose dehydration.** Deleting a placeholder in Explorer can delete the cloud copy, and even the sanctioned "Files On-Demand" route is a **user policy decision, not a cleanup action**: people keep local copies for offline access, backup independence, or because they don't trust eviction. Measure them, report them as context if a drive breakdown calls for it, and stop there. On this machine the answer is settled and permanent — see `feedback_onedrive_must_stay_local`; never re-raise it, however good the arithmetic looks.

## Phase 3 — Propose

Produce a **ranked table**: reclaim GB × tier × one-line action. Lead with the largest Tier-A item and the largest Tier-B item; do not bury a 100 GB finding under ten 2 GB ones.

Write the full report to a file and reference it by path (CLAUDE.md § Working Style — long output goes to a file). An HTML/Artifact report is appropriate here: the deliverable is a table the user scans and picks from.

For each proposed item give: **what it is, why it's safe (or what it costs to restore), the exact command, and the reclaim estimate.**

## Phase 4 — Confirm and apply

- Apply **only** the items the user approved, one at a time, largest first.
- **Re-measure before deleting** — if the size moved materially since the proposal, stop and re-propose (staleness check).
- After each item, print actual-vs-estimated reclaim. Estimates are frequently wrong; report the real number.
- Never chain a Tier-B deletion off a Tier-A approval.

## Phase 5 — Verify

Re-run the drive summary and report actual free space before/after. If a reclaim under-delivered by >20%, say so and explain why (in-use files skipped, compression, sparse allocation). Report faithfully — a cleanup that freed 40 GB when it promised 60 is a 40 GB success and a wrong estimate, and both halves get said.

## When NOT to use this skill

- **A single known offender** ("delete the old ComfyUI zip") — just delete it; the audit ceremony costs more than it saves.
- **Repo/worktree clutter** → `eos-worktree-gc`.
- **Vault structure drift or test-fixture leaks** → `scripts/check_vault_structure.py`, `scripts/check_vault_test_leak.py`.
- **The drive is fine.** Free space above ~15% with no complaint is not a problem to solve.

## Cross-references

- `.claude/rules/proposed-action.md` — impact-shaped propose/preview/confirm; the paradigm this skill instantiates.
- `.claude/rules/audits.md` — false-positive discipline; test any new "this is junk" heuristic against 3 directories you know are precious before trusting it.
- `.claude/rules/daemon-handling.md` — never delete `data/*.db*`, never kill python to free a file handle.
- `eos-worktree-gc` — the repo-side sibling.
