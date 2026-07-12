"""forge — dev/build/release lifecycle for a Forge project.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: start_dev / stop_dev / tail_dev process management, the build + release verbs that delegate to the target, and matching API endpoints.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._data_dir / self.get_project (projects) for state lookups.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path
from emptyos.sdk import web_route
from typing import TYPE_CHECKING

from .targets import TARGETS


async def _kill_process_tree(proc) -> None:
    """Best-effort kill of a process and its children.

    Windows: CTRL_BREAK_EVENT (works because we spawned with
    CREATE_NEW_PROCESS_GROUP) → 5s grace → terminate → taskkill /F /T as
    last resort (kills the whole tree by PID).

    POSIX: SIGTERM → 5s grace → SIGKILL. Process groups are inherited from
    the parent, so the children typically die with the group leader; if not,
    SIGKILL on the leader is the best we can do without a setpgid setup we
    didn't do at spawn.
    """
    pid = getattr(proc, "pid", None)
    try:
        if os.name == "nt":
            try:
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            except Exception:
                proc.terminate()
        else:
            try:
                proc.send_signal(signal.SIGTERM)
            except Exception:
                proc.terminate()
    except Exception:
        pass
    # 5s grace period.
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
        return
    except asyncio.TimeoutError:
        pass
    try:
        proc.kill()
    except Exception:
        pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=2.0)
        return
    except asyncio.TimeoutError:
        pass
    # Last resort on Windows: taskkill kills the tree by PID. POSIX has no
    # equivalent that we haven't already tried.
    if os.name == "nt" and pid:
        try:
            await asyncio.to_thread(
                subprocess.run,
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            pass


if TYPE_CHECKING:
    from .app import ForgeApp  # noqa: F401 — for type hints only


# ─── Bind to ForgeApp class as ────────────────────────────────
#   start_dev     = _dev.start_dev
#   stop_dev      = _dev.stop_dev
#   tail_dev      = _dev.tail_dev
#   build         = _dev.build
#   release       = _dev.release
#   api_dev       = _dev.api_dev
#   api_dev_stop  = _dev.api_dev_stop
#   api_dev_tail  = _dev.api_dev_tail
#   api_build     = _dev.api_build
#   api_release   = _dev.api_release
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


async def start_dev(self, project_id: str) -> dict:
    """Start `npm run tauri dev` for a project. Non-blocking — returns as
    soon as the process spawns. The dev process keeps running until
    stop_dev is called or the daemon shuts down.
    """
    if project_id in self._running:
        return {"error": "already running", "running": True}
    rel = self._vault_rel(project_id)
    fm = self.vault_get_properties(rel)
    if not fm:
        return {"error": "Project not found"}
    target_id = fm.get("target") or ""
    tgt = TARGETS.get(target_id)
    if not tgt:
        return {"error": f"unknown target '{target_id}'"}
    repo_path = Path(fm.get("repo_path") or "").expanduser()
    if not repo_path.exists():
        return {"error": f"repo missing on disk: {repo_path}"}

    run_id = f"{project_id}-{int(time.time())}"
    log_path = self._data_dir("runs", run_id) / "dev.log"

    def _on_log(_line: bytes) -> None:
        # Drain task already writes to the file; UI tails it directly.
        pass

    try:
        rec = await tgt.dev(repo_path=repo_path, log_path=log_path, on_log=_on_log)
    except Exception as e:
        return {"error": f"dev failed to start: {e}"}

    self._running[project_id] = rec
    # Record the previous status so stop_dev can restore it.
    self._dev_meta[project_id] = {"prev_status": fm.get("status") or "scaffolded"}
    self.vault_update(rel, {"status": "dev", "updated": datetime.now().strftime("%Y-%m-%d")})
    await self.emit("forge:dev_started", {"id": project_id, "pid": rec.pid})
    return {"ok": True, "pid": rec.pid, "log_path": str(rec.log_path)}


async def stop_dev(self, project_id: str) -> dict:
    """Send a graceful kill to the dev process tree. Falls back to taskkill
    on Windows after a 5s grace period."""
    rec = self._running.pop(project_id, None)
    if not rec:
        return {"ok": True, "was_running": False}
    proc = rec.proc
    try:
        if proc and getattr(proc, "returncode", None) is None:
            await _kill_process_tree(proc)
    except Exception:
        pass
    # Restore previous vault status.
    meta = self._dev_meta.pop(project_id, {})
    prev = meta.get("prev_status") or "scaffolded"
    rel = self._vault_rel(project_id)
    if self.vault_get_properties(rel):
        self.vault_update(rel, {"status": prev, "updated": datetime.now().strftime("%Y-%m-%d")})
    await self.emit("forge:dev_stopped", {"id": project_id})
    return {"ok": True, "was_running": True}


async def tail_dev(self, project_id: str, offset: int = 0) -> dict:
    rec = self._running.get(project_id)
    if not rec:
        return {"running": False, "offset": offset, "chunk": ""}
    try:
        data = rec.log_path.read_bytes()
    except Exception:
        data = b""
    chunk = data[offset:]
    return {
        "running": True,
        "pid": rec.pid,
        "offset": offset + len(chunk),
        "chunk": chunk.decode("utf-8", errors="replace"),
    }


async def build(self, project_id: str) -> dict:
    """Run `npm run tauri build` and persist the result on the vault note.

    Build is one-shot — blocks for the duration of the build (~5-10 min).
    Status flips to "building" during, "built" or "build-failed" after.
    """
    rel = self._vault_rel(project_id)
    fm = self.vault_get_properties(rel)
    if not fm:
        return {"error": "Project not found"}
    target_id = fm.get("target") or ""
    tgt = TARGETS.get(target_id)
    if not tgt:
        return {"error": f"unknown target '{target_id}'"}
    repo_path = Path(fm.get("repo_path") or "").expanduser()
    if not repo_path.exists():
        return {"error": f"repo missing on disk: {repo_path}"}

    try:
        runtime = self.require("agent-runtime")
    except Exception:
        return {"error": "agent-runtime unavailable"}

    today = datetime.now().strftime("%Y-%m-%d")
    self.vault_update(rel, {"status": "building", "updated": today})

    async with self._lock_for(project_id):
        try:
            result = await tgt.build(repo_path=repo_path, runtime=runtime, on_log=lambda _: None)
        except Exception as e:
            self.vault_update(rel, {"status": "build-failed", "updated": today})
            return {"error": f"build crashed: {e}"}

    if result.success:
        self.vault_update(
            rel,
            {
                "status": "built",
                "updated": today,
                "last_build": today,
            },
        )
        await self.emit(
            "forge:built",
            {"id": project_id, "artifacts": [str(p) for p in result.artifacts]},
        )
        return {
            "ok": True,
            "duration_s": result.duration_s,
            "artifacts": [str(p) for p in result.artifacts],
            "log_path": str(result.log_path) if result.log_path else "",
        }
    else:
        self.vault_update(rel, {"status": "build-failed", "updated": today})
        return {
            "ok": False,
            "error": result.error,
            "duration_s": result.duration_s,
            "log_path": str(result.log_path) if result.log_path else "",
        }


async def release(self, project_id: str, version: str) -> dict:
    """Cut a release: bumps versions, commits, tags `vX.Y.Z`, pushes
    if `origin` exists. Cross-platform installers are produced by the
    GH Actions workflow that the scaffold ships — forge just fires
    the tag.
    """
    rel = self._vault_rel(project_id)
    fm = self.vault_get_properties(rel)
    if not fm:
        return {"error": "Project not found"}
    target_id = fm.get("target") or ""
    tgt = TARGETS.get(target_id)
    if not tgt:
        return {"error": f"unknown target '{target_id}'"}
    repo_path = Path(fm.get("repo_path") or "").expanduser()
    if not repo_path.exists():
        return {"error": f"repo missing on disk: {repo_path}"}

    try:
        runtime = self.require("agent-runtime")
    except Exception:
        return {"error": "agent-runtime unavailable"}

    today = datetime.now().strftime("%Y-%m-%d")
    async with self._lock_for(project_id):
        try:
            result = await tgt.release(
                repo_path=repo_path,
                version=version,
                runtime=runtime,
                on_log=lambda _: None,
            )
        except Exception as e:
            return {"ok": False, "error": f"release crashed: {type(e).__name__}: {e}"}

    if result.success:
        self.vault_update(
            rel,
            {
                "status": "released",
                "version": result.version,
                "updated": today,
                "last_release": today,
            },
        )
        await self.emit(
            "forge:released",
            {
                "id": project_id,
                "version": result.version,
                "tag": result.tag,
                "pushed": result.pushed,
            },
        )
    # Returned regardless of success; success=False still has useful
    # fields (tag created locally, files bumped, etc.) the UI shows.
    return {
        "ok": result.success,
        "version": result.version,
        "tag": result.tag,
        "commit_sha": result.commit_sha,
        "pushed": result.pushed,
        "files_bumped": result.files_bumped,
        "error": result.error,
    }


@web_route("POST", "/api/projects/{id}/dev")
async def api_dev(self, request):
    return await self.start_dev(request.path_params["id"])


@web_route("POST", "/api/projects/{id}/dev/stop")
async def api_dev_stop(self, request):
    return await self.stop_dev(request.path_params["id"])


@web_route("GET", "/api/projects/{id}/dev/tail")
async def api_dev_tail(self, request):
    offset = int(request.query_params.get("offset", "0") or 0)
    return await self.tail_dev(request.path_params["id"], offset=offset)


@web_route("POST", "/api/projects/{id}/build")
async def api_build(self, request):
    return await self.build(request.path_params["id"])


@web_route("POST", "/api/projects/{id}/release")
async def api_release(self, request):
    body = await request.json()
    version = (body.get("version") or "").strip()
    if not version:
        return {"error": "version is required (e.g. '1.2.3')"}
    return await self.release(request.path_params["id"], version)
