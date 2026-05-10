"""App loader — discovers, loads, and manages app lifecycle."""

from __future__ import annotations

import time
import tomllib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from emptyos.kernel.module_import import load_module

if TYPE_CHECKING:
    from emptyos.kernel import Kernel


class AppState(Enum):
    DISCOVERED = "discovered"
    LOADED = "loaded"
    STARTED = "started"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class AppManifest:
    """Parsed manifest.toml for an app."""

    id: str
    name: str
    version: str
    description: str
    path: Path  # Directory containing the app
    entry_module: str = "app"
    entry_class: str | None = None
    provides: dict[str, Any] = field(default_factory=dict)
    requires: dict[str, Any] = field(default_factory=dict)
    events_emits: list[str] = field(default_factory=list)
    events_listens: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_toml(cls, manifest_path: Path) -> AppManifest:
        with open(manifest_path, "rb") as f:
            data = tomllib.load(f)
        app_section = data.get("app", {})
        entry = app_section.get("entry", {})
        events = data.get("provides", {}).get("events", {})
        return cls(
            id=app_section["id"],
            name=app_section.get("name", app_section["id"]),
            version=app_section.get("version", "0.0.0"),
            description=app_section.get("description", ""),
            path=manifest_path.parent,
            entry_module=entry.get("module", "app"),
            entry_class=entry.get("class"),
            provides=data.get("provides", {}),
            requires=data.get("requires", {}),
            events_emits=events.get("emits", []),
            events_listens=events.get("listens", []),
            aliases=app_section.get("aliases", []) or [],
            raw=data,
        )


class _ManifestRegistry(dict):
    """Dict that resolves aliases on lookup but iterates canonical entries only.

    Why: aliases must be reachable via `get(alias)`/`alias in registry` for
    cross-app calls and dependency strings, but values()/items()/iter must not
    duplicate the same manifest under multiple keys (otherwise UI launchers,
    health counts, etc. show the same app twice).
    """

    def __init__(self):
        super().__init__()
        self._aliases: dict[str, str] = {}

    def alias(self, alias: str, canonical: str) -> None:
        self._aliases[alias] = canonical

    def __getitem__(self, key):
        if key in self._aliases and not super().__contains__(key):
            key = self._aliases[key]
        return super().__getitem__(key)

    def __contains__(self, key):
        return super().__contains__(key) or key in self._aliases

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def clear(self):
        super().clear()
        self._aliases.clear()


