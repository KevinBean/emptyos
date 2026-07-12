"""Forge — scaffold and track native apps.

A forge "project" is a vault-tracked record pointing at an external git
repo on disk. Forge owns the lifecycle verbs (scaffold / dev / build / …);
the spawned project is independent — its code lives outside the daemon and
can be opened in any editor.

Two storage domains:
- Vault note  `{vault}/30_Resources/EmptyOS/forge/<id>.md` — frontmatter
  with id/target/repo_path/status/version, body sections `## Design` +
  `## Changelog`.
- Run telemetry  `data/apps/forge/runs/<run_id>/` — stdout logs, build
  artifacts list.

Target dispatch is data-driven via `targets/TARGETS`. Phase 1 implements
the Tauri target only; CLI / Flutter / Electron surface as "coming soon".
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from emptyos.sdk import BaseApp, web_route

from . import dev as _dev
from . import projects as _projects

from .targets import COMING_SOON, ProcessRecord, TARGETS
from .projects import FORGE_TAG  # canonical declaration lives in the projects helper



class ForgeApp(BaseApp):

    SETTABLE_FIELDS = {"name", "git_remote"}

    async def setup(self):
        await super().setup()
        # Live dev processes keyed by project_id. Survives across HTTP
        # requests; cleaned up on app teardown.
        self._running: dict[str, ProcessRecord] = {}
        # Sidecar metadata for live dev sessions (e.g. previous vault status
        # so stop_dev can restore it). Kept separate from ProcessRecord so
        # the dataclass stays pure.
        self._dev_meta: dict[str, dict] = {}
        # Serialise scaffold/build per project to avoid colliding on the
        # same repo directory (npm install, cargo build).
        self._locks: dict[str, asyncio.Lock] = {}

    async def teardown(self):
        """Kill every live dev process. Called when the daemon shuts down."""
        for pid, rec in list(self._running.items()):
            try:
                proc = rec.proc
                if proc and getattr(proc, "returncode", None) is None:
                    proc.kill()
            except Exception:
                pass
        self._running.clear()

    async def panel_projects(self) -> dict | None:
        """Hub stat-tile: count of native projects, with running count folded
        into the label when non-zero."""
        projects = await self.list_projects()
        if not projects:
            return None
        running = sum(1 for p in projects if p["running"])
        label = "Native"
        if running:
            label = f"Native · {running} dev"
        return {
            "icon": "🔨",
            "label": label,
            "value": str(len(projects)),
            "href": "/forge/",
        }

    # ── Dev (extracted to dev.py) ──
    start_dev    = _dev.start_dev
    stop_dev     = _dev.stop_dev
    tail_dev     = _dev.tail_dev
    build        = _dev.build
    release      = _dev.release
    api_dev      = _dev.api_dev
    api_dev_stop = _dev.api_dev_stop
    api_dev_tail = _dev.api_dev_tail
    api_build    = _dev.api_build
    api_release  = _dev.api_release

    # ── Projects (extracted to projects.py) ──
    _default_root        = _projects._default_root
    _vault_rel           = _projects._vault_rel
    _data_dir            = _projects._data_dir
    _lock_for            = _projects._lock_for
    list_projects        = _projects.list_projects
    list_all             = _projects.list_all
    get_project          = _projects.get_project
    set_field            = _projects.set_field
    update_design        = _projects.update_design
    delete_project       = _projects.delete_project
    create_project       = _projects.create_project
    _create_project_impl = _projects._create_project_impl
    api_targets          = _projects.api_targets
    api_list             = _projects.api_list
    api_create           = _projects.api_create
    api_get              = _projects.api_get
    api_delete           = _projects.api_delete
    api_update_design    = _projects.api_update_design
    api_set_field        = _projects.api_set_field
