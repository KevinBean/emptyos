"""Pronounce plugin — auto-starts the pronounce-api service and registers the
local provider on the `pronounce` capability.

Mirrors `plugins/voice-api/plugin.py`. The plugin owns the subprocess; the
capability layer owns the provider chain.
"""

from __future__ import annotations

import aiohttp

from emptyos.capabilities.providers.pronounce_local import LocalPronounceProvider
from emptyos.sdk import BasePlugin


class PronouncePlugin(BasePlugin):
    name = "pronounce"

    def __init__(self, kernel, manifest):
        super().__init__(kernel, manifest)
        self._session: aiohttp.ClientSession | None = None
        self._embedded_proc = None
        self._health_cache: dict = {}
        self._health_ts: float = 0.0

    def _host(self) -> str:
        return self.config("host", "http://127.0.0.1:8603")

    def _token(self) -> str:
        import os as _os

        return (
            self.config("auth_token", "")
            or _os.environ.get("PRONOUNCE_API_TOKEN", "")
            or ""
        ).strip()

    def _auth_headers(self) -> dict:
        return self.bearer_headers(self._token())

    async def connect(self):
        self._session = aiohttp.ClientSession()

        if not await self._service_up():
            try:
                await self._start_embedded()
            except Exception as e:
                print(f"[pronounce] Could not start embedded server: {e}")

        if await self._service_up():
            print(f"[pronounce] Connected to {self._host()}")
            # Warmup loads the model now rather than on the first /score, and
            # on a machine with torch + transformers that is a ~1.2 GB model
            # download. The plugin ships in the public EnglishOS edition, so a
            # boot must never start that download unasked: opt in with
            # `[plugins.pronounce] warmup_on_boot = true`. Without it the
            # service still loads the model lazily on the first /score.
            if self.config("warmup_on_boot", False) is True:
                try:
                    await self._warmup()
                except Exception:
                    pass

        # The capability is registered in `emptyos/capabilities/setup.py` so
        # the chain exists at boot even when the plugin is absent. Here we
        # just append our local provider.
        if self.kernel.capabilities.has("pronounce"):
            pronounce = self.kernel.capabilities.get("pronounce")
            pronounce.add_provider(LocalPronounceProvider(self))

    async def _warmup(self):
        """POST /warmup to start the model load in the background."""
        try:
            async with self._session.post(
                f"{self._host()}/warmup",
                headers=self._auth_headers(),
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    print(f"[pronounce] Warmup started: {data.get('state', 'idle')}")
        except Exception as e:
            print(f"[pronounce] Warmup probe failed: {e}")

    async def _start_embedded(self):
        """Spawn the pronounce service as a subprocess."""
        import asyncio
        import os
        import sys
        from pathlib import Path
        from urllib.parse import urlparse

        server_path = (
            Path(__file__).parent.parent.parent / "services" / "pronounce" / "server.py"
        )
        if not server_path.exists():
            raise FileNotFoundError(f"Pronounce server not found: {server_path}")

        env = os.environ.copy()
        parsed = urlparse(self._host())
        if parsed.port:
            env["PRONOUNCE_API_PORT"] = str(parsed.port)
        if self.config("model_dir"):
            env["PRONOUNCE_MODEL_DIR"] = str(self.config("model_dir"))
        if self.config("model_id"):
            env["PRONOUNCE_MODEL_ID"] = str(self.config("model_id"))

        self._embedded_proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(server_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
        # The service boots fast; the model loads lazily on first /score call.
        # We just wait for the HTTP listener to come up.
        for _ in range(10):
            await asyncio.sleep(0.5)
            if await self._service_up():
                return
        raise RuntimeError("Embedded pronounce server didn't start in time")

    async def disconnect(self):
        if self._embedded_proc and self._embedded_proc.returncode is None:
            self._embedded_proc.terminate()
            self._embedded_proc = None
        if self._session:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None

    async def _service_up(self) -> bool:
        try:
            async with self._session.get(
                f"{self._host()}/health",
                timeout=aiohttp.ClientTimeout(total=2),
            ) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def health(self) -> dict:
        """Cached /health response. 5s TTL like voice-api."""
        import time

        now = time.monotonic()
        if self._health_cache and (now - self._health_ts) < 5.0:
            return self._health_cache
        try:
            async with self._session.get(
                f"{self._host()}/health",
                timeout=aiohttp.ClientTimeout(total=2),
            ) as resp:
                if resp.status != 200:
                    return {}
                data = await resp.json()
                self._health_cache = data
                self._health_ts = now
                return data
        except Exception:
            return {}

    async def score(self, payload: dict) -> dict:
        """POST /score on behalf of a provider. Returns the JSON payload."""
        async with self._session.post(
            f"{self._host()}/score",
            json=payload,
            headers=self._auth_headers(),
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"pronounce /score failed: {resp.status}")
            return await resp.json()

    async def align(self, payload: dict) -> dict:
        """POST /align on behalf of a provider."""
        async with self._session.post(
            f"{self._host()}/align",
            json=payload,
            headers=self._auth_headers(),
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"pronounce /align failed: {resp.status}")
            return await resp.json()
