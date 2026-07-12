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

    async def connect(self):
        """Called on startup. Establish connection to external service.

        Override this to set up HTTP sessions, validate connectivity, etc.
        """

    async def disconnect(self):
        """Called on shutdown. Clean up connections."""

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
        """Get a plugin config value from emptyos.toml [plugins.<id>] section."""
        return self._config.get(key, default)

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
