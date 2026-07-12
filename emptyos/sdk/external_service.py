"""Base class for apps that wrap a public external HTTP service.

Covers the shared shape of `apps/geocode/` (Nominatim) and `apps/routing/` (OSRM):

- `base_url` + `user_agent` configurable via `[apps.<id>]` in `emptyos.toml`
- `_throttle()` — async lock + min-interval gate so repeated calls respect the
  public service's rate policy (Nominatim ≤1 req/s, OSRM ≤1 req/s-ish)
- `_status()` — public-mode gate. When `network.mode = "public"` *and* the app
  is still using its demo URL, returns `{enabled: False, reason: ...}` so
  handlers can short-circuit and UIs can hide affordances. Self-hosters pointing
  `base_url` at their own endpoint stay enabled in all network modes.

Subclass:

    class GeocodeApp(ExternalServiceBase):
        DEMO_BASE = "https://nominatim.openstreetmap.org"
        SERVICE_LABEL = "Geocoding via the OSM Nominatim demo"
        MIN_INTERVAL_S = 1.1

        def __init__(self, kernel, manifest):
            super().__init__(kernel, manifest)
            self._cache: dict = {}
"""

from __future__ import annotations

import asyncio
import time

from .base_app import BaseApp

DEFAULT_USER_AGENT = "EmptyOS/1.0 (mind-companion)"


class ExternalServiceBase(BaseApp):
    # Subclasses override these
    DEMO_BASE: str = ""
    SERVICE_LABEL: str = "External service"
    MIN_INTERVAL_S: float = 1.0
    DEFAULT_USER_AGENT: str = DEFAULT_USER_AGENT

    def __init__(self, kernel, manifest):
        super().__init__(kernel, manifest)
        self._ext_lock = asyncio.Lock()
        self._ext_last_call = 0.0

    def _user_agent(self) -> str:
        return self.app_config("user_agent", self.DEFAULT_USER_AGENT)

    def _base_url(self) -> str:
        return self.app_config("base_url", self.DEMO_BASE).rstrip("/")

    async def _throttle(self) -> None:
        async with self._ext_lock:
            dt = time.monotonic() - self._ext_last_call
            if dt < self.MIN_INTERVAL_S:
                await asyncio.sleep(self.MIN_INTERVAL_S - dt)
            self._ext_last_call = time.monotonic()

    def _status(self) -> dict:
        mode = self.kernel.config.network_mode
        using_demo = self._base_url().rstrip("/") == self.DEMO_BASE.rstrip("/")
        if mode == "public" and using_demo:
            return {
                "enabled": False,
                "mode": mode,
                "using_demo": True,
                "reason": (
                    f"{self.SERVICE_LABEL} is disabled in public mode — "
                    f"self-host or set [apps.{self.manifest.id}] base_url."
                ),
            }
        return {"enabled": True, "mode": mode, "using_demo": using_demo}
