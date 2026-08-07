"""EmptyOS Kernel — config, app loader, plugin loader, capabilities, events, runtime."""

import asyncio
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
        self._housekeeping_task: asyncio.Task | None = None
        self.jobs: dict[str, dict] = {}  # job_id -> {phase, detail, pct, started, ...}
        self.syslog = SystemLog(self.config.data_dir / "syslog.db")
        self.events.set_syslog(self.syslog)
        # Prompt overrides (data/prompts/overrides.json) resolve against this
        # data dir; unconfigured resolution falls back to code defaults.
        from emptyos.sdk import prompt_registry

        prompt_registry.configure(self.config.data_dir)
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
            # Defense-in-depth: turn on the response scrubber by default so
            # any personal-pattern leak gets masked at the HTTP boundary.
            # Operators can still explicitly disable via `presentation.enabled =
            # false` if they have a reason — only auto-enable when unset.
            if self.settings.get("presentation.enabled") is None:
                self.settings.set("presentation.enabled", True)
        self.capabilities.set_consent_manager(self.cloud_consent)

        # Tool consent manager — permission gate for agent tool calls
        from emptyos.capabilities.tool_consent import ToolConsentManager

        saved_tool_policy = self.settings.get("agent.tool_policy")
        tool_policy = saved_tool_policy if saved_tool_policy in ("ask", "auto", "deny") else "ask"
        # How long a "approve for this session" grant lasts. None -> the
        # manager's 1h default; a session grant with no expiry is
        # indistinguishable from "always" on a long-lived daemon.
        try:
            saved_ttl = self.settings.get("agent.tool_session_ttl_s")
            session_ttl = float(saved_ttl) if saved_ttl not in (None, "") else None
        except (TypeError, ValueError):
            session_ttl = None
        self.tool_consent = ToolConsentManager(
            policy=tool_policy,
            events=self.events,
            session_ttl_s=session_ttl,
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

    def validate_verb_args(self, verb: str, args: dict) -> tuple[bool, str]:
        """Gate 1 — shape-check a model-originated verb call before dispatch.

        Returns ``(True, "")`` unless ``[verbs] arg_gate`` is on AND ``verb``
        declares a non-empty ``[[provides.verbs]] args`` schema that ``args``
        violates. Off (the dark default) it is a no-op, so every dispatch path
        behaves byte-identically.

        This is the platform-wide half of the two-gate discipline: the declared
        ``args`` schema has been prompt decoration on every surface except voice
        (see .claude/rules/verb-registry.md — "for the prompt + light
        validation, not strict typing"). Turning the flag on makes the same
        declaration a contract on the agent tool loop, both MCP bridges, and the
        rooms ``[DO:]`` paths, using the one shape checker that already existed
        (``emptyos.sdk.intents.validate_args``).

        Scope, deliberately narrow — the gate only ever tightens a verb that
        declared a schema:

        * a verb with no registry entry passes (nothing was promised);
        * a verb with ``args = {}`` passes (undeclared, not "accepts nothing");
        * ordinary app-to-app ``call_app`` never reaches here — the gate belongs
          around the *model's* call, not every function call.

        Recomputed fresh each call and never cached, matching
        ``autopilot_eligible_set`` — a manifest edit takes effect immediately.
        Fails **open** on any internal error: a broken registry must not wedge
        every action path, and the pre-existing ``TypeError`` handling at each
        dispatch site is still there underneath as the backstop.
        """
        try:
            flag = self.config.get("verbs.arg_gate", False)
            if not (
                flag is True
                or (isinstance(flag, str) and flag.strip().lower() in ("true", "1", "yes", "on"))
            ):
                return True, ""
            entry = self.apps.get_verbs().get(verb)
            schema = (getattr(entry, "args", None) or {}) if entry else {}
            if not schema:
                return True, ""
            from emptyos.sdk.intents import validate_args

            return validate_args(schema, args, allow_unknown=False)
        except Exception:
            return True, ""

    def autopilot_eligible_set(self) -> set[str] | None:
        """The effective autopilot eligibility floor, or ``None`` for legacy.

        Returns ``None`` when ``[autopilot] derive_floor_from_registry`` is off
        (the dark default) — every grant consumer then falls back to the
        persisted ``policy.json`` floor, preserving today's behaviour exactly.

        When the flag is on, computes the floor fresh from the unified verb
        registry (``[[provides.verbs]] eligibility="stable"``) plus operator
        deltas, via ``effective_eligible``. Recomputed each call — never cached
        — so a ``stable -> gated`` manifest flip revokes immediately. Callers
        pass the result as ``eligible=`` to ``autopilot.match`` / ``save_grant``.
        """
        try:
            flag = self.config.get("autopilot.derive_floor_from_registry", False)
            if not (flag is True or (isinstance(flag, str) and flag.strip().lower() in ("true", "1", "yes", "on"))):
                return None
            from emptyos.sdk.autopilot import effective_eligible

            derived = self.apps.get_verbs().eligible_verbs()
            return effective_eligible(self.config.data_dir, derived)
        except Exception:
            # Never let floor computation break an action path — fail closed to
            # the legacy floor (None) rather than raising into the gate.
            return None

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

        Removes everything under data/ except `data/secrets/` (OAuth/service
        credentials, e.g. the YouTube/Gmail connector tokens — BYOK keys are
        per-request contextvars and never touch disk), intentionally preserved
        so a redeploy doesn't force re-authorising every connected service.
        Runs before any SQLite handle is opened.
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

        # Record which process this is, so a restart can aim at it. Without
        # this the only way to stop the daemon was `taskkill /F /IM python.exe`
        # — every Python on the machine, including ComfyUI mid-render.
        from emptyos.sdk.daemon_pidfile import write_pidfile

        write_pidfile(self.config.data_dir, port=self.config.port)

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
        await self.vault_index.start()  # scan vault, subscribe to vault:changed
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

        await self._tailscale_boot_check()

        # 5. Retention housekeeping — events/syslog/run-folders had no prune,
        # so every store grew monotonically for the life of the daemon.
        self._housekeeping_task = asyncio.create_task(self._housekeeping_loop())

        self._started = True
        await self.events.emit("kernel:started", {}, source="kernel")

    # Delay before the first sweep so it never competes with boot, then daily.
    HOUSEKEEPING_FIRST_DELAY_S = 300
    HOUSEKEEPING_INTERVAL_S = 24 * 60 * 60

    async def _housekeeping_loop(self):
        """Periodically prune unbounded stores. Never raises out of the task."""
        try:
            await asyncio.sleep(self.HOUSEKEEPING_FIRST_DELAY_S)
            while True:
                await self._run_housekeeping()
                await asyncio.sleep(self.HOUSEKEEPING_INTERVAL_S)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.syslog.error("housekeeping", f"loop died: {e}")

    async def _run_housekeeping(self):
        """One retention sweep. Each store is independent — one failure must
        not skip the others. All DB/filesystem work runs off the event loop."""
        events_days = int(self.config.get("retention.events_days", 30) or 0)
        syslog_rows = int(self.config.get("retention.syslog_rows", 20000) or 0)
        runs_days = int(self.config.get("retention.runs_days", 90) or 0)
        counts = {"events": 0, "syslog": 0, "runs": 0}

        if events_days > 0:
            try:
                n = await asyncio.to_thread(self.events.prune, events_days)
                counts["events"] = n
                if n:
                    self.syslog.info("housekeeping", f"pruned {n} events older than {events_days}d")
            except Exception as e:
                self.syslog.error("housekeeping", f"event prune failed: {e}")

        if syslog_rows > 0:
            try:
                n = await asyncio.to_thread(self.syslog.trim, syslog_rows)
                counts["syslog"] = n
                if n:
                    self.syslog.info("housekeeping", f"trimmed {n} syslog rows (kept {syslog_rows})")
            except Exception as e:
                self.syslog.error("housekeeping", f"syslog trim failed: {e}")

        if runs_days > 0:
            try:
                n = await asyncio.to_thread(self._sweep_run_folders, runs_days)
                counts["runs"] = n
                if n:
                    self.syslog.info("housekeeping", f"swept {n} run folders older than {runs_days}d")
            except Exception as e:
                self.syslog.error("housekeeping", f"run-folder sweep failed: {e}")

        self.syslog.info(
            "housekeeping",
            "sweep complete: "
            f"events={counts['events']} syslog={counts['syslog']} runs={counts['runs']}",
        )

    def _sweep_run_folders(self, days: int) -> int:
        """Delete dated data/apps/*/runs/<run>/ dirs older than ``days``.

        Run stores also contain named workspaces (for example ``project`` and
        content hashes). Those are not retention snapshots and must never be
        inferred stale from mtime, so unrecognised names are skipped.
        """
        import re
        import shutil
        from datetime import UTC, datetime, timedelta

        root = self.config.data_dir / "apps"
        if not root.is_dir():
            return 0
        cutoff = datetime.now(UTC) - timedelta(days=days)
        removed = 0
        for runs_dir in root.glob("*/runs"):
            if not runs_dir.is_dir():
                continue
            for entry in runs_dir.iterdir():
                if not entry.is_dir():
                    continue  # never touch loose files (indexes, manifests)
                try:
                    compact = re.match(r"^(\d{8})T\d{6}(?:[-_].*)?$", entry.name)
                    dashed = re.match(r"^(\d{4}-\d{2}-\d{2})T\d{2}(?:-\d{2}){2}(?:[-_].*)?$", entry.name)
                    if compact:
                        run_date = datetime.strptime(compact.group(1), "%Y%m%d").replace(tzinfo=UTC)
                    elif dashed:
                        run_date = datetime.strptime(dashed.group(1), "%Y-%m-%d").replace(tzinfo=UTC)
                    else:
                        continue
                    if run_date >= cutoff:
                        continue
                    shutil.rmtree(entry)
                    removed += 1
                except Exception:
                    continue
        return removed

    async def _tailscale_boot_check(self):
        """Surface a boot suggestion when tailnet is up but network.mode = 'local'.

        Doesn't auto-flip mode (would silently change auth posture). Logs a
        one-line breadcrumb to syslog so the user sees it on boot + in
        `/api/syslog`, with the suggested config edit.
        """
        ts = self.services.get_optional("tailscale")
        if not ts:
            return
        try:
            if not await ts.available():
                return
            if self.config.network_mode != "local":
                return
            status = await ts.status()
            ip = status.get("self_ip", "")
            host = status.get("self_name", "")
            self.syslog.info(
                "tailscale",
                f"Tailnet detected ({host} @ {ip}). Daemon is in 'local' mode "
                f"and only reachable at 127.0.0.1. To access from other tailnet "
                f"devices, set network.mode = 'private' in emptyos.toml.",
            )
        except Exception as e:
            self.syslog.debug("tailscale", f"boot check skipped: {e}")

    async def stop(self):
        """Graceful shutdown: apps -> plugins -> runtime."""
        if not self._started:
            return
        # Announce the attempt before doing any of it. `kernel:stopped` fires at
        # the very end, so a caller that gives up early — the tray's
        # `fut.result(timeout=5)`, which 215 apps cannot finish inside — then
        # `os._exit`s leaves no trace at all, and a deliberate shutdown becomes
        # indistinguishable from an external kill. That ambiguity is what made
        # 2026-07-30's daemon deaths undiagnosable.
        await self.events.emit("kernel:stopping", {}, source="kernel")

        # Drop the PID record next: from here on this process is on its way
        # out, and a stale record is what makes a killer aim at the wrong tree.
        from emptyos.sdk.daemon_pidfile import clear_pidfile

        clear_pidfile(self.config.data_dir)
        if self._housekeeping_task:
            self._housekeeping_task.cancel()
            try:
                await self._housekeeping_task
            except asyncio.CancelledError:
                pass  # expected — we just cancelled it
            except Exception:
                pass  # a dying housekeeper must not block shutdown
            self._housekeeping_task = None
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
