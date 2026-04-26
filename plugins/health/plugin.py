"""Health plugin — active watchdog: detect → diagnose → fix → report.

Not just monitoring — actively repairs what it can and reports what it can't.
Includes GPU VRAM monitoring for Ollama and ComfyUI.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from emptyos.sdk import BasePlugin

try:
    import aiohttp
    _HAS_AIOHTTP = True
except ImportError:
    _HAS_AIOHTTP = False

if TYPE_CHECKING:
    from emptyos.kernel import Kernel


class HealthPlugin(BasePlugin):
    name = "health"

    def __init__(self, kernel, manifest):
        super().__init__(kernel, manifest)
        self._last_check: dict = {}
        self._watchdog_task = None
        self._vault_file_count: int = -1
        self._vault_count_time: float = 0
        self._start_time: float = 0
        self._connector_failures: dict[str, int] = {}  # plugin_id -> consecutive failures
        self._known_problems: set[str] = set()

    async def connect(self):
        self._start_time = time.monotonic()
        interval = int(self.config("watchdog_interval", 60))
        self._watchdog_task = asyncio.create_task(self._watchdog_loop(interval))

    async def disconnect(self):
        if self._watchdog_task:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass

    async def available(self) -> bool:
        return True

    @property
    def uptime(self) -> float:
        return time.monotonic() - self._start_time

    # ──────────────────────────────────────────────
    # Watchdog loop: detect → diagnose → fix → report
    # ──────────────────────────────────────────────

    async def _watchdog_loop(self, interval: int):
        """Active watchdog — scans, fixes, reports."""
        try:
            while True:
                await asyncio.sleep(interval)
                problems = await self._detect()
                for problem in problems:
                    # Only report NEW problems (avoid event spam)
                    problem_key = f"{problem['type']}:{problem.get('id', '')}"
                    if problem_key in self._known_problems:
                        continue
                    self._known_problems.add(problem_key)
                    diagnosis = await self._diagnose(problem)
                    fixed = await self._fix(diagnosis)
                    await self._report(problem, diagnosis, fixed)
                    if fixed:
                        self._known_problems.discard(problem_key)
                # Clear problems that resolved
                current_keys = {f"{p['type']}:{p.get('id', '')}" for p in problems}
                self._known_problems &= current_keys

                await self.kernel.events.emit(
                    "health:heartbeat",
                    {"uptime": round(self.uptime), "problems": len(problems)},
                    source="health",
                )
        except asyncio.CancelledError:
            pass

    async def _detect(self) -> list[dict]:
        """Scan for problems across the system."""
        problems = []

        # Check connectors
        for plugin_id, instance in self.kernel.plugins.instances.items():
            if plugin_id == "health":
                continue
            try:
                avail = await instance.available()
                if not avail:
                    self._connector_failures[plugin_id] = self._connector_failures.get(plugin_id, 0) + 1
                    problems.append({
                        "type": "connector_down",
                        "id": plugin_id,
                        "failures": self._connector_failures[plugin_id],
                    })
                else:
                    self._connector_failures[plugin_id] = 0
            except Exception as e:
                self._connector_failures[plugin_id] = self._connector_failures.get(plugin_id, 0) + 1
                problems.append({
                    "type": "connector_error",
                    "id": plugin_id,
                    "error": str(e),
                    "failures": self._connector_failures[plugin_id],
                })

        # Check capabilities — any with zero available providers?
        for name, cap in self.kernel.capabilities.list().items():
            has_provider = False
            for p in cap.providers:
                try:
                    if await p.available():
                        has_provider = True
                        break
                except Exception:
                    pass
            if not has_provider and name in ("think", "read", "write", "search"):
                problems.append({"type": "capability_degraded", "id": name})

        # Check apps in error state
        from emptyos.kernel.app_loader import AppState
        for app_id, state in self.kernel.apps.states.items():
            if state == AppState.ERROR:
                problems.append({"type": "app_error", "id": app_id})

        return problems

    async def _diagnose(self, problem: dict) -> dict:
        """Figure out WHY something is wrong."""
        diagnosis = {**problem, "diagnosis": "unknown", "fixable": False}

        if problem["type"] == "connector_down":
            pid = problem["id"]
            instance = self.kernel.plugins.instances.get(pid)
            if instance:
                host = getattr(instance, '_host', lambda: 'unknown')()
                diagnosis["diagnosis"] = f"service unreachable at {host}"
                diagnosis["fixable"] = problem.get("failures", 0) >= 3  # try reconnect after 3 failures

        elif problem["type"] == "connector_error":
            diagnosis["diagnosis"] = f"exception: {problem.get('error', '')[:100]}"
            diagnosis["fixable"] = True

        elif problem["type"] == "capability_degraded":
            diagnosis["diagnosis"] = f"no available providers for {problem['id']}"
            diagnosis["fixable"] = False  # can't create providers on the fly

        elif problem["type"] == "app_error":
            diagnosis["diagnosis"] = f"app failed to load"
            diagnosis["fixable"] = True  # can try reload

        return diagnosis

    async def _fix(self, diagnosis: dict) -> bool:
        """Attempt to fix the problem. Returns True if fixed."""
        if not diagnosis.get("fixable"):
            return False

        if diagnosis["type"] in ("connector_down", "connector_error"):
            pid = diagnosis["id"]
            instance = self.kernel.plugins.instances.get(pid)
            if instance and hasattr(instance, "connect"):
                try:
                    if hasattr(instance, "disconnect"):
                        await instance.disconnect()
                    await instance.connect()
                    if await instance.available():
                        self._connector_failures[pid] = 0
                        return True
                except Exception:
                    pass

        elif diagnosis["type"] == "app_error":
            app_id = diagnosis["id"]
            try:
                await self.kernel.apps.load(app_id)
                return True
            except Exception:
                pass

        return False

    async def _report(self, problem: dict, diagnosis: dict, fixed: bool):
        """Report the problem — via event bus + notifications."""
        severity = "info" if fixed else "warning"
        action = "fixed" if fixed else "detected"

        await self.kernel.events.emit(
            f"health:problem:{action}",
            {
                "problem_type": problem["type"],
                "id": problem.get("id", ""),
                "diagnosis": diagnosis.get("diagnosis", ""),
                "fixed": fixed,
            },
            source="health",
        )

        # Notify user for unfixed problems
        if not fixed:
            notif = self.kernel.services.get_optional("notifications")
            if notif:
                await notif.send(
                    f"{problem['type']}: {problem.get('id', '')} — {diagnosis.get('diagnosis', '')}",
                    priority="warning",
                    source="health",
                )

    # ──────────────────────────────────────────────
    # Full check (for eos health / API)
    # ──────────────────────────────────────────────

    async def check(self) -> dict:
        """Run a full system health check."""
        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "uptime_seconds": round(self.uptime),
            "kernel": "ok",
            "vault": await self._check_vault(),
            "capabilities": await self._check_capabilities(),
            "connectors": await self._check_connectors(),
            "apps": self._check_apps(),
            "services": self._check_services(),
            "recent_problems": self._connector_failures,
        }
        self._last_check = result
        return result

    async def _check_vault(self) -> dict:
        vault_path = self.kernel.config.notes_path
        if not vault_path:
            return {"status": "not_configured"}
        if not vault_path.exists():
            return {"status": "missing", "path": str(vault_path)}
        now = time.monotonic()
        if now - self._vault_count_time > 300 or self._vault_file_count < 0:
            try:
                self._vault_file_count = await asyncio.to_thread(
                    lambda: sum(1 for _ in vault_path.rglob("*.md"))
                )
                self._vault_count_time = now
            except Exception:
                pass
        return {"status": "ok", "path": str(vault_path), "files": self._vault_file_count}

    async def _check_capabilities(self) -> dict:
        results = {}
        for name, cap in self.kernel.capabilities.list().items():
            providers = []
            for p in cap.providers:
                try:
                    avail = await p.available()
                except Exception:
                    avail = False
                providers.append({"name": p.name, "available": avail})
            any_available = any(p["available"] for p in providers)
            results[name] = {"status": "ok" if any_available else "degraded", "providers": providers}
        return results

    async def _check_connectors(self) -> dict:
        results = {}
        for plugin_id, instance in self.kernel.plugins.instances.items():
            if plugin_id == "health":
                continue
            try:
                avail = await instance.available()
                failures = self._connector_failures.get(plugin_id, 0)
                results[plugin_id] = {
                    "status": "ok" if avail else "unreachable",
                    "consecutive_failures": failures,
                }
            except Exception as e:
                results[plugin_id] = {"status": "error", "error": str(e)}
        return results

    def _check_apps(self) -> dict:
        from emptyos.kernel.app_loader import AppState
        return {
            app_id: {"status": self.kernel.apps.states.get(app_id, AppState.DISCOVERED).value, "name": m.name}
            for app_id, m in self.kernel.apps.manifests.items()
        }

    def _check_services(self) -> dict:
        return {
            e.name: {"status": e.status.value, "type": type(e.instance).__name__}
            for e in self.kernel.services.list()
        }

    # ──────────────────────────────────────────────
    # GPU VRAM monitoring (Ollama + ComfyUI)
    # ──────────────────────────────────────────────

    @property
    def _ollama_host(self) -> str:
        """Read Ollama host from its plugin config, not hardcoded."""
        ollama = self.kernel.services.get("ollama")
        if ollama and hasattr(ollama, "_host"):
            return ollama._host()
        return self.config("ollama_host", "http://localhost:11434")

    @property
    def _comfyui_host(self) -> str:
        """Read ComfyUI host from its plugin config, not hardcoded."""
        comfyui = self.kernel.services.get("comfyui")
        if comfyui and hasattr(comfyui, "_host"):
            return comfyui._host()
        return self.config("comfyui_host", "http://localhost:8188")

    async def _ollama_gpu(self) -> dict:
        """Get Ollama loaded models and VRAM usage."""
        if not _HAS_AIOHTTP:
            return {"running": False, "models": [], "total_vram_gb": 0}
        try:
            timeout = aiohttp.ClientTimeout(total=3)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{self._ollama_host}/api/ps") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        models = data.get("models", [])
                        total_vram = sum(m.get("size_vram", 0) for m in models)
                        return {
                            "running": True,
                            "models": [
                                {
                                    "name": m["name"],
                                    "size_gb": round(m.get("size", 0) / 1e9, 1),
                                    "vram_gb": round(m.get("size_vram", 0) / 1e9, 1),
                                    "gpu": m.get("size_vram", 0) > 0,
                                }
                                for m in models
                            ],
                            "total_vram_gb": round(total_vram / 1e9, 1),
                        }
                    return {"running": True, "models": [], "total_vram_gb": 0}
        except Exception:
            return {"running": False, "models": [], "total_vram_gb": 0}

    async def _comfyui_gpu(self) -> dict:
        """Get ComfyUI VRAM usage."""
        if not _HAS_AIOHTTP:
            return {"running": False, "vram_used_gb": 0, "vram_total_gb": 0}
        try:
            timeout = aiohttp.ClientTimeout(total=3)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{self._comfyui_host}/system_stats") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        devices = data.get("devices", [])
                        gpu = next((d for d in devices if d.get("type") == "cuda"), None)
                        if gpu:
                            used = gpu.get("vram_total", 0) - gpu.get("vram_free", 0)
                            return {
                                "running": True,
                                "vram_used_gb": round(used / 1e9, 1),
                                "vram_total_gb": round(gpu.get("vram_total", 0) / 1e9, 1),
                            }
                    return {"running": False, "vram_used_gb": 0, "vram_total_gb": 0}
        except Exception:
            return {"running": False, "vram_used_gb": 0, "vram_total_gb": 0}

    async def gpu_status(self) -> dict:
        """Combined GPU VRAM status from Ollama + ComfyUI."""
        ollama, comfyui = await asyncio.gather(self._ollama_gpu(), self._comfyui_gpu())
        total_active = ollama["total_vram_gb"] + comfyui.get("vram_used_gb", 0)
        # ComfyUI CUDA context uses ~2GB baseline even when idle
        baseline = 2.0 if comfyui["running"] else 0
        return {
            "ollama": ollama,
            "comfyui": comfyui,
            "total_active_vram_gb": round(total_active, 1),
            "gpu_busy": total_active > (0.5 + baseline),
        }

    async def gpu_free(self) -> dict:
        """Unload all GPU models from Ollama and ComfyUI."""
        results = {"ollama": {"ok": False}, "comfyui": {"ok": False}}

        if not _HAS_AIOHTTP:
            return results

        # Ollama: unload each loaded model
        try:
            ollama = await self._ollama_gpu()
            if ollama["running"]:
                timeout = aiohttp.ClientTimeout(total=10)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    for m in ollama["models"]:
                        if m.get("vram_gb", 0) > 0:
                            await session.post(
                                f"{self._ollama_host}/api/generate",
                                json={"model": m["name"], "keep_alive": 0},
                            )
                results["ollama"] = {"ok": True, "unloaded": len(ollama["models"])}
        except Exception as e:
            results["ollama"] = {"ok": False, "error": str(e)}

        # ComfyUI: free memory
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{self._comfyui_host}/free",
                    json={"unload_models": True, "free_memory": True},
                ) as resp:
                    results["comfyui"] = {"ok": resp.status == 200}
        except Exception as e:
            results["comfyui"] = {"ok": False, "error": str(e)}

        return results
