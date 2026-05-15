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
from pathlib import Path

import aiohttp

from emptyos.sdk import BasePlugin
from emptyos.sdk.daemon_supervisor import spawn_emptyos_daemon, terminate_daemon


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

        try:
            self._proc = spawn_emptyos_daemon(
                config_path=cfg,
                cwd=cfg.parent.parent,  # project root
                extra_env={"EOS_DEMO_INSTANCE": "1"},
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

    async def stop(self) -> dict:
        """Terminate the sidecar daemon. Only kills the subprocess this
        plugin spawned (self._proc) — never an arbitrary python.exe. Safe
        per .claude/rules/daemon-handling.md because we own the handle."""
        if self._is_inner_daemon():
            return {"ok": False, "reason": "inside_demo_daemon"}
        proc = self._proc
        if proc is None:
            # Plugin didn't spawn this one (already running when we booted, or
            # spawned by a prior daemon process). Refuse to kill an unowned
            # process; operator action required.
            if await self.available():
                return {"ok": False, "reason": "running_but_unowned",
                        "hint": "stop the :9001 daemon manually"}
            return {"ok": True, "already_stopped": True}
        result = await terminate_daemon(proc, probe=self.available)
        # Match original semantics: only release the Popen handle on
        # successful terminate. On failure the proc may still be alive
        # and a future stop()/restart() needs the handle to retry.
        if result.get("ok"):
            self._proc = None
        return result

    async def restart(self) -> dict:
        """Stop + start the sidecar daemon. Used by the dogfood-agent
        fix-agent lane between code edits and verify-runs so the sandbox
        always loads the patched code. Returns a structured dict so the
        caller can branch on failures."""
        if self._is_inner_daemon():
            return {"ok": False, "reason": "inside_demo_daemon"}
        if not self._enabled():
            return {"ok": False, "reason": "disabled_in_config"}
        stop = await self.stop()
        if not stop.get("ok") and not stop.get("already_stopped"):
            return {"ok": False, "stage": "stop", **stop}
        ok = await self.auto_start()
        return {"ok": bool(ok), "stage": "start", "host": self._host()}
