"""BaseEngine — the class all EmptyOS engines inherit from.

Engines are shared computation libraries that live in-process.
Unlike plugins (which connect to external services via HTTP),
engines wrap importable packages (numpy, opendssdirect, etc.)
and expose domain functions to multiple apps.

Apps access engines via self.engine("engine_id").
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from emptyos.kernel import Kernel


class BaseEngine:
    """Base class for EmptyOS engines.

    Subclass this and implement init(). The engine will be
    auto-discovered from the engines/ directory and registered
    into the ServiceRegistry during kernel boot (after plugins,
    before apps).
    """

    name: str = "base"

    def __init__(self, kernel: Kernel, manifest: dict):
        self.kernel = kernel
        self.manifest = manifest

    async def init(self):
        """Called on startup. Check dependencies, warm caches.

        Override this to import optional packages and set up state.
        """

    async def shutdown(self):
        """Called on kernel stop. Clean up resources."""

    async def available(self) -> bool:
        """Are the engine's dependencies available?

        Override to check importability of required packages.
        """
        return True

    async def health_check(self) -> dict:
        """Return engine health status. Called by ServiceRegistry."""
        avail = await self.available()
        return {"status": "ok" if avail else "unavailable", "available": avail}

    def __repr__(self) -> str:
        return f"<Engine:{self.name}>"
