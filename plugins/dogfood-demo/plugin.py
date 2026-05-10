"""Dogfood Demo — sidecar EmptyOS daemon on :9001 with sample vault.

The personal daemon launches this as a service the same way it launches
ComfyUI / voice-api / Ollama. Apps consume it via plain config (e.g.
`[apps.ppt] embed_base = "http://localhost:9001"`) — no hard coupling.

Recursion is avoided by:
  1. The inner daemon's `emptyos.toml` setting `[plugins.dogfood-demo] enabled = false`
  2. Belt-and-braces check at runtime: if our own kernel.config.demo_enabled is
     true, we are *inside* the demo daemon and refuse to spawn another.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import aiohttp

from emptyos.sdk import BasePlugin


class DogfoodDemoPlugin(BasePlugin):
    name = "dogfood-demo"

    def __init__(self, kernel, manifest):
        super().__init__(kernel, manifest)
        self._session: aiohttp.ClientSession | None = None
        self._proc: subprocess.Popen | None = None

    # ── Config helpers ──────────────────────────────────────────────

    def _port(self) -> int:
        try:
            return int(self.config("port", 9001))
        except (TypeError, ValueError):
            return 9001

    def _host(self) -> str:
        return self.config("host", f"http://localhost:{self._port()}")

    def _config_path(self) -> Path:
        # Inner daemon's config file. Default sits at ./dogfood/emptyos.toml
        # under the project root — the existing dogfood folder already has
        # an example template + vault layout.
        raw = self.config("config_path", "")
        if raw:
            return Path(raw)
        root = self.kernel.config.path.parent if hasattr(self.kernel.config, "path") else Path.cwd()
        return root / "dogfood" / "emptyos.toml"

    def _vault_path(self) -> Path:
        raw = self.config("vault_path", "")
        if raw:
            return Path(raw)
        root = self.kernel.config.path.parent if hasattr(self.kernel.config, "path") else Path.cwd()
        return root / "dogfood" / "vault"

    def _enabled(self) -> bool:
        return bool(self.config("enabled", True))

    def _is_inner_daemon(self) -> bool:
        # If we're loaded inside the dogfood daemon itself, refuse to spawn another.
        # Three independent signals — any one is enough:
        if os.environ.get("EOS_DEMO_INSTANCE") == "1":
            return True
        if getattr(self.kernel.config, "demo_enabled", False):
            return True
        # Belt: if our own daemon is listening on the dogfood port, we ARE it.
        try:
            own_port = int(self.kernel.config.get("network.port", 9000))
            if own_port == self._port():
                return True
        except Exception:
            pass
        return False

    # ── Service contract ────────────────────────────────────────────

    async def connect(self):
        self._session = aiohttp.ClientSession()
        if self._is_inner_daemon():
            print("[dogfood-demo] Skipping — running inside demo daemon (recursion guard)")
            return
        if not self._enabled():
            print("[dogfood-demo] Disabled in config (set [plugins.dogfood-demo] enabled = true)")
            return
        if await self.available():
            print(f"[dogfood-demo] Already running at {self._host()}")
            return
        autostart = bool(self.config("autostart", True))
        if autostart:
            asyncio.create_task(self.auto_start())
        else:
            print(f"[dogfood-demo] Not running at {self._host()} — run `eos service start dogfood-demo` to launch")

    async def disconnect(self):
        if self._session:
            try:
                await self._session.close()
            except Exception:
                pass
        # Don't kill the child process — the user's task manager owns it once spawned.
        # Mirrors how comfyui plugin behaves on shutdown.

    async def available(self) -> bool:
        if not self._session:
            return False
        try:
            async with self._session.get(
                f"{self._host()}/api/demo/status",
                timeout=aiohttp.ClientTimeout(total=2),
            ) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def health(self) -> dict:
        if self._is_inner_daemon():
            return {"available": False, "reason": "Recursion guard: inside demo daemon"}
        if not self._enabled():
            return {"available": False, "reason": "Disabled in config"}
        if await self.available():
            return {"available": True, "reason": None, "recovery": None}
        return {
            "available": False,
            "reason": f"Sidecar daemon unreachable at {self._host()}",
            "recovery": {
                "kind": "service",
                "id": "dogfood-demo",
                "url": self._host(),
                "hint": (
                    "First run: copy dogfood/emptyos.toml.example to dogfood/emptyos.toml, "
                    "ensure dogfood/vault/ has at least one note, "
                    "then restart the personal daemon."
                ),
            },
        }

    # ── Launcher ────────────────────────────────────────────────────

    async def auto_start(self) -> bool:
        if self._is_inner_daemon():
            return False
        if await self.available():
            return True

        cfg = self._config_path()
        if not cfg.exists():
            print(
                f"[dogfood-demo] Config missing: {cfg}\n"
                f"  cp demo/emptyos.toml.example {cfg}  # then edit"
            )
            return False
        vault = self._vault_path()
        if not vault.exists() or not any(vault.iterdir()):
            print(
                f"[dogfood-demo] Vault missing or empty: {vault}\n"
                f"  python scripts/demo-setup.py --output {vault} --force"
            )
            return False

        # Use python.exe (NOT pythonw.exe — firewall-blocked per Kevin's memory).
        python_exe = sys.executable
        if python_exe.lower().endswith("pythonw.exe"):
            python_exe = python_exe[:-len("pythonw.exe")] + "python.exe"

        env = os.environ.copy()
        env["EOS_CONFIG"] = str(cfg)
        env["EOS_DEMO_INSTANCE"] = "1"

        # CREATE_NO_WINDOW on Windows; harmless flag on POSIX.
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._proc = subprocess.Popen(  # noqa: ASYNC220
                [python_exe, "-m", "emptyos", "start"],
                cwd=str(cfg.parent.parent),  # project root
                env=env,
                creationflags=flags,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print(f"[dogfood-demo] Spawning sidecar at {self._host()} (pid {self._proc.pid})…")
        except Exception as e:
            print(f"[dogfood-demo] Spawn failed: {e}")
            return False

        for _ in range(30):  # up to 60s
            await asyncio.sleep(2)
            if await self.available():
                print(f"[dogfood-demo] Ready at {self._host()}")
                return True
        print(f"[dogfood-demo] Timed out waiting for {self._host()} — check {cfg.parent}/data-demo/eos-stderr.log")
        return False