class AppLoader:
    """Discovers and manages apps."""

    def __init__(self, kernel: Kernel):
        self.kernel = kernel
        self.manifests: _ManifestRegistry = _ManifestRegistry()
        self.instances: dict[str, Any] = {}
        self.states: dict[str, AppState] = {}
        # Per-app load timings (import_ms / setup_ms / total_ms) so a
        # slow boot can be diagnosed after the fact via /system/diag.
        self._load_timings: dict[str, dict[str, float]] = {}

    @property
    def running(self) -> list[str]:
        return [aid for aid, s in self.states.items() if s == AppState.STARTED]

    def discover(self) -> list[AppManifest]:
        """Scan apps directory for manifest.toml files."""
        apps_path = Path(self.kernel.config.get("apps.path", "./apps"))
        if not apps_path.is_absolute():
            apps_path = Path(self.kernel.config.path).parent / apps_path
        apps_path = apps_path.resolve()

        self.manifests.clear()
        if not apps_path.exists():
            return []

        # Scan core apps + personal apps (if present)
        scan_dirs = [apps_path]
        personal_path = apps_path / "personal"
        if personal_path.exists():
            scan_dirs.append(personal_path)

        # Demo-mode app suppression. Two mechanisms:
        #   - demo.hide_apps (config list): deployment-time blocklist for apps
        #     whose infra (camera, GPU, voice-api service) isn't available in
        #     a given demo environment. Maintained per deployment.
        #   - [app] private = true (manifest flag): app self-declares "I am not
        #     for public/demo deployments". Same effect as hide_apps, but lives
        #     with the app, so every demo deployment honours it without the
        #     operator having to remember to add it.
        # `private = true` is gated on demo.enabled (it's a "don't show in demo"
        # flag, not a hard exclusion); `demo.hide_apps` is honoured whenever
        # set, which preserves existing behaviour for operators who used it as
        # a generic blocklist before this flag existed.
        demo_on = self.kernel.config.demo_enabled
        hide = set(self.kernel.config.get("demo.hide_apps", []) or [])

        for scan_dir in scan_dirs:
            for manifest_file in sorted(scan_dir.glob("*/manifest.toml")):
                # Skip _example, _retired, and other underscore-prefixed dirs
                if manifest_file.parent.name.startswith("_"):
                    continue
                if manifest_file.parent.name in hide:
                    continue
                try:
                    manifest = AppManifest.from_toml(manifest_file)
                    if manifest.id in hide:
                        continue
                    if demo_on and manifest.raw.get("app", {}).get("private", False):
                        continue
                    # Only warn on real canonical-id collisions, not when a new
                    # app's id happens to match an alias from another app —
                    # `in self.manifests` would match either via __contains__.
                    if dict.__contains__(self.manifests, manifest.id):
                        self.kernel.syslog.warn(
                            "app_loader",
                            f"'{manifest.id}' in {scan_dir.name}/ overrides {self.manifests[manifest.id].path}",
                        )
                    self.manifests[manifest.id] = manifest
                    self.states[manifest.id] = AppState.DISCOVERED
                    # Aliases also resolve to this manifest (for dependency strings)
                    # but iteration yields canonical entries only.
                    for alias in manifest.aliases:
                        if alias not in self.manifests:
                            self.manifests.alias(alias, manifest.id)
                except Exception as e:
                    self.kernel.syslog.error("app_loader", f"Failed to parse {manifest_file}: {e}")

        return list(self.manifests.values())

    async def load(self, app_id: str, _loading: set[str] | None = None) -> Any:
        """Import and instantiate an app. Loads required apps first.

        Circular dependencies between apps are allowed — cross-app calls are
        lazy at runtime, so A→B→A declarations are valid as long as the calls
        happen after both apps are loaded. The `_loading` set breaks the
        recursion when we revisit an app that's already mid-load."""
        if app_id in self.instances:
            return self.instances[app_id]  # already loaded

        if _loading is None:
            _loading = set()
        if app_id in _loading:
            # Cycle — bail out; the other side of the cycle will register us once done.
            return None
        _loading = _loading | {app_id}

        manifest = self.manifests.get(app_id)
        if not manifest:
            raise KeyError(f"App not found: {app_id}")

        # Load required apps first (dependency resolution from graph)
        for dep_app in manifest.requires.get("apps", []):
            if dep_app not in self.instances and dep_app in self.manifests:
                await self.load(dep_app, _loading=_loading)

        # Validate declared dependencies
        self._validate_dependencies(app_id, manifest)

        try:
            t_start = time.perf_counter()
            module_file = manifest.path / f"{manifest.entry_module}.py"
            instance = load_module(
                module_file,
                f"eos_apps.{app_id}",
                manifest.path,
                manifest.entry_class,
                self.kernel,
                manifest,
            )
            t_import = time.perf_counter()

            self.instances[app_id] = instance
            self.states[app_id] = AppState.LOADED
            # Register manifest aliases — call_app("old_id", ...) keeps working after a rename.
            for alias in manifest.aliases:
                if alias not in self.instances:
                    self.instances[alias] = instance

            # Run setup if available
            if hasattr(instance, "setup"):
                await instance.setup()
            t_setup = time.perf_counter()

            # Register scheduled jobs if scheduler is available
            if self.kernel.scheduler:
                self.kernel.scheduler.register_app_jobs(app_id, instance)

            # Boot-time observability: warn loudly if any single app blocked
            # the loader for more than a second so future slow boots tell us
            # which app stalled instead of going silent.
            import_ms = (t_import - t_start) * 1000.0
            setup_ms = (t_setup - t_import) * 1000.0
            self._load_timings[app_id] = {
                "import_ms": import_ms, "setup_ms": setup_ms,
                "total_ms": import_ms + setup_ms,
            }
            if (import_ms + setup_ms) > 1000.0:
                self.kernel.syslog.warn(
                    "app_loader",
                    f"slow load '{app_id}': import={import_ms:.0f}ms "
                    f"setup={setup_ms:.0f}ms",
                )

            return instance
        except Exception as e:
            self.states[app_id] = AppState.ERROR
            raise RuntimeError(f"Failed to load app '{app_id}': {e}") from e

    async def start(self, app_id: str):
        """Start a loaded app (begin background tasks, register routes)."""
        if app_id not in self.instances:
            await self.load(app_id)
        instance = self.instances[app_id]
        if hasattr(instance, "start"):
            await instance.start()
        self.states[app_id] = AppState.STARTED

    async def stop(self, app_id: str):
        """Stop a running app."""
        # Unregister scheduled jobs
        if self.kernel.scheduler:
            self.kernel.scheduler.unregister_app_jobs(app_id)

        instance = self.instances.pop(app_id, None)
        if instance and hasattr(instance, "teardown"):
            try:
                await instance.teardown()
            except Exception as e:
                self.kernel.syslog.error("app_loader", f"Error tearing down '{app_id}': {e}")
        self.states[app_id] = AppState.STOPPED

    def get_load_timings(self) -> dict[str, dict[str, float]]:
        """Per-app boot-time load timings (import_ms / setup_ms / total_ms).

        Populated as apps load; useful for diagnosing slow boots.
        Returns a copy so callers can't mutate the loader's state.
        """
        return {aid: dict(t) for aid, t in self._load_timings.items()}

    def get_cli_commands(self) -> dict[str, AppManifest]:
        """Get all apps that provide CLI commands."""
        result = {}
        for app_id, manifest in self.manifests.items():
            cli_section = manifest.provides.get("cli", {})
            for cmd in cli_section.get("commands", []):
                result[cmd] = manifest
        return result

    def get_providers(self, section: str) -> dict[str, Any]:
        """Get all apps that provide a given manifest section.

        Example: get_providers("project-tools") returns {app_id: section_data}
        for every app whose manifest has [provides.project-tools].
        """
        result = {}
        for app_id, manifest in self.manifests.items():
            data = manifest.provides.get(section)
            if data:
                result[app_id] = data
        return result

    def get_contributions(self, target: str, slot: str) -> list[dict]:
        """Gather `[[contributes.<target>.<slot>]]` entries from every app manifest.

        Each returned dict carries the manifest entry plus `_app_id` (the contributor).
        Used by extension-point hosts (hub slots, addon slots, future targets) to
        enumerate who wants to register into a named slot without importing them.
        """
        result: list[dict] = []
        for app_id, manifest in self.manifests.items():
            contributes = manifest.raw.get("contributes", {}).get(target, {}).get(slot)
            if not contributes:
                continue
            if isinstance(contributes, dict):
                contributes = [contributes]
            if not isinstance(contributes, list):
                continue
            for entry in contributes:
                if not isinstance(entry, dict):
                    continue
                result.append({**entry, "_app_id": app_id})
        return result

    def _validate_dependencies(self, app_id: str, manifest: AppManifest):
        """Warn on unmet dependencies. Apps still load (graceful degradation)."""
        requires = manifest.requires
        missing = []

        for cap_name in requires.get("capabilities", []):
            if not self.kernel.capabilities.has(cap_name):
                missing.append(f"capability:{cap_name}")

        for svc_name in requires.get("services", []):
            if not self.kernel.services.has(svc_name):
                missing.append(f"service:{svc_name}")

        for conn_name in requires.get("connectors", []):
            if not self.kernel.services.has(conn_name):
                missing.append(f"connector:{conn_name}")

        for dep_app in requires.get("apps", []):
            if dep_app not in self.manifests:
                missing.append(f"app:{dep_app}")

        if missing:
            self.kernel.syslog.warn("app_loader", f"'{app_id}' unmet deps: {', '.join(missing)}")
