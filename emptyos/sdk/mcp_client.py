"""Inbound MCP client — let the EmptyOS agent loop CONSUME external MCP servers.

Mirror image of ``emptyos/mcp_server.py`` (which exposes EmptyOS's *own* tools TO
claude-cli): this spawns an external stdio MCP server (a filesystem server,
Context7, a GitHub server, …) and proxies *its* tools INTO the agent's tool
registry, so ``eos chat`` / ``/agent/`` can call them like native tools — with
the existing ``ToolConsentManager`` gate in front (the proxy tools are
``permission="ask"``).

Hand-rolled JSON-RPC 2.0 over stdio (newline-delimited), matching the framing in
``emptyos/mcp_server.py`` — no ``mcp`` SDK dependency. This module is pure
transport; the dark flag (``[apps.agent] feature.mcp-inbound.enabled``) lives at
the call site (the agent app's setup), so with the flag off this code is never
reached and the registry is byte-identical.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field

PROTOCOL_VERSION = "2024-11-05"
CLIENT_INFO = {"name": "emptyos-agent", "version": "1.0.0"}


@dataclass
class MCPServerSpec:
    """One external MCP server to spawn. Mirrors the standard ``mcpServers``
    dotfile entry shape used by Claude Code / Codex."""

    id: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None


def specs_from_config(rows: list[dict] | None) -> list[MCPServerSpec]:
    """Parse ``[[apps.agent.mcp_servers]]`` config rows into specs. Skips rows
    missing an ``id`` or ``command`` (fail-soft — a malformed row never raises)."""
    out: list[MCPServerSpec] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        sid = (r.get("id") or "").strip()
        cmd = (r.get("command") or "").strip()
        if not sid or not cmd:
            continue
        out.append(
            MCPServerSpec(
                id=sid,
                command=cmd,
                args=[str(a) for a in (r.get("args") or [])],
                env={str(k): str(v) for k, v in (r.get("env") or {}).items()},
                cwd=(r.get("cwd") or None),
            )
        )
    return out


class MCPClient:
    """A spawned stdio MCP server connection. One request in flight at a time
    (serialized by a lock) — the agent loop dispatches tools sequentially."""

    def __init__(self, spec: MCPServerSpec, *, timeout_s: float = 30.0):
        self.spec = spec
        self.timeout_s = timeout_s
        self._proc: asyncio.subprocess.Process | None = None
        self._id = 0
        self._lock = asyncio.Lock()
        self.tools: list[dict] = []

    async def start(self) -> None:
        """Spawn the server, run the MCP handshake, and cache its tool list."""
        env = {**os.environ, **(self.spec.env or {})}
        self._proc = await asyncio.create_subprocess_exec(
            self.spec.command,
            *self.spec.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
            cwd=self.spec.cwd or None,
        )
        await self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
        )
        await self._notify("notifications/initialized", {})
        res = await self._request("tools/list", {})
        self.tools = res.get("tools", []) or []

    async def call_tool(self, name: str, arguments: dict | None) -> str:
        """Invoke one remote tool; return its text content (or an ``error: ...``
        string). Never raises into the caller — the agent loop reads the string
        as a tool_result and self-corrects."""
        res = await self._request("tools/call", {"name": name, "arguments": arguments or {}})
        parts: list[str] = []
        for block in res.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        text = "\n".join(p for p in parts if p) if parts else json.dumps(res)
        if res.get("isError"):
            return f"error: {text}"
        return text

    async def stop(self) -> None:
        """Terminate the server subprocess. Never raises."""
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    # ── internals ───────────────────────────────────────────────────────

    async def _send(self, obj: dict) -> None:
        assert self._proc and self._proc.stdin
        self._proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        await self._proc.stdin.drain()

    async def _notify(self, method: str, params: dict) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def _request(self, method: str, params: dict) -> dict:
        if self._proc is None or self._proc.stdout is None:
            raise RuntimeError(f"MCP server {self.spec.id!r} not started")
        async with self._lock:
            self._id += 1
            rid = self._id
            await self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
            while True:
                raw = await asyncio.wait_for(
                    self._proc.stdout.readline(), timeout=self.timeout_s
                )
                if not raw:
                    raise RuntimeError(f"MCP server {self.spec.id!r} closed the stream")
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue  # stray non-JSON line on stdout — skip
                if msg.get("id") != rid:
                    continue  # a notification or out-of-order frame — skip
                if "error" in msg:
                    err = msg["error"]
                    detail = err.get("message", err) if isinstance(err, dict) else err
                    raise RuntimeError(f"MCP {method} error: {detail}")
                return msg.get("result") or {}


async def connect_mcp_servers(
    specs: list[MCPServerSpec], *, timeout_s: float = 30.0
) -> list[MCPClient]:
    """Start each server; a server that fails to start is dropped (one bad
    server must not block the rest, nor the daemon's boot)."""
    clients: list[MCPClient] = []
    for spec in specs:
        client = MCPClient(spec, timeout_s=timeout_s)
        try:
            await client.start()
            clients.append(client)
        except Exception:
            await client.stop()
    return clients
