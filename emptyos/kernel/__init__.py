"""EmptyOS Kernel — config, app loader, plugin loader, capabilities, events, runtime."""

import logging

from emptyos.kernel.config import Config

logger = logging.getLogger("kernel")
from emptyos.kernel.agents import AgentResolver
from emptyos.kernel.app_loader import AppLoader
from emptyos.kernel.engine_loader import EngineLoader
from emptyos.kernel.event_bus import EventBus
from emptyos.kernel.plugin_loader import PluginLoader
from emptyos.kernel.service_registry import ServiceRegistry
from emptyos.kernel.syslog import SystemLog

__all__ = [
    "Config",
    "ServiceRegistry",
    "EventBus",
    "AppLoader",
    "PluginLoader",
    "EngineLoader",
    "AgentResolver",
    "Kernel",
]


class Kernel:
    """The EmptyOS kernel. Wires everything together."""

    def __init__(self, config_path: str = "emptyos.toml"):
        self.config = Config(config_path)
        # Demo reset runs before any runtime db/file handle is opened.
        # On Linux unlink-while-open works, but Windows hosts (and any future
        # boot order changes) shouldn't have to worry about ordering.
        if self.config.demo_enabled and self.config.demo_reset_on_restart:
            self._demo_reset_state()
        self.services = ServiceRegistry()
        db_path = self.config.data_dir / "events.db"
        self.events = EventBus(db_path=db_path)
        self.plugins = PluginLoader(self)
        self.engines = EngineLoader(self)
        self.apps = AppLoader(self)
        self._started = False
        self.jobs: dict[str, dict] = {}  # job_id -> {phase, detail, pct, started, ...}
        self.syslog = SystemLog(self.config.data_dir / "syslog.db")
        self.events.set_syslog(self.syslog)
        self.agents = AgentResolver(self)

        # Platform runtime services (initialized lazily on start)
        self.vault_watcher = None
        self.scheduler = None
        self.realtime = None

        # Settings service — constructed before capabilities so think providers
        # can read `think.<name>.model` overrides at boot time.
        from emptyos.runtime.settings import SettingsService

        self.settings = SettingsService(self)

        # Build capabilities from config (with settings overlay for model overrides)
        from emptyos.capabilities.setup import build_capabilities

        self.capabilities = build_capabilities(self.config, settings=self.settings, kernel=self)

        # Cloud consent manager — gates cloud providers in the capability chain.
        # Resolution order: settings (user override, persisted) > emptyos.toml > default.
        from emptyos.capabilities.consent import CloudConsentManager

        saved_policy = self.settings.get("cloud.consent")
        effective_policy = (
            saved_policy
            if saved_policy in ("ask", "always", "never")
            else self.config.cloud_consent
        )
        self.cloud_consent = CloudConsentManager(
            policy=effective_policy,
            events=self.events,
            kernel=self,
        )
        # In demo mode, pre-approve cloud providers (users opt in via BYOK)
        if self.config.demo_enabled:
            self.cloud_consent.set_policy("always")
        self.capabilities.set_consent_manager(self.cloud_consent)

        # Tool consent manager — permission gate for agent tool calls
        from emptyos.capabilities.tool_consent import ToolConsentManager

        saved_tool_policy = self.settings.get("agent.tool_policy")
        tool_policy = saved_tool_policy if saved_tool_policy in ("ask", "auto", "deny") else "ask"
        self.tool_consent = ToolConsentManager(
            policy=tool_policy,
            events=self.events,
        )

        # Register kernel-level services
        self.services.register("config", self.config)
        self.services.register("events", self.events)
        self.services.register("cloud_consent", self.cloud_consent, tags=["system"])
        self.services.register("tool_consent", self.tool_consent, tags=["system"])
        self.services.register("settings", self.settings, tags=["system"])

        # Vault map — discovers app data locations in vault
        from emptyos.runtime.vault_map import VaultMap

        self.vault_map = VaultMap(self.config.notes_path)
        self.services.register("vault_map", self.vault_map, tags=["system"])

    def capability(self, name: str):
        """Get a capability by name."""
        return self.capabilities.get(name)

    def trim_jobs(self, max_age_seconds: int = 3600, max_finished: int = 200):
        """Evict finished jobs older than max_age or exceeding max_finished count."""
        if len(self.jobs) <= max_finished:
            return
        import time

        now = time.time()
        finished = sorted(
            [(k, v) for k, v in self.jobs.items() if v.get("finished")],
            key=lambda x: x[1]["finished"],
        )
        keep = 0
        for k, v in finished:
            if now - v["finished"] > max_age_seconds or len(finished) - keep > max_finished:
                del self.jobs[k]
            else:
                keep += 1

    def _demo_reset_state(self) -> None:
        """Wipe runtime state for a clean demo boot.

        Removes everything under data/ except `data/secrets/` (BYOK key cache,
        intentionally preserved across resets so a redeploy doesn't log every
        visitor out mid-session). Runs before any SQLite handle is opened.
        Fail-soft: on any error, log to stdout and continue — a stuck reset
        must never block the daemon from booting.
        """
        import shutil

        data_dir = self.config.data_dir
        if not data_dir.exists():
            return
        try:
            for entry in data_dir.iterdir():
                if entry.name == "secrets":
                    continue
                try:
                    if entry.is_dir() and not entry.is_symlink():
                        shutil.rmtree(entry, ignore_errors=True)
                    else:
                        entry.unlink(missing_ok=True)
                except OSError as e:
                    logger.warning("[demo-reset] could not remove %s: %s", entry, e)
        except OSError as e:
            logger.warning("[demo-reset] iterdir(%s) failed: %s", data_dir, e)
            return
        # Recreate the dirs the daemon expects to exist on boot
        for sub in ("apps", "billing", "syslog"):
            try:
                (data_dir / sub).mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        logger.info("[demo-reset] wiped %s (preserved secrets/)", data_dir)

    async def _demo_seed_apps(self) -> None:
        """Run each app's `demo/seed.py` `seed(app)` coroutine.

        Probes `<app.path>/demo/seed.py` for every running app — works for
        both `apps/<id>/` and `apps/personal/<id>/` since `manifest.path` is
        the app's discovered directory. Per-app failures are caught and
        logged to syslog; seeding is best-effort, not boot-critical.
        """
        from emptyos.kernel.module_import import load_module

        for app_id in list(self.apps.running):
            manifest = self.apps.manifests.get(app_id)
            if not manifest:
                continue
            seed_file = manifest.path / "demo" / "seed.py"
            if not seed_file.exists():
                continue
            instance = self.apps.instances.get(app_id)
            if instance is None:
                continue
            try:
                module = load_module(
                    seed_file,
                    f"eos_apps.{app_id}.demo_seed",
                    seed_file.parent,
                    None,  # no class — module-level seed()
                    self,
                    manifest,
                )
                seed_fn = getattr(module, "seed", None)
                if seed_fn is None:
                    continue
                result = seed_fn(instance)
                if hasattr(result, "__await__"):
                    await result
                self.syslog.info("kernel", f"demo seed: {app_id} OK")
            except Exception as e:
                self.syslog.warn("kernel", f"demo seed '{app_id}': {e}")

    async def start(self):
        """Boot the kernel: runtime services -> plugins -> apps."""
        if self._started:
            return

        # 1. Start platform runtime services
        from emptyos.kernel.workers import WorkerPool
        from emptyos.runtime.realtime import RealtimeManager
        from emptyos.runtime.scheduler import Scheduler
        from emptyos.runtime.vault_index import VaultIndex
        from emptyos.runtime.vault_watcher import VaultWatcher

        self.vault_watcher = VaultWatcher(self)
        self.vault_index = VaultIndex(self)
        self.scheduler = Scheduler(self)
        self.realtime = RealtimeManager(self)
        self.worker_pool = WorkerPool(
            self, max_workers=int(self.config.get("workers.max_workers", 1) or 1)
        )

        await self.vault_watcher.start()
        self.vault_index.start()  # scan vault, subscribe to vault:changed
        self.services.register("vault_index", self.vault_index, tags=["system"])
        await self.scheduler.start()
        await self.realtime.start()
        await self.worker_pool.start()
        self.services.register("workers", self.worker_pool, tags=["system"])

        # 2. Discover and load plugins (register as services)
        self.plugins.discover()
        await self.plugins.load_all()

        # 3. Discover and load engines (shared computation libraries)
        self.engines.discover()
        await self.engines.load_all()

        # 4. Discover and start apps (can now require() plugin services + engines)
        # Fail-soft: a broken app gets ERROR state and the boot continues with
        # the rest, mirroring plugin_loader. Without this, one bad app blocks
        # the daemon from binding :9000 entirely.
        self.apps.discover()
        # Calling enabled_ids() also seeds data/store/installed-apps.json on
        # first boot with every discovered manifest — preserves today's
        # behaviour on existing daemons (everything stays enabled until the
        # user prunes via the store). Demo mode bypasses the gate.
        enabled_apps = self.apps.enabled_ids()
        for app_id in self.config.get("apps.autostart", []):
            if app_id not in self.apps.manifests:
                continue
            if app_id not in enabled_apps:
                # Autostart entry for an uninstalled-or-disabled app — log + skip
                # rather than silently override the store. User pruned it; respect that.
                self.syslog.info("kernel", f"autostart '{app_id}' skipped (not enabled)")
                continue
            try:
                await self.apps.load(app_id)
                await self.apps.start(app_id)
            except Exception as e:
                # `e` already carries the "Failed to load app 'X': …"
                # prefix from app_loader.load — don't double it up.
                self.syslog.error("kernel", f"autostart '{app_id}': {e}")

        # 4b. Demo seed — populate fresh state with sample content per app.
        # Runs only when demo.enabled and demo.seed_on_boot are both set.
        # Each `apps/<id>/demo/seed.py` exporting an async `seed(app)` is
        # called with its app instance; failures are isolated and logged.
        if self.config.demo_enabled and self.config.demo_seed_on_boot:
            await self._demo_seed_apps()

        # 4. Vault map — auto-rescan on folder changes
        self.vault_map.load()
        _vm = self.vault_map
        _last_rescan = [0.0]

        async def _on_vault_change(event):
            import time

            change = event.data.get("change", "")
            path = event.data.get("path", "")
            # Rescan on folder-level changes (added/deleted dirs, or moves)
            if change in ("added", "deleted") and "/" in path:
                now = time.time()
                if now - _last_rescan[0] > 30:  # debounce: max once per 30s
                    _last_rescan[0] = now
                    changes = _vm.rescan()
                    if changes:
                        logger.info("[VaultMap] Auto-healed: %s", changes)

        self.events.on("vault:changed", _on_vault_change)

        self._started = True
        await self.events.emit("kernel:started", {}, source="kernel")

    async def stop(self):
        """Graceful shutdown: apps -> plugins -> runtime."""
        if not self._started:
            return
        for app_id in list(self.apps.running):
            await self.apps.stop(app_id)
        await self.engines.stop_all()
        await self.plugins.stop_all()

        # Stop runtime services
        if self.realtime:
            await self.realtime.stop()
        if self.scheduler:
            await self.scheduler.stop()
        if self.vault_watcher:
            await self.vault_watcher.stop()
        if hasattr(self, "vault_index") and self.vault_index:
            self.vault_index.stop()

        await self.events.emit("kernel:stopped", {}, source="kernel")
        self._started = False
