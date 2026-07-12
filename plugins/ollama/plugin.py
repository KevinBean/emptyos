"""Ollama plugin — local LLM inference service."""

import json

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

    # ── Model management (one-click serving, consumed by apps/.../model-serving) ──

    def _pull_store(self) -> dict:
        """Lazy per-model pull-progress map: {model -> {status, percent, done, error}}."""
        if not hasattr(self, "_pulls"):
            self._pulls: dict[str, dict] = {}
        return self._pulls

    def pull_status(self, model: str) -> dict:
        """Current progress for an in-flight/finished pull (poll target for the UI)."""
        return self._pull_store().get(
            model, {"status": "idle", "percent": 0, "done": False, "error": ""}
        )

    async def pull(self, model: str) -> dict:
        """Stream a model download from the Ollama registry, recording progress.

        Long-running: run as a background task (``asyncio.create_task``) and poll
        ``pull_status(model)``. Idempotent — a second call while a pull is active
        no-ops. Ollama streams NDJSON lines like
        ``{"status": "pulling …", "total": N, "completed": M}``.
        """
        store = self._pull_store()
        cur = store.get(model)
        if cur and not cur.get("done") and cur.get("status") not in ("", "idle", "error"):
            return cur  # already pulling
        store[model] = {"status": "starting", "percent": 0, "done": False, "error": ""}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._host()}/api/pull",
                    json={"model": model, "stream": True},
                    timeout=aiohttp.ClientTimeout(total=3600),
                ) as resp:
                    if resp.status != 200:
                        store[model] = {"status": "error", "percent": 0, "done": True,
                                        "error": f"HTTP {resp.status}"}
                        return store[model]
                    async for raw in resp.content:
                        line = raw.decode("utf-8", "ignore").strip()
                        if not line:
                            continue
                        try:
                            ev = json.loads(line)
                        except Exception:
                            continue
                        if ev.get("error"):
                            store[model] = {"status": "error", "percent": 0, "done": True,
                                            "error": str(ev["error"])}
                            return store[model]
                        total, done = ev.get("total") or 0, ev.get("completed") or 0
                        pct = int(done * 100 / total) if total else store[model].get("percent", 0)
                        store[model] = {"status": ev.get("status", "pulling"),
                                        "percent": pct, "done": False, "error": ""}
            store[model] = {"status": "success", "percent": 100, "done": True, "error": ""}
        except Exception as e:  # noqa: BLE001 — surface any transport failure to the UI
            store[model] = {"status": "error", "percent": 0, "done": True, "error": str(e)}
        return store[model]

    async def show(self, model: str) -> dict:
        """Model metadata (size in GB) via Ollama ``/api/show``; {} if unknown."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._host()}/api/show",
                    json={"model": model},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status != 200:
                        return {}
                    data = await resp.json()
                    size = (data.get("details") or {}).get("parameter_size") or ""
                    return {"size_gb": round((data.get("size") or 0) / 1e9, 2), "params": size}
        except Exception:
            return {}

    async def ps(self) -> list[dict]:
        """Currently loaded models (name + VRAM) via Ollama ``/api/ps``."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._host()}/api/ps", timeout=aiohttp.ClientTimeout(total=3)
                ) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
                    return [
                        {"name": m.get("name", ""),
                         "vram_gb": round((m.get("size_vram") or 0) / 1e9, 1)}
                        for m in data.get("models", [])
                    ]
        except Exception:
            return []
