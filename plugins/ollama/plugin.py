"""Ollama plugin — local LLM inference service."""

import aiohttp

from emptyos.sdk import BasePlugin


class OllamaPlugin(BasePlugin):
    name = "ollama"

    def _host(self) -> str:
        # Honor [plugins.ollama].host first (plugin-specific config), then
        # fall back to [capabilities.think.ollama].host (where users typically
        # configure the LLM endpoint), then default to localhost. This keeps
        # the plugin's connectivity warning accurate without forcing users to
        # duplicate the host setting in two TOML sections.
        host = self.config("host", "")
        if not host and hasattr(self, "kernel"):
            host = self.kernel.config.get("capabilities.think.ollama.host", "")
        return host or "http://localhost:11434"

    async def connect(self):
        """Verify Ollama is reachable."""
        if await self.available():
            print(f"[Ollama] Connected to {self._host()}")
        else:
            print(f"[Ollama] Warning: not reachable at {self._host()}")

    async def available(self) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._host()}/api/tags",
                    timeout=aiohttp.ClientTimeout(total=2),
                ) as resp:
                    return resp.status == 200
        except Exception:
            return False

    async def models(self) -> list[str]:
        """List available models."""
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self._host()}/api/tags") as resp:
                data = await resp.json()
                return [m["name"] for m in data.get("models", [])]

    async def generate(self, prompt: str, model: str = "", **kwargs) -> str:
        """Generate a completion."""
        model = model or self.config("model", "qwen3.5")
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self._host()}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False, **kwargs},
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return data.get("response", "")
