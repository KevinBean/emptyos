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
    # True when the manifest was discovered under apps/_catalog/ — code is
    # on disk but excluded from loading regardless of install state. The
    # store moves the folder out of _catalog/ on install.
    parked: bool = False

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


class _AliasAwareRegistry(dict):
    """Dict that resolves aliases on lookup but iterates canonical entries only.

    Why: aliases must be reachable via `get(alias)`/`alias in registry` for
    cross-app calls and dependency strings, but values()/items()/iter must not
    duplicate the same value under multiple keys (otherwise UI launchers,
    health counts, etc. show the same app twice). Aliases are held in a side
    map, never as real dict entries, so iteration is canonical-only for free.

    Used for BOTH ``manifests`` and ``instances`` — a launcher that iterates
    ``instances.items()`` (hub, people, release) must not see an aliased app
    twice, the same way manifest iteration doesn't.
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

    def pop(self, key, *default):
        """Pop a canonical entry (dropping any aliases that target it), or drop
        an alias-only key. Keeps the side map in sync so a stopped/rolled-back
        app leaves no dangling alias resolving to a missing instance."""
        if super().__contains__(key):
            for a in [a for a, c in self._aliases.items() if c == key]:
                self._aliases.pop(a, None)
            return super().pop(key, *default)
        if key in self._aliases:
            self._aliases.pop(key, None)
            return default[0] if default else None
        if default:
            return default[0]
        raise KeyError(key)

    def clear(self):
        super().clear()
        self._aliases.clear()


# Always-on apps — loaded regardless of the store's installed set.
# Without these the daemon can't be operated: store to manage installs,
# settings to configure things, hub for home. (State inspection lives at the
# built-in /system server route, not an app, so it needs no essential entry.)
# A user who really wants to disable one can edit data/store/installed-apps.json
# directly; the store UI refuses the toggle on these ids.
ESSENTIAL_APPS: frozenset[str] = frozenset({"store", "settings", "hub"})


class AppLoader:
    """Discovers and manages apps."""

    def __init__(self, kernel: Kernel):
        self.kernel = kernel
        self.manifests: _AliasAwareRegistry = _AliasAwareRegistry()
        self.instances: _AliasAwareRegistry = _AliasAwareRegistry()
        self.states: dict[str, AppState] = {}
        # Per-app load timings (import_ms / setup_ms / total_ms) so a
        # slow boot can be diagnosed after the fact via /system/diag.
        self._load_timings: dict[str, dict[str, float]] = {}

    @property
    def running(self) -> list[str]:
        return [aid for aid, s in self.states.items() if s == AppState.STARTED]

    def reachable_manifests(self) -> list[AppManifest]:
        """Manifests a user can actually get to — what `/api/apps` reports.

        An app the loader only DISCOVERED (present on disk, never installed) or
        that ERRORed has no mounted routes, so anything enumerating "the apps"
        for a user-facing surface wants this rather than `manifests.values()`.
        Listing the difference is what made Studio's icon library count 231
        against the launcher's 230, and put a test fixture in a product surface.
        """
        skip = {AppState.DISCOVERED, AppState.ERROR}
        return [m for m in self.manifests.values() if self.state_of(m.id) not in skip]

    def state_of(self, app_id: str) -> AppState:
        """Reported state for an app, reconciled against the live instance.

        `states` is a hand-maintained mirror of `instances`; when the two
        disagree, `instances` is the fact — an app with a live instance had its
        routes mounted and is reachable, whatever the label says. Reporting it
        as DISCOVERED makes a working app invisible to `/api/apps` (and so to
        nav, `/api/apps/sections`, topology, and the four audits that iterate
        that list) while every one of its routes still answers 200.

        Only DISCOVERED is reconciled. ERROR is never upgraded: `load()` pops
        the instance on failure, so ERROR and a live instance cannot both hold,
        and a genuine load error must stay visible. STOPPED is a deliberate
        state and is left alone.

        Read this instead of `states.get(...)` at every reporting surface.
        """
        st = self.states.get(app_id, AppState.DISCOVERED)
        if st is AppState.DISCOVERED and dict.__contains__(self.instances, app_id):
            return AppState.LOADED
        return st

    def discover(self) -> list[AppManifest]:
        """Scan apps directory for manifest.toml files."""
        apps_path = Path(self.kernel.config.get("apps.path", "./apps"))
        if not apps_path.is_absolute():
            apps_path = Path(self.kernel.config.path).parent / apps_path
        apps_path = apps_path.resolve()

        self.manifests.clear()
        if not apps_path.exists():
            return []

        # Discover every app across the track tree — apps/public/<group>/<id>/,
        # apps/extension/<group>/<id>/ — plus the flat legacy layout,
        # apps/personal/ (local), and apps/_catalog/ (parked: code on disk but
        # excluded from loading; the store moves a folder out on install). The
        # depth-agnostic scan rule lives in emptyos.sdk.app_layout so the kernel
        # loader and the release tooling share one source and can't drift.
        # Imported locally to avoid any boot-time import cycle through
        # emptyos.sdk.__init__.
        from emptyos.sdk.app_layout import iter_app_dirs, is_parked

        # Demo-mode app suppression. Two mechanisms:
        #   - demo.hide_apps (config list): deployment-time blocklist for apps
        #     whose infra (camera, GPU, voice-api service) isn't available in
        #     a given demo environment. Maintained per deployment.
        #   - [app] private = true (manifest flag): app self-declares "I am not
        #     for public/demo deployments". Gated on demo.enabled.
        demo_on = self.kernel.config.demo_enabled
        hide = set(self.kernel.config.get("demo.hide_apps", []) or [])

        # Read once, above the loop: it is loop-invariant, and inside the loop
        # it would sit under the per-manifest `except Exception` below, where a
        # missing property degrades into a misleading "Failed to parse <file>"
        # and silently skips that manifest's alias registration.
        booted = self.kernel.started

        for _id_hint, app_dir in iter_app_dirs(
            apps_path, include_personal=True, include_catalog=True
        ):
            manifest_file = app_dir / "manifest.toml"
            if app_dir.name in hide:
                continue
            try:
                manifest = AppManifest.from_toml(manifest_file)
                if manifest.id in hide:
                    continue
                if demo_on and manifest.raw.get("app", {}).get("private", False):
                    continue
                parked = is_parked(app_dir, apps_path)
                manifest.parked = parked
                # A parked manifest must never override an active one — if the
                # same id exists in both a track folder and apps/_catalog/, the
                # active copy wins (the parked one is leftover). Other id
                # collisions warn (last wins).
                if dict.__contains__(self.manifests, manifest.id):
                    existing = self.manifests[manifest.id]
                    if parked and not existing.parked:
                        continue
                    self.kernel.syslog.warn(
                        "app_loader",
                        f"'{manifest.id}' at {app_dir} overrides {existing.path}",
                    )
                self.manifests[manifest.id] = manifest
                # Never downgrade an app that already has a live instance.
                # `states` is a mirror of `instances`, and `load()` short-circuits
                # on anything already in `instances` — so a discover() that runs
                # after a load strands that app at DISCOVERED permanently, and
                # `/api/apps` (plus nav, sections, topology, and the four audits
                # that read it) hides an app whose routes are mounted and serving.
                # This is a real boot path, not a hypothetical (confirmed on the
                # live daemon 2026-08-31, five apps every boot):
                #   1. `cli/main.py::_get_kernel()` calls discover() — #1.
                #   2. `Kernel.start()` runs `plugins.load_all()`, and the
                #      telegram plugin's `connect()` spawns `_poll_loop()`, whose
                #      first tick calls `_ensure_bridge_agent()` →
                #      `apps.load("rooms")`. Manifests are already populated by
                #      #1, so it succeeds and pulls the whole dependency tree:
                #      rooms → projects → {github-connector, git, reports}.
                #   3. `Kernel.start()` then calls discover() — #2 — which used
                #      to stamp all five back to DISCOVERED.
                #   4. The boot loop skips them (`not in instances`), so nothing
                #      ever re-set the state.
                # A sandbox pool member never reproduced it because it has no
                # telegram token, so step 2 never runs.
                #
                # The log line stays, because it is what names the caller if a
                # *different* path starts doing the same thing — but it is only
                # a warning AFTER boot. During boot the five apps above hit it
                # every single time, and five expected WARN lines per boot is
                # noise that a sixth, genuinely new id would disappear into: a
                # detector that always fires carries no signal
                # (`.claude/rules/audits.md` — the inverse of a check that is
                # green because it checks nothing). So boot logs `info`, and a
                # discover() once the kernel is up — the case nobody has
                # explained yet — logs `warn`.
                if dict.__contains__(self.instances, manifest.id):
                    log = self.kernel.syslog.warn if booted else self.kernel.syslog.info
                    log(
                        "app_loader",
                        f"discover() re-ran while '{manifest.id}' is loaded — "
                        f"keeping its state instead of downgrading to discovered",
                    )
                else:
                    self.states[manifest.id] = AppState.DISCOVERED
                # Aliases also resolve to this manifest (for dependency strings)
                # but iteration yields canonical entries only.
                for alias in manifest.aliases:
                    if alias not in self.manifests:
                        self.manifests.alias(alias, manifest.id)
            except Exception as e:
                self.kernel.syslog.error("app_loader", f"Failed to parse {manifest_file}: {e}")

        return list(self.manifests.values())

    def essential_ids(self) -> frozenset[str]:
        """App ids the store cannot disable/uninstall. Public surface so
        consumers (notably `apps/store/`) don't reach into module constants."""
        return ESSENTIAL_APPS

    def parked_ids(self) -> set[str]:
        """App ids whose code lives under apps/_catalog/ rather than apps/.

        Parked apps are always "not installed" no matter what the JSON state
        file says — the filesystem is the source of truth here. Surfaced by
        the store catalog as a distinct status; the install handler moves
        the folder out before flipping JSON state.
        """
        return {aid for aid, m in self.manifests.items() if m.parked}

    def installed_ids(self) -> set[str]:
        """Set of app ids marked installed in the store's state file.

        Pure read of `data/store/installed-apps.json` minus the parked set
        (parked code can't be "installed" — installation includes the
        folder move). Does NOT subtract the disabled set or union
        essentials. Use `enabled_ids()` for the "what should load at boot"
        decision.

        Seeds the file on first call so existing daemons see today's
        behaviour preserved. Demo mode bypasses the gate entirely.
        """
        from emptyos.runtime import store_state

        if self.kernel.config.demo_enabled:
            return {aid for aid, m in self.manifests.items() if not m.parked}

        data_dir = self.kernel.config.data_dir
        # Seed only with non-parked manifests — parked apps must not be
        # auto-marked installed on first boot, otherwise the seed would
        # contradict their filesystem location.
        store_state.seed_if_missing(
            data_dir,
            "apps",
            ((m.id, m.version) for m in self.manifests.values() if not m.parked),
        )
        return store_state.installed_ids(data_dir, "apps") - self.parked_ids()

    def disabled_ids(self) -> set[str]:
        """Set of installed app ids the user has disabled. Essentials cannot be disabled.

        Demo mode reports an empty disabled set — the bundled experience
        ignores per-user disable choices.
        """
        from emptyos.runtime import store_state

        if self.kernel.config.demo_enabled:
            return set()
        return store_state.disabled_ids(self.kernel.config.data_dir, "apps") - ESSENTIAL_APPS

    def enabled_ids(self) -> set[str]:
        """`installed - disabled ∪ essentials` — what the kernel should load.

        This is the set Kernel.start(), `cli start`, and the web lazy-load
        middleware all iterate over. Essentials are unioned in last so a
        determined user disabling `store` via direct file edit still gets
        the store back at boot.
        """
        if self.kernel.config.demo_enabled:
            return set(self.manifests.keys()) | ESSENTIAL_APPS
        return (self.installed_ids() - self.disabled_ids()) | ESSENTIAL_APPS

    def enabled_manifests(self) -> dict[str, AppManifest]:
        """Discovered manifests filtered by enabled_ids().

        What gets actually loaded. Distinct from `installed_ids()` —
        a disabled-but-installed app is in `installed_ids()` but not here.
        """
        enabled = self.enabled_ids()
        return {aid: m for aid, m in self.manifests.items() if aid in enabled}

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

        # Load required apps first (dependency resolution from graph). Respect
        # the Store gate: a disabled/uninstalled dependency is a runtime unmet
        # dependency, not a reason to silently load code the user pruned.
        enabled: set[str] | None = None
        for dep_app in manifest.requires.get("apps", []):
            if dep_app not in self.instances and dep_app in self.manifests:
                if enabled is None:
                    try:
                        enabled = self.enabled_ids()
                    except Exception:
                        enabled = set(self.manifests.keys())
                if dep_app not in enabled:
                    continue
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
            # Drift check: warn loudly if any declared [[provides.verbs]] method
            # is missing from the live instance (renamed/typo). Never raises.
            self._validate_app_verbs(app_id, manifest, instance)
            # Register manifest aliases — call_app("old_id", ...) keeps working after a
            # rename. Aliases live in the registry's side map (not as real dict
            # entries), so instances.items()/values() stay canonical-only and launchers
            # that iterate them (hub, people, release) don't render the app twice.
            for alias in manifest.aliases:
                if alias not in self.instances:
                    self.instances.alias(alias, app_id)

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
                "import_ms": import_ms,
                "setup_ms": setup_ms,
                "total_ms": import_ms + setup_ms,
            }
            if (import_ms + setup_ms) > 1000.0:
                self.kernel.syslog.warn(
                    "app_loader",
                    f"slow load '{app_id}': import={import_ms:.0f}ms setup={setup_ms:.0f}ms",
                )

            return instance
        except Exception as e:
            self.states[app_id] = AppState.ERROR
            # Drop the half-initialized instance + its aliases. `instance` is
            # registered at line ~317 *before* setup() runs so in-flight cyclic
            # call_app can resolve; if setup() then throws we must not leave a
            # zombie behind, or a later load() short-circuits on the cached
            # instance (skipping setup) and routes mount for an app whose
            # capability providers / event subs never registered. Popping here
            # makes both the boot eager-load and the lazy-mount retry able to
            # cleanly re-attempt instead of serving a broken half-app.
            # pop() drops the canonical entry *and* any aliases targeting it, so
            # no dangling alias survives to resolve to a half-dead instance.
            self.instances.pop(app_id, None)
            raise RuntimeError(f"Failed to load app '{app_id}': {e}") from e

    def log_load_failure(self, app_id: str, exc: Exception, *, source: str, phase: str) -> None:
        """Persist an app load/setup failure to syslog with its traceback.

        Both load entry points — the CLI boot eager-load and the web lazy-mount
        — funnel failures here. A skipped app silently zeroes out every
        capability it provides (e.g. viz → `artifact`), so the failure must
        survive the session (syslog → /system + the feed), not just print to a
        console that scrolls away. Call from inside the `except` block so
        ``format_exc()`` captures the live exception.
        """
        import traceback

        self.kernel.syslog.error(
            source,
            f"{phase} failed for '{app_id}': {exc}",
            data={"app_id": app_id, "traceback": traceback.format_exc()},
        )

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

    def get_verbs(self):
        """Aggregate every app's ``[[provides.verbs]]`` into a VerbRegistry.

        Iterates ALL discovered manifests (not just loaded instances) — unlike
        ``get_contributions`` — because the autopilot eligibility floor is
        computed at boot when most apps are still lazy. Method-existence can't
        be checked here (no instance); that runs at instance-creation via
        ``_validate_app_verbs``. Manifest-static rejects (bad verb shape,
        cross-app declaration, unknown surface/eligibility) are warned + skipped.

        Returns a ``VerbRegistry`` (always — empty if nothing declares verbs).
        """
        from emptyos.sdk.verb_registry import VerbRegistry, parse_verb_entry

        entries = []
        for app_id, manifest in self.manifests.items():
            raw_verbs = manifest.provides.get("verbs")
            if not raw_verbs:
                continue
            if isinstance(raw_verbs, dict):
                raw_verbs = [raw_verbs]
            if not isinstance(raw_verbs, list):
                continue
            allowed = {app_id, *(manifest.aliases or [])}
            for rv in raw_verbs:
                entry, err = parse_verb_entry(
                    rv if isinstance(rv, dict) else {}, app_id, allowed_app_halves=allowed)
                if err is not None:
                    try:
                        self.kernel.syslog.warn("verb_registry", f"{app_id}: {err}")
                    except Exception:
                        pass
                    continue
                entries.append(entry)
        return VerbRegistry(entries)

    def _validate_app_verbs(self, app_id: str, manifest: AppManifest, instance) -> None:
        """Warn (never raise) if a declared verb's method doesn't exist on the
        live instance. This is the drift-killer: a renamed method screams here
        the first time the app loads instead of silently breaking a grant.
        """
        raw_verbs = manifest.provides.get("verbs")
        if not raw_verbs:
            return
        try:
            from emptyos.sdk.verb_registry import parse_verb_entry

            if isinstance(raw_verbs, dict):
                raw_verbs = [raw_verbs]
            if not isinstance(raw_verbs, list):
                return
            allowed = {app_id, *(manifest.aliases or [])}
            for rv in raw_verbs:
                entry, err = parse_verb_entry(
                    rv if isinstance(rv, dict) else {}, app_id, allowed_app_halves=allowed)
                if err is not None or entry is None:
                    continue  # static error already warned in get_verbs()
                for m in entry.dispatch_methods():
                    if not callable(getattr(instance, m, None)):
                        self.kernel.syslog.warn(
                            "verb_registry",
                            f"{app_id}: verb {entry.verb!r} declares method {m!r} "
                            f"which is not a callable on the app instance",
                        )
        except Exception:
            pass  # validation must never break app load

    def get_contributions(self, target: str, slot: str) -> list[dict]:
        """Gather `[[contributes.<target>.<slot>]]` entries from loaded apps.

        Each returned dict carries the manifest entry plus `_app_id` (the contributor).
        Used by extension-point hosts (hub slots, addon slots, future targets) to
        enumerate who wants to register into a named slot without importing them.

        Filters to apps that are actually loaded (present in `self.instances`).
        A discovered-but-not-loaded app can't service a contribution — rendering
        its tile/intent/panel just invites a 404 or a fail-soft dispatch. Hosts
        that need raw manifest data can read `self.manifests` directly.
        """
        result: list[dict] = []
        for app_id, manifest in self.manifests.items():
            if app_id not in self.instances:
                continue
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

        enabled: set[str] | None = None
        for dep_app in requires.get("apps", []):
            if dep_app not in self.manifests:
                missing.append(f"app:{dep_app}")
                continue
            if enabled is None:
                try:
                    enabled = self.enabled_ids()
                except Exception:
                    enabled = set(self.manifests.keys())
            if dep_app not in enabled:
                missing.append(f"app:{dep_app}")

        if missing:
            self.kernel.syslog.warn("app_loader", f"'{app_id}' unmet deps: {', '.join(missing)}")
            # Missing infrastructure is a growth signal, not just a boot
            # warning. Keep it in the shared demand log so Growth Agent and
            # future classifiers can prioritize recurring soil requests.
            try:
                from emptyos.sdk import demand_log

                demand_log.append(
                    self.kernel.config.data_dir,
                    {
                        "app": app_id,
                        "kind": "unmet_dependency",
                        "query": app_id,
                        "result": "missing",
                        "missing": missing,
                    },
                )
            except Exception:
                pass
