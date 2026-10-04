"""Tailscale plugin — read-only awareness of the local tailnet.

Shells out to `tailscale status --json` and exposes the parsed result. Never
runs `tailscale up/down/funnel` — Tailscale's own UI manages state; EmptyOS
just reads it. If you want write-side control, that's a different plugin
(opt-in, more invasive — see `.claude/rules/plugins.md` graduation notes).

Apps:
    ts = self.require("tailscale")
    if await ts.available():
        me = await ts.identity()        # {"login_name": "...", "node_name": "..."}
        peers = await ts.peers()        # [{name, ip, online, os, last_seen}, ...]
        bind = await ts.preferred_bind() # tailnet IP to bind a service to, "" if no tailnet
        if await ts.is_peer(request_ip):
            ...  # trusted source — different audit log label
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from typing import Any

from emptyos.sdk import BasePlugin
from emptyos.sdk.proc import run_command

logger = logging.getLogger("tailscale")

# Likely binary locations per platform. First hit wins.
_BINARY_CANDIDATES = [
    "tailscale",  # PATH (Linux, macOS Homebrew, Windows if added to PATH)
    r"C:\Program Files\Tailscale\tailscale.exe",
    r"C:\Program Files (x86)\Tailscale\tailscale.exe",
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
    "/usr/local/bin/tailscale",
    "/usr/bin/tailscale",
]


class TailscalePlugin(BasePlugin):
    name = "tailscale"

    def __init__(self, kernel, manifest):
        super().__init__(kernel, manifest)
        self._binary: str = ""
        self._cache: dict[str, Any] | None = None
        self._cache_at: float = 0.0
        self._cache_ttl: float = 30.0
        self._probe_lock = asyncio.Lock()

    async def connect(self):
        override = self.config("binary", "") or ""
        self._cache_ttl = float(self.config("cache_ttl_s", 30) or 30)
        self._binary = self._resolve_binary(override)
        if not self._binary:
            logger.info("Tailscale CLI not found — plugin will report unavailable")
            return
        # Warm cache once on boot so the first request is fast.
        status = await self._raw_status()
        if status.get("BackendState") == "Running":
            self_node = status.get("Self") or {}
            ip = (self_node.get("TailscaleIPs") or [""])[0]
            logger.info("Tailscale connected — node %s at %s", self_node.get("HostName", "?"), ip)
        else:
            logger.info("Tailscale CLI found but backend state: %s", status.get("BackendState", "?"))

    @staticmethod
    def _resolve_binary(override: str) -> str:
        if override and os.path.exists(override):
            return override
        for candidate in _BINARY_CANDIDATES:
            path = shutil.which(candidate) if not os.path.isabs(candidate) else (candidate if os.path.exists(candidate) else "")
            if path:
                return path
        return ""

    async def _raw_status(self, force: bool = False) -> dict[str, Any]:
        """Shell out to `tailscale status --json`, with TTL cache."""
        now = time.time()
        if not force and self._cache is not None and (now - self._cache_at) < self._cache_ttl:
            return self._cache
        async with self._probe_lock:
            # Double-check after acquiring lock — another caller may have refreshed.
            if not force and self._cache is not None and (time.time() - self._cache_at) < self._cache_ttl:
                return self._cache
            if not self._binary:
                self._cache = {"BackendState": "NoCLI"}
                self._cache_at = now
                return self._cache
            res = await run_command([self._binary, "status", "--json"], timeout=5.0)
            if not res.ok:
                logger.debug("tailscale status failed: %s", res.stderr)
                data = {"BackendState": "Error", "_error": res.stderr or "status failed"}
            else:
                try:
                    data = json.loads(res.stdout or "{}")
                except json.JSONDecodeError as e:
                    logger.debug("tailscale status parse failed: %s", e)
                    data = {"BackendState": "Error", "_error": str(e)}
            self._cache = data
            self._cache_at = time.time()
            return data

    async def available(self) -> bool:
        """True when the CLI is present AND backend is Running (logged in + connected)."""
        if not self._binary:
            return False
        data = await self._raw_status()
        return data.get("BackendState") == "Running"

    def binary_path(self) -> str:
        """Resolved `tailscale` binary path, or "" if not found.

        A harmless read (does not violate the read-only guarantee) so the
        sibling `tailscale-serve` write plugin can reuse binary resolution
        instead of duplicating `_BINARY_CANDIDATES`.
        """
        return self._binary

    # ── Public API ──────────────────────────────────────────────────

    async def status(self) -> dict[str, Any]:
        """Coarse summary — backend state + this node's IP + peer count."""
        data = await self._raw_status()
        self_node = data.get("Self") or {}
        peers = data.get("Peer") or {}
        return {
            "backend_state": data.get("BackendState", "?"),
            "self_ip": (self_node.get("TailscaleIPs") or [""])[0],
            "self_name": self_node.get("HostName", ""),
            "self_dns": self_node.get("DNSName", "").rstrip("."),
            "peer_count": len(peers),
            "online_peer_count": sum(1 for p in peers.values() if p.get("Online")),
            "tailnet": data.get("MagicDNSSuffix", "").rstrip("."),
        }

    async def preferred_bind(self) -> str:
        """This node's Tailscale IP, but only when the backend is Running.

        The address a service should bind to so it's reachable on the (encrypted,
        device-scoped) tailnet and nowhere else. Returns "" when there's no live
        tailnet, so callers can fall back to their own default (e.g. 0.0.0.0).
        """
        data = await self._raw_status()
        if data.get("BackendState") != "Running":
            return ""
        self_node = data.get("Self") or {}
        return (self_node.get("TailscaleIPs") or [""])[0]

    async def identity(self) -> dict[str, Any]:
        """Who owns this node — Tailscale login name + UserProfile."""
        data = await self._raw_status()
        self_node = data.get("Self") or {}
        user_id = self_node.get("UserID")
        users = data.get("User") or {}
        profile = users.get(str(user_id)) if user_id else None
        return {
            "login_name": (profile or {}).get("LoginName", ""),
            "display_name": (profile or {}).get("DisplayName", ""),
            "node_name": self_node.get("HostName", ""),
            "node_id": self_node.get("ID", ""),
        }

    async def peers(self, include_offline: bool = True) -> list[dict[str, Any]]:
        """Other devices on this tailnet."""
        data = await self._raw_status()
        out: list[dict[str, Any]] = []
        for node in (data.get("Peer") or {}).values():
            online = bool(node.get("Online"))
            if not include_offline and not online:
                continue
            ips = node.get("TailscaleIPs") or []
            out.append({
                "name": node.get("HostName", ""),
                "dns": node.get("DNSName", "").rstrip("."),
                "ip": ips[0] if ips else "",
                "ips": ips,
                "online": online,
                "os": node.get("OS", ""),
                "last_seen": node.get("LastSeen", ""),
                "exit_node": bool(node.get("ExitNode")),
            })
        out.sort(key=lambda p: (not p["online"], p["name"].lower()))
        return out

    async def is_peer(self, ip: str) -> bool:
        """Is this IP a tailnet member (self or any peer)? Useful for audit log labelling."""
        if not ip:
            return False
        data = await self._raw_status()
        self_node = data.get("Self") or {}
        for candidate in self_node.get("TailscaleIPs") or []:
            if candidate == ip:
                return True
        for node in (data.get("Peer") or {}).values():
            for candidate in node.get("TailscaleIPs") or []:
                if candidate == ip:
                    return True
        return False

    async def funnel_enabled(self) -> bool:
        """Is any service exposed via Tailscale Funnel from this node?

        `tailscale status --json` reports Funnel under `CurrentTailnet.FunnelEnabled`
        in recent versions; older versions surface it via `Self.CapMap`. We check
        both shapes and fall back to False on uncertainty.
        """
        data = await self._raw_status()
        tailnet = data.get("CurrentTailnet") or {}
        if "FunnelEnabled" in tailnet:
            return bool(tailnet.get("FunnelEnabled"))
        self_node = data.get("Self") or {}
        cap_map = self_node.get("CapMap") or {}
        return "https://tailscale.com/cap/funnel" in cap_map

    async def refresh(self) -> dict[str, Any]:
        """Force a status refresh, bypassing cache. Returns the new summary."""
        await self._raw_status(force=True)
        return await self.status()
