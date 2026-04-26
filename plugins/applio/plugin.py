"""Applio plugin — AI voice conversion via external Applio service.

Connects to Applio at localhost:6969. Provides voice conversion API.
"""

from __future__ import annotations

import aiohttp

from emptyos.sdk import BasePlugin


class ApplioPlugin(BasePlugin):
    name = "applio"

    def __init__(self, kernel, manifest):
        super().__init__(kernel, manifest)
        self._session = None

    def _host(self) -> str:
        return self.config("host", "http://localhost:6969")

    async def connect(self):
        self._session = aiohttp.ClientSession()
        is_up = await self.available()
        if is_up:
            print(f"[Applio] Connected to {self._host()}")
        else:
            print(f"[Applio] Not reachable at {self._host()}")

    async def disconnect(self):
        if self._session:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None

    async def available(self) -> bool:
        try:
            async with self._session.get(
                f"{self._host()}/health",
                timeout=aiohttp.ClientTimeout(total=2),
            ) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def models(self) -> list[str]:
        """List available voice models."""
        try:
            async with self._session.get(f"{self._host()}/models") as resp:
                data = await resp.json()
                return data if isinstance(data, list) else data.get("models", [])
        except Exception:
            return []

    async def convert(
        self,
        audio_path: str,
        model: str = "",
        pitch: int = 0,
    ) -> str:
        """Convert voice in audio file. Returns path to converted audio."""
        payload = {"audio_path": audio_path}
        if model:
            payload["model"] = model
        if pitch:
            payload["pitch"] = pitch

        async with self._session.post(
            f"{self._host()}/convert",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Applio convert failed: {resp.status}")
            data = await resp.json()
            return data.get("path", data.get("output_path", ""))
