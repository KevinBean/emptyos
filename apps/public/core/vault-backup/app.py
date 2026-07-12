"""Vault Backup — scheduled OS-agnostic snapshots of the mounted vault.

Replaces the external Windows ``backup-vault.bat`` with a platform-native
mechanism: a scheduler job snapshots ``notes.path`` into dated folders under a
configurable destination, prunes snapshots past the retention window, records
run state, and surfaces a staleness signal on the hub.

The raw filesystem snapshot is the SDK primitive ``emptyos.sdk.fs_snapshot``
(pure ``shutil``, OS-agnostic, Docker-bootable — CLAUDE.md rule 20). This app
is the orchestration layer: schedule, run-state, status, events, and UI. The
heavy copy runs in ``asyncio.to_thread`` so it never blocks the event loop
(see the middleware/handler blocking gotcha in memory).
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, web_route
from emptyos.sdk import fs_snapshot

log = logging.getLogger("emptyos.vault-backup")

_JOB_ID = "vault-backup-snapshot"


class VaultBackupApp(BaseApp):
    async def setup(self):
        await super().setup()
        self._register_schedule()
        log.info("vault-backup started")

    async def teardown(self):
        self.remove_cron_job(_JOB_ID)
        await super().teardown()

    # ── Schedule wiring ────────────────────────────────────────
    def _register_schedule(self):
        if not self._cfg("enabled", True):
            self.remove_cron_job(_JOB_ID)
            return
        cron = str(self._cfg("schedule_cron", "0 2 * * *")).strip() or "0 2 * * *"
        ok = self.add_cron_job_logged(
            _JOB_ID, self._scheduled_run, cron=cron, crash_event="backup_crash",
        )
        if ok:
            self.log_activity({"event": "schedule_registered", "cron": cron})
        else:
            self.log_activity({"event": "schedule_failed", "cron": cron})

    async def _scheduled_run(self):
        await self.run_backup(trigger="schedule")

    # ── Config resolution ──────────────────────────────────────
    def _cfg(self, key: str, default):
        """Read a `[provides.settings]` value: UI settings service first (where
        the settings panel writes), then ``emptyos.toml [apps.vault-backup]``,
        then the default. The settings panel keys are ``vault-backup.<key>``."""
        v = self.setting(f"vault-backup.{key}", None)
        if v is None:
            v = self.app_config(key, None)
        return default if v is None else v

    def _dest_root(self) -> Path:
        """Resolve the backup destination. Configurable; defaults to a sibling
        of the vault named ``<vault-name>-backups`` so we never hardcode a
        machine path (CLAUDE.md rule 13)."""
        configured = str(self._cfg("dest", "") or "").strip()
        if configured:
            return Path(configured)
        vault = self.vault_root
        return vault.parent / f"{vault.name}-backups"

    def _retention_days(self) -> int:
        try:
            return max(1, int(self._cfg("retention_days", 7)))
        except (TypeError, ValueError):
            return 7

    def _stale_hours(self) -> int:
        try:
            return max(1, int(self._cfg("stale_hours", 36)))
        except (TypeError, ValueError):
            return 36

    def _mode(self) -> str:
        m = str(self._cfg("mode", "incremental") or "incremental").strip()
        return m if m in ("incremental", "zip", "folder") else "incremental"

    # ── Exclude resolution ─────────────────────────────────────
    def _include_media(self) -> bool:
        """When true, media (video/audio) is kept IN local snapshots. Default
        false: media is large and usually covered by the off-site push, so the
        local snapshot stays small. Flip on for a self-contained local copy."""
        return bool(self._cfg("include_media", False))

    @staticmethod
    def _parse_tokens(raw) -> set[str]:
        """Split a free-text 'a, b  c' list into a clean token set."""
        if not raw:
            return set()
        return {t.strip() for t in str(raw).replace(",", " ").split() if t.strip()}

    def _exclude_dirs(self) -> frozenset[str]:
        """SDK defaults (dot-dirs, node_modules, site-packages, …) plus any
        user-configured extra folder names."""
        return fs_snapshot.DEFAULT_EXCLUDE_DIRS | self._parse_tokens(
            self._cfg("extra_exclude_dirs", ""))

    def _exclude_suffixes(self) -> frozenset[str]:
        """SDK defaults, minus media when ``include_media`` is on, plus any
        user-configured extra suffixes (normalised to lowercase ``.ext``)."""
        base = set(fs_snapshot.DEFAULT_EXCLUDE_SUFFIXES)
        if self._include_media():
            base -= fs_snapshot.MEDIA_SUFFIXES
        extra = {(s if s.startswith(".") else "." + s).lower()
                 for s in self._parse_tokens(self._cfg("extra_exclude_suffixes", ""))}
        return frozenset(base | extra)

    def _offsite_cmd(self) -> str:
        """User-configured shell command template run after a successful backup
        to push the snapshot off-machine (rclone / aws / rsync / scp). Empty =
        disabled. Placeholders: {snapshot} = snapshot path, {name} = its name.
        Configured in the settings panel (or emptyos.toml) — the user supplies
        their own tool, the app never bakes in a cloud SDK."""
        return str(self._cfg("offsite_cmd", "") or "").strip()

    def _backup_data_enabled(self) -> bool:
        return bool(self._cfg("backup_data", True))

    def _data_include(self) -> set[str]:
        return self._parse_tokens(self._cfg("data_include", "settings.json secrets store"))

    def _snapshot_data(self, dest_root: Path) -> dict:
        """Zip a curated local-data allowlist under ``dest_root/data``.

        ``data/apps`` and the live event/syslog SQLite files are always refused:
        hot-copying WAL databases is not a consistent backup. The resulting
        archive remains local; ``_run_offsite`` receives only the vault
        snapshot path.
        """
        data_root = Path(self.kernel.config.data_dir)
        data_dest = dest_root / "data"
        data_dest.mkdir(parents=True, exist_ok=True)
        target = data_dest / f"{datetime.now().strftime('%Y-%m-%d')}.zip"
        temp = target.with_suffix(".zip.tmp")
        blocked_roots = {"apps"}
        blocked_files = {
            "events.db", "events.db-wal", "events.db-shm",
            "syslog.db", "syslog.db-wal", "syslog.db-shm",
        }
        files = 0
        size = 0
        included: list[str] = []
        try:
            with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for token in sorted(self._data_include()):
                    rel = Path(token)
                    if rel.is_absolute() or ".." in rel.parts or not rel.parts:
                        continue
                    if rel.parts[0].lower() in blocked_roots:
                        continue
                    src = data_root / rel
                    candidates = [src] if src.is_file() else (
                        sorted(p for p in src.rglob("*") if p.is_file()) if src.is_dir() else []
                    )
                    token_added = False
                    for path in candidates:
                        arc = path.relative_to(data_root)
                        if arc.parts[0].lower() in blocked_roots or arc.name.lower() in blocked_files:
                            continue
                        try:
                            zf.write(path, arc.as_posix())
                            size += path.stat().st_size
                        except OSError:
                            continue
                        files += 1
                        token_added = True
                    if token_added:
                        included.append(rel.as_posix())
            temp.replace(target)
        finally:
            if temp.exists():
                temp.unlink(missing_ok=True)
        return {
            "enabled": True,
            "snapshot": str(target),
            "files": files,
            "bytes": size,
            "included": included,
        }

    # ── Off-site push (optional, config-driven) ────────────────
    async def _run_offsite(self, snapshot_path: str) -> dict | None:
        tmpl = self._offsite_cmd()
        if not tmpl:
            return None
        name = Path(snapshot_path).name
        try:
            cmd = [a.replace("{snapshot}", snapshot_path).replace("{name}", name)
                   for a in shlex.split(tmpl)]
        except ValueError as e:
            return {"ok": False, "error": f"bad offsite_cmd: {e}"}
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            _, err = await asyncio.wait_for(proc.communicate(), timeout=1800)
            ok = proc.returncode == 0
            return {"ok": ok, "returncode": proc.returncode,
                    "error": ("" if ok else (err or b"").decode("utf-8", "replace")[:300])}
        except asyncio.TimeoutError:
            return {"ok": False, "error": "offsite command timed out (30 min)"}
        except (OSError, FileNotFoundError) as e:
            return {"ok": False, "error": str(e)[:300]}

    # ── Public: run a backup ───────────────────────────────────
    async def run_backup(self, trigger: str = "manual") -> dict:
        dest_root = self._dest_root()
        started = datetime.now(timezone.utc).isoformat()
        try:
            result = await asyncio.to_thread(
                fs_snapshot.snapshot_tree, self.vault_root, dest_root,
                mode=self._mode(),
                exclude_dirs=self._exclude_dirs(),
                exclude_suffixes=self._exclude_suffixes())
            pruned = await asyncio.to_thread(
                fs_snapshot.prune_snapshots, dest_root, self._retention_days())
            if self._backup_data_enabled():
                data_backup = await asyncio.to_thread(self._snapshot_data, dest_root)
                data_backup["pruned"] = await asyncio.to_thread(
                    fs_snapshot.prune_snapshots,
                    dest_root / "data",
                    self._retention_days(),
                )
            else:
                data_backup = {"enabled": False}
            offsite = await self._run_offsite(result["snapshot"])
            record = {
                "ok": True,
                "trigger": trigger,
                "started": started,
                "finished": datetime.now(timezone.utc).isoformat(),
                "dest_root": str(dest_root),
                "pruned": pruned,
                "data_backup": data_backup,
                "offsite": offsite,
                **result,
            }
            self._record(record)
            await self.emit("vault-backup:completed", {
                "files": record["files"], "snapshot": record["snapshot"],
            })
            self.log_activity({"event": "backup_completed", "files": record["files"],
                               "duration_s": record["duration_s"], "trigger": trigger})
            return record
        except Exception as e:  # noqa: BLE001 — fail-soft, record + surface
            record = {
                "ok": False,
                "trigger": trigger,
                "started": started,
                "finished": datetime.now(timezone.utc).isoformat(),
                "dest_root": str(dest_root),
                "error": str(e)[:400],
            }
            self._record(record)
            await self.emit("vault-backup:failed", {"error": record["error"]})
            self.log_activity({"event": "backup_failed", "error": record["error"]})
            return record

    def _record(self, record: dict):
        state = self.load_state(default={}) or {}
        state["last_run"] = record
        history = state.get("history", [])
        history.insert(0, {k: record.get(k) for k in
                           ("ok", "trigger", "finished", "files", "bytes", "mode",
                            "copied", "linked", "added_bytes", "had_prior",
                            "hardlink_supported", "offsite",
                            "data_backup",
                            "compressed_bytes", "compressed", "duration_s", "error")})
        state["history"] = history[:30]
        self.save_state(state)

    # ── Status computation ─────────────────────────────────────
    def _status(self) -> dict:
        state = self.load_state(default={}) or {}
        last = state.get("last_run") or {}
        stale_hours = self._stale_hours()
        age_h = None
        stale = True
        if last.get("finished"):
            try:
                fin = datetime.fromisoformat(last["finished"])
                if fin.tzinfo is None:
                    fin = fin.replace(tzinfo=timezone.utc)
                age_h = (datetime.now(timezone.utc) - fin).total_seconds() / 3600
                stale = age_h > stale_hours
            except ValueError:
                pass
        # Warn when the dest silently can't hardlink (incremental degraded to
        # full copies) — only meaningful once a prior snapshot existed to link.
        hardlink_warning = (
            last.get("mode") == "incremental"
            and last.get("had_prior")
            and last.get("hardlink_supported") is False
        )
        return {
            "last_run": last,
            "age_hours": round(age_h, 1) if age_h is not None else None,
            "stale": stale,
            "stale_hours": stale_hours,
            "enabled": bool(self._cfg("enabled", True)),
            "dest_root": str(self._dest_root()),
            "retention_days": self._retention_days(),
            "mode": self._mode(),
            "include_media": self._include_media(),
            "backup_data": self._backup_data_enabled(),
            "data_include": sorted(self._data_include()),
            "data_backup": last.get("data_backup"),
            "offsite_enabled": bool(self._offsite_cmd()),
            "hardlink_warning": hardlink_warning,
            "next_run": self.get_cron_job_next_fire(_JOB_ID),
            "never_run": not bool(last),
        }

    def _restore_target(self, name: str) -> Path:
        """Default restore location — a sibling of the dest, never the live
        vault. The user inspects the restored tree and copies back by hand."""
        safe = name.replace(".zip", "").replace("/", "-").replace("\\", "-")
        vault = self.vault_root
        return vault.parent / f"{vault.name}-restored-{safe}"

    def _list_snapshots(self) -> list[dict]:
        return fs_snapshot.list_snapshots(self._dest_root())

    # ── CLI ────────────────────────────────────────────────────
    @cli_command("status")
    async def cli_status(self):
        s = self._status()
        if s["never_run"]:
            print("Vault backup: never run")
        else:
            flag = "STALE" if s["stale"] else "ok"
            print(f"Vault backup: {s['age_hours']}h ago ({flag}), "
                  f"{s['last_run'].get('files', '?')} files -> {s['dest_root']}")

    @cli_command("now")
    async def cli_now(self):
        r = await self.run_backup(trigger="cli")
        print("OK" if r["ok"] else f"FAILED: {r.get('error')}")

    # ── Web API ────────────────────────────────────────────────
    @web_route("GET", "/api/status")
    async def api_status(self, request):
        return self._status()

    @web_route("GET", "/api/snapshots")
    async def api_snapshots(self, request):
        return {"snapshots": self._list_snapshots(), "dest_root": str(self._dest_root())}

    @web_route("POST", "/api/backup-now")
    async def api_backup_now(self, request):
        return await self.run_backup(trigger="manual")

    @web_route("POST", "/api/restore")
    async def api_restore(self, request):
        """Restore a snapshot into a NEW sibling folder (never the live vault).
        Body: {snapshot: "<name>", target?: "<abs path>"}. The user inspects the
        result and copies back by hand — a deliberate safety gate (the live
        vault is never overwritten by a restore)."""
        body = await request.json()
        name = (body.get("snapshot") or "").strip()
        if not name:
            return {"ok": False, "error": "snapshot name required"}
        # Resolve against the known snapshot list — never trust a raw path.
        snaps = {s["name"]: s["path"] for s in self._list_snapshots()}
        if name not in snaps:
            return {"ok": False, "error": f"unknown snapshot: {name}"}
        target = Path(body.get("target").strip()) if body.get("target") else self._restore_target(name)
        try:
            res = await asyncio.to_thread(
                fs_snapshot.restore_snapshot, Path(snaps[name]), target)
            self.log_activity({"event": "restore", "snapshot": name,
                               "target": res["target"], "files": res["files"]})
            return {"ok": True, **res}
        except Exception as e:  # noqa: BLE001 — fail-soft
            return {"ok": False, "error": str(e)[:300]}

    @web_route("POST", "/api/reschedule")
    async def api_reschedule(self, request):
        """Re-read settings and re-register the cron job (call after a
        settings change so the new schedule/enabled state takes effect
        without a daemon restart)."""
        self._register_schedule()
        return {"ok": True, "next_run": self.get_cron_job_next_fire(_JOB_ID),
                "enabled": bool(self._cfg("enabled", True))}

    # ── Hub panel ──────────────────────────────────────────────
    async def panel_backup_status(self) -> dict | None:
        s = self._status()
        if s["never_run"]:
            return {"label": "Vault Backup", "value": "never", "tone": "warn"}
        if s["stale"]:
            return {"label": "Vault Backup", "value": f"{s['age_hours']}h ⚠",
                    "tone": "warn"}
        age = s["age_hours"] or 0
        val = f"{int(age)}h ago" if age >= 1 else "just now"
        return {"label": "Vault Backup", "value": val}
