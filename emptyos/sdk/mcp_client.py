"""Inbound MCP client — let the EmptyOS agent loop CONSUME external MCP servers.

Mirror image of ``emptyos/mcp_server.py`` (which exposes EmptyOS's *own* tools TO
claude-cli): this connects to an external MCP server (a filesystem server,
Context7, a GitHub server, a running Velorn desktop app, …) and proxies *its*
tools INTO the agent's tool registry, so ``eos chat`` / ``/agent/`` can call them
like native tools — with the existing ``ToolConsentManager`` gate in front (the
proxy tools are ``permission="ask"``).

Two transports, chosen per server by which field the config row carries:

- ``command`` → **stdio**: spawn the server as a subprocess and speak
  newline-delimited JSON-RPC on its stdin/stdout.
- ``url`` → **http**: POST one JSON-RPC object per request to a server someone
  else is already running. The response is ``application/json`` (or the frame
  inside an SSE ``data:`` line); notifications answer 202 with no body.

The second exists so the agent can drive local desktop apps that expose an MCP
endpoint — a running Velorn, say — which we reach but never spawn, and which are
therefore simply absent when the user has not launched them.

Hand-rolled JSON-RPC 2.0 in both cases, matching the framing in
``emptyos/mcp_server.py`` — no ``mcp`` SDK dependency. This module is pure
transport; the dark flag (``[apps.agent] feature.mcp-inbound.enabled``) lives at
the call site (the agent app's setup), so with the flag off this code is never
reached and the registry is byte-identical.

``aiohttp`` is imported lazily inside the HTTP transport only, so a stdio-only
deployment never pays for it and an import failure can only ever disable HTTP
servers — never the stdio ones that worked before this transport existed.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from emptyos.mcp_protocol import PROTOCOL_VERSION

CLIENT_INFO = {"name": "emptyos-agent", "version": "1.0.0"}

__all__ = [
    "CLIENT_INFO",
    "MCPClient",
    "MCPServerSpec",
    "PROTOCOL_VERSION",
    "Transport",
    "connect_mcp_servers",
    "specs_from_config",
]


@dataclass
class MCPServerSpec:
    """One external MCP server to reach. Mirrors the standard ``mcpServers``
    dotfile entry shape used by Claude Code / Codex, which carries either a
    ``command`` (stdio, spawned) or a ``url`` (HTTP, already running).

    ``command`` keeps its leading position so existing positional construction
    — ``MCPServerSpec("echo", sys.executable, [path])`` — is unchanged.
    """

    id: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def transport(self) -> str:
        """``"http"`` when a url is set, else ``"stdio"``. A row carrying both
        prefers stdio, because a spawned server is the one we control."""
        return "http" if (self.url and not self.command) else "stdio"


def specs_from_config(rows: list[dict] | None) -> list[MCPServerSpec]:
    """Parse ``[[apps.agent.mcp_servers]]`` config rows into specs. Skips rows
    missing an ``id``, or carrying neither ``command`` nor ``url`` (fail-soft —
    a malformed row never raises)."""
    out: list[MCPServerSpec] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        sid = (r.get("id") or "").strip()
        cmd = (r.get("command") or "").strip()
        url = (r.get("url") or "").strip()
        if not sid or not (cmd or url):
            continue
        out.append(
            MCPServerSpec(
                id=sid,
                command=cmd,
                args=[str(a) for a in (r.get("args") or [])],
                env={str(k): str(v) for k, v in (r.get("env") or {}).items()},
                cwd=(r.get("cwd") or None),
                url=url,
                headers={str(k): str(v) for k, v in (r.get("headers") or {}).items()},
            )
        )
    return out


@runtime_checkable
class Transport(Protocol):
    """What ``MCPClient`` needs of a wire, and the whole of what it needs.

    The handshake, the request-id counter, the serializing lock, and the tool
    cache all live above this line and are transport-blind; everything below it
    is framing. Two implementations follow — stdio and HTTP — and the split is
    what lets an error from either read identically to the agent loop.

    ``runtime_checkable`` so the implementations can be pinned by a test. Note
    what that buys and what it does not: ``isinstance`` checks method *presence*
    only, never signatures, so it catches a transport that missed a method — not
    one whose ``request`` takes different arguments.
    """

    async def open(self) -> None:
        """Make the connection usable — spawn, or build a client session."""
        ...

    async def notify(self, method: str, params: dict) -> None:
        """Send a frame carrying no ``id``. Nothing comes back."""
        ...

    async def request(self, rid: int, method: str, params: dict) -> dict:
        """Send a frame with ``id=rid`` and return that frame's ``result``.
        Raises on a JSON-RPC error, so both wires fail the same way."""
        ...

    async def close(self) -> None:
        """Release whatever ``open`` acquired. Must never raise, and must be
        safe on a transport that never opened."""
        ...


def _result_or_raise(msg: dict, method: str) -> dict:
    """Unwrap one JSON-RPC response frame. Shared by both transports so an
    error from an HTTP server reads identically to one from a stdio server."""
    if "error" in msg:
        err = msg["error"]
        detail = err.get("message", err) if isinstance(err, dict) else err
        raise RuntimeError(f"MCP {method} error: {detail}")
    return msg.get("result") or {}


class _StdioTransport:
    """Spawn the server and speak newline-delimited JSON-RPC on its stdio.

    Byte-for-byte the behaviour this module shipped before the HTTP transport
    existed — only lifted out of ``MCPClient`` so the handshake above it can be
    shared.
    """

    def __init__(self, spec: MCPServerSpec, timeout_s: float):
        self.spec = spec
        self.timeout_s = timeout_s
        self._proc: asyncio.subprocess.Process | None = None

    async def open(self) -> None:
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

    async def _send(self, obj: dict) -> None:
        assert self._proc and self._proc.stdin
        self._proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        await self._proc.stdin.drain()

    async def notify(self, method: str, params: dict) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def request(self, rid: int, method: str, params: dict) -> dict:
        if self._proc is None or self._proc.stdout is None:
            raise RuntimeError(f"MCP server {self.spec.id!r} not started")
        await self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            raw = await asyncio.wait_for(self._proc.stdout.readline(), timeout=self.timeout_s)
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
            return _result_or_raise(msg, method)

    async def close(self) -> None:
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


class _HttpTransport:
    """POST one JSON-RPC object per request to an already-running MCP server.

    The MCP Streamable-HTTP shape: a request POST answers either
    ``application/json`` (one response frame) or ``text/event-stream`` (the
    frame inside an SSE ``data:`` line); a notification POST answers 202 with
    an empty body and is never waited on. A ``Mcp-Session-Id`` handed back by
    the server is echoed on every later request.

    Nothing here spawns or supervises the server — an HTTP MCP server is a
    process someone else owns, so ``close()`` only drops our client session.
    That is why a server behind this transport is *dark when it is not running*
    rather than something the daemon can start.
    """

    #: Bound on *reaching* the host, separate from ``timeout_s`` (which budgets
    #: a whole tool call). A URL — unlike a spawned ``command`` — can name a
    #: machine that is asleep, firewalled, or off the VPN, and such a host does
    #: not refuse the connection, it swallows it. ``connect_mcp_servers`` is
    #: awaited inside the agent app's ``setup()``, so without this bound one
    #: stale row would stall app load for the full ``timeout_s`` on every boot
    #: (`.claude/rules/debugging.md` § "Awaited warm-up in setup()").
    CONNECT_TIMEOUT_S = 5.0

    def __init__(self, spec: MCPServerSpec, timeout_s: float):
        self.spec = spec
        self.timeout_s = timeout_s
        self._session = None
        self._session_id = ""

    async def open(self) -> None:
        import aiohttp

        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(
                total=self.timeout_s,
                sock_connect=min(self.CONNECT_TIMEOUT_S, self.timeout_s),
            )
        )

    def _headers(self) -> dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        h.update(self.spec.headers or {})
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    async def notify(self, method: str, params: dict) -> None:
        """Fire-and-forget. A notification carries no ``id``, so a spec-abiding
        server replies 202 with no body — there is nothing to read, and a
        failure here must not sink an otherwise working session."""
        if self._session is None:
            raise RuntimeError(f"MCP server {self.spec.id!r} not started")
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        try:
            async with self._session.post(
                self.spec.url, json=payload, headers=self._headers()
            ) as resp:
                await resp.read()
        except Exception:
            pass

    async def request(self, rid: int, method: str, params: dict) -> dict:
        if self._session is None:
            raise RuntimeError(f"MCP server {self.spec.id!r} not started")
        payload = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        async with self._session.post(
            self.spec.url, json=payload, headers=self._headers()
        ) as resp:
            sid = resp.headers.get("Mcp-Session-Id")
            if sid:
                self._session_id = sid
            if resp.status >= 400:
                body = (await resp.text())[:400]
                raise RuntimeError(f"MCP {method} error: HTTP {resp.status} {body}")
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "text/event-stream" in ctype:
                msg = await self._read_sse(resp, rid)
            else:
                raw = (await resp.text()).strip()
                if not raw:
                    raise RuntimeError(f"MCP {method} error: empty response body")
                try:
                    msg = json.loads(raw)
                except ValueError:
                    # A proxy or captive portal answering 200 with HTML is the
                    # realistic case. Name it here rather than letting a raw
                    # JSONDecodeError out — every other failure in this module
                    # reads as "MCP <method> error: ...", and the agent loop
                    # self-corrects off that string.
                    raise RuntimeError(
                        f"MCP {method} error: non-JSON response ({raw[:200]})"
                    ) from None
        # A server may answer a single request with a one-element batch.
        if isinstance(msg, list):
            match = next(
                (m for m in msg if isinstance(m, dict) and m.get("id") == rid), None
            )
            if match is None:
                raise RuntimeError(f"MCP {method} error: no frame for id {rid}")
            msg = match
        return _result_or_raise(msg, method)

    async def _read_sse(self, resp, rid: int) -> dict:
        """Pull ``data:`` frames until the one matching our id. The session's
        total timeout bounds this — an SSE stream that never carries our frame
        fails as a timeout rather than hanging the agent loop."""
        async for raw in resp.content:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if not chunk:
                continue
            try:
                msg = json.loads(chunk)
            except Exception:
                continue
            if isinstance(msg, dict) and msg.get("id") != rid:
                continue
            return msg
        raise RuntimeError(f"MCP server {self.spec.id!r} closed the stream")

    async def close(self) -> None:
        session, self._session = self._session, None
        if session is None:
            return
        try:
            await session.close()
        except Exception:
            pass


class MCPClient:
    """One external MCP server connection, over stdio or HTTP. A single request
    is in flight at a time (serialized by a lock) — the agent loop dispatches
    tools sequentially."""

    def __init__(self, spec: MCPServerSpec, *, timeout_s: float = 30.0):
        self.spec = spec
        self.timeout_s = timeout_s
        self._id = 0
        self._lock = asyncio.Lock()
        self.tools: list[dict] = []
        self._transport: Transport = (
            _HttpTransport(spec, timeout_s)
            if spec.transport == "http"
            else _StdioTransport(spec, timeout_s)
        )

    async def start(self) -> None:
        """Connect (spawning first, for stdio), run the MCP handshake, and cache
        the server's tool list."""
        await self._transport.open()
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
        """Release the connection — terminate the subprocess (stdio) or close
        the client session (HTTP). Safe to call on a client that never started,
        which is the path ``connect_mcp_servers`` takes when ``start()`` throws.
        Never raises."""
        await self._transport.close()

    # ── internals ───────────────────────────────────────────────────────

    async def _notify(self, method: str, params: dict) -> None:
        await self._transport.notify(method, params)

    async def _request(self, method: str, params: dict) -> dict:
        async with self._lock:
            self._id += 1
            return await self._transport.request(self._id, method, params)


async def connect_mcp_servers(
    specs: list[MCPServerSpec], *, timeout_s: float = 30.0
) -> list[MCPClient]:
    """Start each server; a server that fails to start is dropped (one bad
    server must not block the rest, nor the daemon's boot). An HTTP server that
    simply is not running today — a desktop app the user has not launched —
    fails here and leaves the registry exactly as it was."""
    clients: list[MCPClient] = []
    for spec in specs:
        client = MCPClient(spec, timeout_s=timeout_s)
        try:
            await client.start()
        except asyncio.CancelledError:
            # A cancel mid-boot (daemon shutting down while setup runs) is not
            # an ``Exception``, so it would sail past the handler below and
            # leak the transport it had already opened — an aiohttp session
            # loud enough to print "Unclosed client session" at exit. Release
            # it, then let the cancellation continue.
            await client.stop()
            raise
        except Exception:
            await client.stop()
            continue
        clients.append(client)
    return clients
