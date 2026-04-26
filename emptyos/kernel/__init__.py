"""EmptyOS Kernel — config, app loader, plugin loader, capabilities, events, runtime."""

import logging

from emptyos.kernel.config import Config

logger = logging.getLogger("kernel")
from emptyos.kernel.service_registry import ServiceRegistry
from emptyos.kernel.event_bus import EventBus
from emptyos.kernel.app_loader import AppLoader
from emptyos.kernel.plugin_loader import PluginLoader
from emptyos.kernel.engine_loader import EngineLoader
from emptyos.kernel.syslog import SystemLog
from emptyos.kernel.agents import AgentResolver

__all__ = ["Config", "ServiceRegistry", "EventBus", "AppLoader", "PluginLoader", "EngineLoader", "AgentResolver", "Kernel"]


class Kernel:
    """The EmptyOS kernel. Wires everything together."""

    def __init__(self, config_path: str = "emptyos.toml"):
        self.config = Config(config_path)
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
        effective_policy = saved_policy if saved_policy in ("ask", "always", "never") else self.config.cloud_consent
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

    async def start(self):
        """Boot the kernel: runtime services -> plugins -> apps."""
        if self._started:
            return

        # 1. Start platform runtime services
        from emptyos.runtime.vault_watcher import VaultWatcher
        from emptyos.runtime.vault_index import VaultIndex
        from emptyos.runtime.scheduler import Scheduler
        from emptyos.runtime.realtime import RealtimeManager
        from emptyos.kernel.workers import WorkerPool

        self.vault_watcher = VaultWatcher(self)
        self.vault_index = VaultIndex(self)
        self.scheduler = Scheduler(self)
        self.realtime = RealtimeManager(self)
        self.worker_pool = WorkerPool(self, max_workers=int(
            self.config.get("workers.max_workers", 1) or 1
        ))

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
        self.apps.discover()
        for app_id in self.config.get("apps.autostart", []):
            if app_id in self.apps.manifests:
                await self.apps.load(app_id)
                await self.apps.start(app_id)

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
        if hasattr(self, 'vault_index') and self.vault_index:
            self.vault_index.stop()

        await self.events.emit("kernel:stopped", {}, source="kernel")
        self._started = False
