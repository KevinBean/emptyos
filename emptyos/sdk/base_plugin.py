"""BasePlugin — the class all EmptyOS plugins inherit from.

Plugins are "device drivers" that connect EmptyOS to external services
(ComfyUI, Telegram, Voice API, etc.) and register into the ServiceRegistry.
Apps access plugins via self.require("service_name") or self.service("service_name").
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from emptyos.kernel import Kernel


class BasePlugin:
    """Base class for EmptyOS plugins.

    Subclass this and implement connect(). The plugin will be
    auto-discovered from the plugins/ directory and registered
    into the ServiceRegistry during kernel boot.
    """

    name: str = "base"

    def __init__(self, kernel: Kernel, manifest: dict):
        self.kernel = kernel
        self.manifest = manifest
        self._config: dict[str, Any] = {}
        # Strong refs to detached tasks — asyncio only weak-refs a running one.
        self._bg_tasks: set = set()

    def spawn_background(self, coro, *, label: str = ""):
        """Run a coroutine detached from `connect()`, holding a strong reference.

        The plugin twin of `BaseApp.spawn_background`, sharing one
        implementation (`emptyos.sdk.background`). Plugins reach for this in the
        same place apps do — an `auto_start()` warm-up that must not block boot.
        A bare `asyncio.create_task` there can be garbage-collected mid-flight,
        because asyncio keeps only a weak reference to a running task, and the
        failure is silent.
        """
        from emptyos.sdk.background import spawn_tracked

        def _log(e: BaseException) -> None:
            try:
                self.kernel.syslog.warn(
                    "plugin_loader",
                    f"background task failed in plugin '{self.name}'"
                    f"{(' (' + label + ')') if label else ''}: {e}",
                )
            except Exception:
                pass

        return spawn_tracked(coro, tasks=self._bg_tasks, on_error=_log)

    async def connect(self):
        """Called on startup. Establish connection to external service.

        Override this to set up HTTP sessions, validate connectivity, etc.
        """

    async def disconnect(self):
        """Called on shutdown. Clean up connections.

        Subclasses that override this should call ``await super().disconnect()``
        so outstanding background tasks are cancelled rather than left running
        against a closed session.
        """
        from emptyos.sdk.background import cancel_tracked

        cancel_tracked(self._bg_tasks)

    async def available(self) -> bool:
        """Is the external service reachable right now?

        Should be fast (cached/debounced). Override for real checks.
        """
        return False

    async def health_check(self) -> bool:
        """Detailed health check. Can be slow (HTTP probe, etc.).

        Called by ServiceRegistry.health_check(). Defaults to available().
        """
        return await self.available()

    def config(self, key: str, default: Any = None) -> Any:
        """Get a plugin config value from emptyos.toml [plugins.<id>] section.

        Dot-path aware. TOML parses an unquoted dotted key into *nested* tables
        — ``feature.x.enabled = true`` becomes
        ``{"feature": {"x": {"enabled": True}}}`` — so a flat lookup of
        ``"feature.x.enabled"`` misses and the caller silently gets its default.
        That shipped ``[plugins.comfyui] feature.model-residency.enabled``
        reading ``true`` in the config file and ``False`` in the code
        (2026-07-27): the flag was set, and dead.

        **One flag, not two.** All of ``plugins/`` holds three
        ``self.config("feature.`` sites; the other two are
        ``feature.gpu-arbiter.enabled`` (which is ``false`` in the config, so it
        read False for the wrong reason but the right answer) and
        ``feature.runtime-compatibility.enabled`` (absent from the config and
        defaulting True). A regression pin whose stated motivation does not
        match the shipped config misleads the next reader, so the count is
        exact deliberately.

        **This fix activates a live flag.** Landing it flips
        ``model-residency`` from dead to ON, at a
        ``residency_min_vram_gb`` that sits on this card's measured headroom —
        a behaviour change riding along inside a bug fix. Soak it as its own
        decision rather than assuming the flag's author meant it to be on today.

        Matching ``BaseApp.app_config`` is the intent, not the achieved state:
        ``Config.get`` does pure dotted traversal *with* an ``EOS_*`` env
        override, while this tries the flat key first (so a quoted dotted key
        works here and not there) and has no env override at all.

        The flat lookup is tried first, so a quoted key (which TOML keeps
        literal) and every existing non-dotted key behave exactly as before.
        """
        if key in self._config:
            return self._config[key]
        if "." not in key:
            return default
        node: Any = self._config
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    @staticmethod
    def bearer_headers(token: str | None) -> dict[str, str]:
        """Build an `Authorization: Bearer <token>` header dict, or empty dict
        when token is falsy. Plugins gating an HTTP-RPC over a shared secret
        (Blender bridge, voice-api, …) compose this with their own token-source
        logic — token sourcing differs per service (file, env, config), but the
        header shape doesn't."""
        return {"Authorization": f"Bearer {token}"} if token else {}

    def __repr__(self) -> str:
        return f"<Plugin:{self.name}>"
