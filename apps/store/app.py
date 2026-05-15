"""Store app — local catalog + install gate over apps, plugins, skills.

V1 is local-only: every manifest in `apps/` + `apps/personal/` + `plugins/` and
every `.claude/skills/eos-*/` shows up in the store. Install/uninstall flips
per-user state (or moves a folder, for skills); the running daemon doesn't
reload code — next `restart.bat` picks up the new set.

State location:
- Apps:    `data/store/installed-apps.json`     (see `emptyos/runtime/store_state.py`)
- Plugins: `data/store/installed-plugins.json`  (same module)
- Skills:  filesystem — folder at `.claude/skills/eos-<id>/` (installed) vs
           `.claude/skills/_archive/eos-<id>/` (uninstalled)

Community-plugin install model — restart required after install/uninstall for
apps + plugins; skills hot-load because Claude Code reads the skills directory
live.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from emptyos.sdk import BaseApp, web_route
from emptyos.runtime import store_state


# Skills that can't be uninstalled — without these the session-wrapup ↔
# session-resume loop breaks, which is the load-bearing leverage the user
# gets from the .claude/skills/ directory.
ESSENTIAL_SKILLS: frozenset[str] = frozenset({"eos-session-resume", "eos-session-wrapup"})

# Frontmatter parse — first `---` to first `---` block.
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


class StoreApp(BaseApp):
    """Read catalog, list with install state, flip install state on POST."""

    # ── Catalog endpoints ──────────────────────────────────────────────────

    @web_route("GET", "/api/catalog/apps")
    async def api_catalog_apps(self, request) -> dict:
        """List every discovered app manifest with install + enable state."""
        loader = self.kernel.apps
        installed = loader.installed_ids()
        disabled = loader.disabled_ids()
        essentials = loader.essential_ids()
        items: list[dict] = []
        for app_id, m in loader.manifests.items():
            # Skip alias-duplicates — _ManifestRegistry iteration is already
            # canonical-only, but defend against future changes.
            if not isinstance(m.raw, dict):
                continue
            requires_apps = list(m.requires.get("apps", []) or [])
            items.append({
                "id": app_id,
                "name": m.name,
                "description": m.description,
                "version": m.version,
                "category": m.raw.get("app", {}).get("store_category", "other"),
                "private": bool(m.raw.get("app", {}).get("private", False)),
                "requires_apps": requires_apps,
                "dependents": self._dependents_of(app_id, kind="apps"),
                "installed": app_id in installed,
                "disabled": app_id in disabled,
                "parked": bool(getattr(m, "parked", False)),
                "essential": app_id in essentials,
            })
        items.sort(key=lambda x: (x["category"], x["name"].lower()))
        return {"items": items, "essentials": sorted(essentials)}

    @web_route("GET", "/api/catalog/plugins")
    async def api_catalog_plugins(self, request) -> dict:
        """List every discovered plugin manifest with install + enable + capability info."""
        loader = self.kernel.plugins
        installed = loader.installed_ids()
        disabled = loader.disabled_ids()
        essentials = loader.essential_ids()
        items: list[dict] = []
        for pid, m in loader.manifests.items():
            cap_section = m.provides.get("capability", {}) or {}
            cap_summary: list[str] = []
            for cap_name, info in cap_section.items():
                provider = (info or {}).get("provider") if isinstance(info, dict) else None
                cap_summary.append(f"{cap_name}{':' + provider if provider else ''}")
            items.append({
                "id": pid,
                "name": m.name,
                "description": m.description,
                "version": m.version,
                "category": m.raw.get("plugin", {}).get("store_category", "other"),
                "provides_services": list(m.provides.get("services", []) or []),
                "provides_capabilities": cap_summary,
                "dependents": [],  # plugins don't have manifest-declared dependents today
                "installed": pid in installed,
                "disabled": pid in disabled,
                "essential": pid in essentials,
            })
        items.sort(key=lambda x: (x["category"], x["name"].lower()))
        return {"items": items, "essentials": sorted(essentials)}

    @web_route("GET", "/api/catalog/skills")
    async def api_catalog_skills(self, request) -> dict:
        """List every `.claude/skills/eos-*` folder (installed) and `_archive/eos-*` (uninstalled).

        Skill metadata (name, description) parsed from each SKILL.md's YAML
        frontmatter. Folder location is the source of truth — no JSON state file.
        """
        live, archive = self._skill_dirs()
        items: list[dict] = []
        for skill_dir in sorted(live.glob("eos-*")):
            if not skill_dir.is_dir():
                continue
            items.append(self._skill_card(skill_dir, installed=True))
        if archive.exists():
            for skill_dir in sorted(archive.glob("eos-*")):
                if not skill_dir.is_dir():
                    continue
                items.append(self._skill_card(skill_dir, installed=False))
        items.sort(key=lambda x: x["id"])
        return {"items": items, "essentials": sorted(ESSENTIAL_SKILLS)}

    # ── Install / uninstall endpoints ──────────────────────────────────────

    @web_route("POST", "/api/install/{kind}/{item_id}")
    async def api_install(self, request) -> dict:
        kind = request.path_params["kind"]
        item_id = request.path_params["item_id"]
        if kind not in ("apps", "plugins", "skills"):
            return {"error": f"unknown kind: {kind}"}
        body = await self.safe_json(request)
        also_install_deps = bool(body.get("install_deps", False))

        if kind == "skills":
            return self._install_skill(item_id)

        loader = self.kernel.apps if kind == "apps" else self.kernel.plugins
        if item_id not in loader.manifests:
            return {"error": f"'{item_id}' not in catalog"}
        m = loader.manifests[item_id]

        # If parked (apps only — plugins don't have a _catalog/ today), the
        # folder must be moved out before the install flag means anything.
        # Dep walk also runs against the post-move state.
        if kind == "apps" and getattr(m, "parked", False):
            err = self._unpark_app(item_id)
            if err:
                return {"error": err}

        # Dependency walk (apps only — plugins don't have inter-plugin deps yet).
        missing_deps: list[str] = []
        if kind == "apps":
            requires_apps = m.requires.get("apps", []) or []
            installed_apps = self.kernel.apps.installed_ids()
            missing_deps = [d for d in requires_apps if d not in installed_apps]
            if missing_deps and not also_install_deps:
                return {
                    "ok": False,
                    "needs_confirm": True,
                    "missing_deps": missing_deps,
                    "message": (
                        f"Installing '{item_id}' also requires: {', '.join(missing_deps)}. "
                        "POST again with {\"install_deps\": true} to install all."
                    ),
                }
            for dep in missing_deps:
                if dep in self.kernel.apps.manifests:
                    dep_m = self.kernel.apps.manifests[dep]
                    if getattr(dep_m, "parked", False):
                        err = self._unpark_app(dep)
                        if err:
                            return {"error": f"dep '{dep}': {err}"}
                    store_state.mark_installed(
                        self.kernel.config.data_dir, "apps", dep, dep_m.version
                    )

        store_state.mark_installed(
            self.kernel.config.data_dir, kind, item_id, m.version
        )
        await self.emit(
            "store:installed",
            {"kind": kind, "id": item_id, "deps_installed": missing_deps},
        )
        return {
            "ok": True,
            "restart_required": True,
            "deps_installed": missing_deps,
            "message": f"'{item_id}' installed. Run restart.bat to load it.",
        }

    @web_route("POST", "/api/uninstall/{kind}/{item_id}")
    async def api_uninstall(self, request) -> dict:
        kind = request.path_params["kind"]
        item_id = request.path_params["item_id"]
        if kind not in ("apps", "plugins", "skills"):
            return {"error": f"unknown kind: {kind}"}
        body = await self.safe_json(request)
        force = bool(body.get("force", False))
        # Optional deeper uninstall — moves the folder to apps/_catalog/ so
        # the code can be reinstalled later. Default false = V1 behaviour
        # (state-flip only, code stays put). Apps only; meaningless for
        # plugins (no plugins/_catalog/ today) and skills (already
        # folder-move via _archive/).
        also_park = bool(body.get("also_park", False))

        if kind == "skills":
            if item_id in ESSENTIAL_SKILLS:
                return {"error": f"'{item_id}' is essential — cannot uninstall via store."}
            return self._uninstall_skill(item_id)

        # Essential guard (apps + plugins)
        loader = self.kernel.apps if kind == "apps" else self.kernel.plugins
        if item_id in loader.essential_ids():
            return {"error": f"'{item_id}' is essential — cannot uninstall via store."}

        # Dependents check (apps only)
        dependents: list[str] = []
        if kind == "apps":
            dependents = self._dependents_of(item_id, kind="apps", installed_only=True)
            if dependents and not force:
                return {
                    "ok": False,
                    "needs_confirm": True,
                    "dependents_broken": dependents,
                    "message": (
                        f"Uninstalling '{item_id}' will break: {', '.join(dependents)}. "
                        "POST again with {\"force\": true} to uninstall anyway."
                    ),
                }

        changed = store_state.mark_uninstalled(
            self.kernel.config.data_dir, kind, item_id
        )
        if not changed:
            return {"ok": True, "restart_required": False, "message": f"'{item_id}' was not installed."}

        # Optional: physically move the source to apps/_catalog/ so it's
        # listed as "Parked" rather than just "Not installed" on next boot.
        parked = False
        park_error = ""
        if kind == "apps" and also_park:
            park_error = self._park_app(item_id) or ""
            parked = not park_error

        if parked:
            tail = " Source moved to apps/_catalog/."
        elif park_error:
            tail = f" (also-park failed: {park_error})"
        else:
            tail = ""

        await self.emit(
            "store:uninstalled",
            {"kind": kind, "id": item_id, "dependents_broken": dependents, "parked": parked},
        )
        return {
            "ok": True,
            "restart_required": True,
            "dependents_broken": dependents,
            "message": f"'{item_id}' uninstalled.{tail} Run restart.bat to unload it.",
        }

    @web_route("POST", "/api/disable/{kind}/{item_id}")
    async def api_disable(self, request) -> dict:
        """Disable an installed app/plugin. Stays installed (config preserved)
        but doesn't load at next boot. Skills don't support disable —
        uninstall them instead (folder move is the only meaningful state).
        """
        kind = request.path_params["kind"]
        item_id = request.path_params["item_id"]
        if kind == "skills":
            return {"error": "skills don't support disable — use uninstall."}
        if kind not in ("apps", "plugins"):
            return {"error": f"unknown kind: {kind}"}

        loader = self.kernel.apps if kind == "apps" else self.kernel.plugins
        if item_id in loader.essential_ids():
            return {"error": f"'{item_id}' is essential — cannot disable."}
        if item_id not in loader.installed_ids():
            return {"error": f"'{item_id}' is not installed — nothing to disable."}

        changed = store_state.mark_disabled(
            self.kernel.config.data_dir, kind, item_id
        )
        if not changed:
            return {"ok": True, "restart_required": False, "message": f"'{item_id}' is already disabled."}
        await self.emit("store:disabled", {"kind": kind, "id": item_id})
        return {
            "ok": True,
            "restart_required": True,
            "message": f"'{item_id}' disabled. Run restart.bat to unload it.",
        }

    @web_route("POST", "/api/enable/{kind}/{item_id}")
    async def api_enable(self, request) -> dict:
        """Re-enable a previously-disabled app/plugin. Skills don't disable
        separately; this endpoint is a no-op for them.
        """
        kind = request.path_params["kind"]
        item_id = request.path_params["item_id"]
        if kind == "skills":
            return {"error": "skills don't support enable — use install."}
        if kind not in ("apps", "plugins"):
            return {"error": f"unknown kind: {kind}"}

        loader = self.kernel.apps if kind == "apps" else self.kernel.plugins
        if item_id not in loader.installed_ids():
            return {"error": f"'{item_id}' is not installed — install it first."}

        changed = store_state.mark_enabled(
            self.kernel.config.data_dir, kind, item_id
        )
        if not changed:
            return {"ok": True, "restart_required": False, "message": f"'{item_id}' is already enabled."}
        await self.emit("store:enabled", {"kind": kind, "id": item_id})
        return {
            "ok": True,
            "restart_required": True,
            "message": f"'{item_id}' enabled. Run restart.bat to load it.",
        }

    @web_route("GET", "/api/restart-required")
    async def api_restart_required(self, request) -> dict:
        """Cheap heuristic — `last_change` after daemon boot ⇒ restart pending.

        Daemon boot time is wall-clock at module import (close enough). The
        UI uses this to surface a sticky banner after the user clicks
        install/uninstall.
        """
        boot_ts = _boot_ts()
        apps_change = store_state.last_change(self.kernel.config.data_dir, "apps")
        plugins_change = store_state.last_change(self.kernel.config.data_dir, "plugins")
        pending = False
        for change in (apps_change, plugins_change):
            if change and _iso_to_ts(change) > boot_ts:
                pending = True
                break
        return {"restart_required": pending}

    # ── Helpers ────────────────────────────────────────────────────────────

    def _dependents_of(self, item_id: str, *, kind: str, installed_only: bool = False) -> list[str]:
        """Apps in the manifest registry that declare item_id in their [requires.apps].

        `installed_only=True` filters to currently-installed dependents — used
        on uninstall to decide whether to warn the user.
        """
        if kind != "apps":
            return []
        loader = self.kernel.apps
        installed = loader.installed_ids() if installed_only else None
        out: list[str] = []
        for aid, m in loader.manifests.items():
            if aid == item_id:
                continue
            reqs = m.requires.get("apps", []) or []
            if item_id in reqs:
                if installed is None or aid in installed:
                    out.append(aid)
        return sorted(out)

    def _apps_root(self) -> Path:
        """Resolve the apps/ root the kernel discovered from. Same logic
        as `app_loader.discover()` so install/uninstall move folders into
        a location the next boot's discovery will see."""
        apps_path = Path(self.kernel.config.get("apps.path", "./apps"))
        if not apps_path.is_absolute():
            apps_path = Path(self.kernel.config.path).parent / apps_path
        return apps_path.resolve()

    def _move_dir(self, src: Path, dst: Path) -> str | None:
        """Rename src → dst with the safety guards every caller shares:
        src must exist, dst must NOT (no clobber), dst's parent is
        autocreated. Returns None on success or a one-line error string.

        Used by app park/unpark and skill install/uninstall — every site
        is "move one app/skill folder between two stable locations" with
        identical failure modes."""
        if not src.exists():
            return f"source not found at {src}"
        if dst.exists():
            return f"destination already exists at {dst}"
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dst)
            return None
        except OSError as e:
            return f"move failed: {e}"

    def _unpark_app(self, app_id: str) -> str | None:
        apps_root = self._apps_root()
        src = apps_root / "_catalog" / app_id
        dst = apps_root / app_id
        err = self._move_dir(src, dst)
        if err:
            return err
        # Update in-memory manifest so the rest of the request sees the new state.
        m = self.kernel.apps.manifests.get(app_id)
        if m is not None:
            m.parked = False
            m.path = dst
        return None

    def _park_app(self, app_id: str) -> str | None:
        apps_root = self._apps_root()
        src = apps_root / app_id
        dst = apps_root / "_catalog" / app_id
        err = self._move_dir(src, dst)
        if err:
            return err
        m = self.kernel.apps.manifests.get(app_id)
        if m is not None:
            m.parked = True
            m.path = dst
        return None

    def _skill_dirs(self) -> tuple[Path, Path]:
        """Return (live, archive) skill dirs. Repo-root anchored."""
        repo_root = Path(self.kernel.config.path).parent.resolve()
        live = repo_root / ".claude" / "skills"
        archive = live / "_archive"
        return live, archive

    def _skill_card(self, skill_dir: Path, *, installed: bool) -> dict:
        """Build a catalog entry from a skill folder. Parses SKILL.md frontmatter."""
        skill_id = skill_dir.name
        skill_md = skill_dir / "SKILL.md"
        name = skill_id
        description = ""
        if skill_md.exists():
            try:
                text = skill_md.read_text(encoding="utf-8", errors="replace")
                m = _FRONTMATTER_RE.match(text)
                if m:
                    fm = m.group(1)
                    for line in fm.splitlines():
                        if ":" not in line:
                            continue
                        key, _, value = line.partition(":")
                        key = key.strip().lower()
                        value = value.strip()
                        if key == "name" and value:
                            name = value
                        elif key == "description" and value:
                            description = value
            except OSError:
                pass
        return {
            "id": skill_id,
            "name": name,
            "description": description[:400],
            "installed": installed,
            "essential": skill_id in ESSENTIAL_SKILLS,
        }

    def _install_skill(self, skill_id: str) -> dict:
        live, archive = self._skill_dirs()
        src = archive / skill_id
        dst = live / skill_id
        if dst.exists():
            return {"ok": True, "restart_required": False, "message": f"Skill '{skill_id}' already installed."}
        if not src.exists():
            return {"error": f"Skill '{skill_id}' not found in archive."}
        err = self._move_dir(src, dst)
        if err:
            return {"error": f"Could not install '{skill_id}': {err}"}
        # No daemon restart needed for skills — Claude Code reads live folder.
        return {"ok": True, "restart_required": False, "message": f"Skill '{skill_id}' installed."}

    def _uninstall_skill(self, skill_id: str) -> dict:
        live, archive = self._skill_dirs()
        src = live / skill_id
        dst = archive / skill_id
        if not src.exists():
            return {"ok": True, "restart_required": False, "message": f"Skill '{skill_id}' was not installed."}
        # Stale archive entry from a prior install/uninstall cycle would
        # block the rename. Clear it first; the user already saw this id
        # disappear once, so collision means leftover, not real data.
        if dst.exists():
            import shutil
            shutil.rmtree(dst, ignore_errors=True)
        err = self._move_dir(src, dst)
        if err:
            return {"error": f"Could not uninstall '{skill_id}': {err}"}
        return {"ok": True, "restart_required": False, "message": f"Skill '{skill_id}' moved to _archive/."}


# ── Module-level helpers (not on the class — pure functions) ─────────────

_BOOT_TS_CACHE: list[float] = []


def _boot_ts() -> float:
    """Process start time, cached. Used as the restart-required cutoff."""
    if not _BOOT_TS_CACHE:
        _BOOT_TS_CACHE.append(time.time())
    return _BOOT_TS_CACHE[0]


def _iso_to_ts(iso: str) -> float:
    """Parse an ISO timestamp to epoch seconds. Tolerant — returns 0 on failure."""
    try:
        from datetime import datetime
        return datetime.fromisoformat(iso).timestamp()
    except (ValueError, TypeError):
        return 0.0
