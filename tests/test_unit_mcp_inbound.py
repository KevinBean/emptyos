"""Unit tests for inbound MCP (P1.3) — the EOS loop consuming external MCP servers.

Covers the pure config/proxy layer plus a real round-trip on **both** transports:
a stdio echo server spawned as a subprocess, and an HTTP server shaped like
Velorn's `electron/mcpServer.js`. No daemon.
"""

from __future__ import annotations

import asyncio
import json
import sys
import textwrap

import pytest

from emptyos.sdk import mcp_client as mcp_client_mod
from emptyos.sdk.agent_tools.mcp_proxy import MCPProxyTool, proxy_tools_for
from emptyos.sdk.mcp_client import (
    MCPClient,
    MCPServerSpec,
    Transport,
    _HttpTransport,
    _StdioTransport,
    connect_mcp_servers,
    specs_from_config,
)

# ── the two shared shapes ───────────────────────────────────────────────

def test_one_protocol_version_across_every_mcp_module():
    """Both servers advertise it, the client requests it. Three copies of the
    same literal meant a revision bump applied twice and missed once."""
    from emptyos import mcp_outbound_server, mcp_protocol, mcp_server
    from emptyos.sdk import mcp_client

    versions = {
        "leaf": mcp_protocol.PROTOCOL_VERSION,
        "mcp_server": mcp_server.PROTOCOL_VERSION,
        "mcp_outbound_server": mcp_outbound_server.PROTOCOL_VERSION,
        "mcp_client": mcp_client.PROTOCOL_VERSION,
    }
    assert len(set(versions.values())) == 1, versions


def test_mcp_protocol_leaf_stays_import_free():
    """`mcp_server.py` is spawned standalone (`python -m emptyos.mcp_server`)
    and imports nothing but stdlib. The shared constant must not become the
    thing that drags `emptyos.sdk` into that bridge process."""
    import ast
    from pathlib import Path

    import emptyos.mcp_protocol as leaf

    tree = ast.parse(Path(leaf.__file__).read_text(encoding="utf-8"))
    imports = [
        n for n in ast.walk(tree)
        if isinstance(n, (ast.Import, ast.ImportFrom))
        and not (isinstance(n, ast.ImportFrom) and n.module == "__future__")
    ]
    assert imports == [], "the leaf grew an import"


def test_both_transports_satisfy_the_transport_protocol():
    """Presence only — `runtime_checkable` cannot check signatures. Enough to
    catch a transport that missed a method the client calls."""
    spec = MCPServerSpec(id="t", command="python")
    assert isinstance(_StdioTransport(spec, 1.0), Transport)
    assert isinstance(_HttpTransport(MCPServerSpec(id="t", url="http://x/mcp"), 1.0), Transport)

# ── pure config parsing ─────────────────────────────────────────────────

def test_specs_from_config_parses_and_skips_bad_rows():
    rows = [
        {"id": "fs", "command": "python", "args": ["-m", "srv"], "env": {"K": "v"}},
        {"id": "", "command": "x"},          # no id → skip
        {"id": "y", "command": ""},          # no command → skip
        "not-a-dict",                          # skip
    ]
    specs = specs_from_config(rows)
    assert len(specs) == 1
    assert specs[0].id == "fs"
    assert specs[0].args == ["-m", "srv"]
    assert specs[0].env == {"K": "v"}


def test_specs_from_config_empty():
    assert specs_from_config(None) == []
    assert specs_from_config([]) == []


def test_specs_from_config_parses_url_rows():
    rows = [
        {"id": "velorn", "url": "http://127.0.0.1:19790/mcp", "headers": {"X-K": "v"}},
        {"id": "nope"},                      # neither command nor url → skip
        {"id": "", "url": "http://x/mcp"},   # no id → skip
    ]
    specs = specs_from_config(rows)
    assert len(specs) == 1
    assert specs[0].id == "velorn"
    assert specs[0].url == "http://127.0.0.1:19790/mcp"
    assert specs[0].headers == {"X-K": "v"}


def test_spec_transport_selection():
    assert MCPServerSpec(id="a", url="http://x/mcp").transport == "http"
    assert MCPServerSpec(id="b", command="python").transport == "stdio"
    # A row carrying both prefers the server we spawn and control.
    assert MCPServerSpec(id="c", command="python", url="http://x/mcp").transport == "stdio"


# ── proxy tool naming + dispatch (fake client, no subprocess) ────────────

class _FakeSpec:
    id = "demo"


class _FakeClient:
    def __init__(self, tools, result="ok"):
        self.spec = _FakeSpec()
        self.tools = tools
        self._result = result
        self.called_with = None

    async def call_tool(self, name, arguments):
        self.called_with = (name, arguments)
        return self._result


@pytest.mark.asyncio
async def test_proxy_tool_namespaced_name_and_run():
    client = _FakeClient(tools=[], result="hello from mcp")
    tool = MCPProxyTool(client, "read_file", "Read a file", {"type": "object"})
    assert tool.name == "mcp__demo__read_file"
    assert tool.permission == "ask"       # gated like any mutating tool
    assert tool.readonly is False         # plan mode blocks it

    res = await tool.run(None, path="x.txt")
    assert res.ok is True
    assert res.content == "hello from mcp"
    assert client.called_with == ("read_file", {"path": "x.txt"})


@pytest.mark.asyncio
async def test_proxy_tool_error_string_marks_not_ok():
    client = _FakeClient(tools=[], result="error: boom")
    tool = MCPProxyTool(client, "do", "")
    res = await tool.run(None)
    assert res.ok is False


def test_proxy_tools_for_builds_one_per_tool():
    client = _FakeClient(tools=[
        {"name": "a", "description": "tool a", "inputSchema": {"type": "object"}},
        {"name": "b"},
        {"no_name": True},   # skipped
    ])
    tools = proxy_tools_for(client)
    assert [t.name for t in tools] == ["mcp__demo__a", "mcp__demo__b"]


# ── real stdio round-trip against a minimal echo MCP server ──────────────

_ECHO_SERVER = textwrap.dedent("""
    import json, sys
    def main():
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            req = json.loads(line)
            method = req.get("method", "")
            rid = req.get("id")
            if method == "initialize":
                result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                          "serverInfo": {"name": "echo", "version": "1.0.0"}}
            elif method == "notifications/initialized":
                continue  # notification — no response
            elif method == "tools/list":
                result = {"tools": [{"name": "echo", "description": "echo text",
                          "inputSchema": {"type": "object",
                          "properties": {"text": {"type": "string"}}}}]}
            elif method == "tools/call":
                args = (req.get("params") or {}).get("arguments") or {}
                result = {"content": [{"type": "text", "text": args.get("text", "")}]}
            else:
                print(json.dumps({"jsonrpc": "2.0", "id": rid,
                      "error": {"code": -32601, "message": "no"}}), flush=True)
                continue
            print(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}), flush=True)
    main()
""")


@pytest.mark.asyncio
async def test_real_stdio_handshake_list_and_call(tmp_path):
    server = tmp_path / "echo_server.py"
    server.write_text(_ECHO_SERVER, encoding="utf-8")

    spec = MCPServerSpec(id="echo", command=sys.executable, args=[str(server)])
    client = MCPClient(spec, timeout_s=10)
    try:
        await client.start()
        assert [t["name"] for t in client.tools] == ["echo"]

        out = await client.call_tool("echo", {"text": "round-trip works"})
        assert out == "round-trip works"

        # proxy wrapping the real client
        tools = proxy_tools_for(client)
        assert tools[0].name == "mcp__echo__echo"
        res = await tools[0].run(None, text="via proxy")
        assert res.ok and res.content == "via proxy"
    finally:
        await client.stop()


# ── real HTTP round-trip against a Velorn-shaped MCP server ──────────────
#
# The handler below mirrors Velorn's own `electron/mcpServer.js:handleRequest`:
# POST answers 200 `application/json` with one JSON-RPC frame, and anything
# without an id (or under `notifications/`) answers 202 with an empty body.
# Testing against that shape rather than a mock is what proves the transport
# would actually drive a running Velorn desktop app.

async def _serve_mcp(handler):
    """Start a throwaway MCP server on an ephemeral loopback port.
    Returns ``(url, cleanup)``."""
    from aiohttp import web

    app = web.Application()
    app.router.add_post("/mcp", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    return f"http://127.0.0.1:{port}/mcp", runner.cleanup


def _dispatch(method, rid, params):
    """The three methods the handshake + a tool call need."""
    if method == "initialize":
        return {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                "serverInfo": {"name": "velorn-ish", "version": "0.3.23"}}
    if method == "tools/list":
        return {"tools": [{"name": "get_project_summary", "description": "summary",
                           "inputSchema": {"type": "object"}},
                          {"name": "echo", "description": "echo text",
                           "inputSchema": {"type": "object"}}]}
    if method == "tools/call":
        args = (params or {}).get("arguments") or {}
        return {"content": [{"type": "text", "text": args.get("text", "")}]}
    return None


@pytest.mark.asyncio
async def test_real_http_handshake_list_and_call():
    from aiohttp import web

    seen: list[str] = []

    async def handler(request):
        msg = await request.json()
        method, rid = str(msg.get("method") or ""), msg.get("id")
        seen.append(method)
        if rid is None or method.startswith("notifications/"):
            return web.Response(status=202)          # Velorn's notification path
        result = _dispatch(method, rid, msg.get("params"))
        if result is None:
            return web.json_response(
                {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no"}}
            )
        return web.json_response({"jsonrpc": "2.0", "id": rid, "result": result})

    url, cleanup = await _serve_mcp(handler)
    client = MCPClient(MCPServerSpec(id="velorn", url=url), timeout_s=10)
    try:
        await client.start()
        assert [t["name"] for t in client.tools] == ["get_project_summary", "echo"]
        # The notification must have been sent, and must not have blocked.
        assert "notifications/initialized" in seen

        out = await client.call_tool("echo", {"text": "http round-trip works"})
        assert out == "http round-trip works"

        tools = proxy_tools_for(client)
        assert tools[0].name == "mcp__velorn__get_project_summary"
        assert tools[0].permission == "ask"      # still consent-gated over HTTP
    finally:
        await client.stop()
        await cleanup()


@pytest.mark.asyncio
async def test_http_echoes_session_id_after_initialize():
    from aiohttp import web

    got_session_headers: list[str | None] = []

    async def handler(request):
        msg = await request.json()
        method, rid = str(msg.get("method") or ""), msg.get("id")
        got_session_headers.append(request.headers.get("Mcp-Session-Id"))
        if rid is None or method.startswith("notifications/"):
            return web.Response(status=202)
        return web.json_response(
            {"jsonrpc": "2.0", "id": rid, "result": _dispatch(method, rid, msg.get("params"))},
            headers={"Mcp-Session-Id": "sess-42"},
        )

    url, cleanup = await _serve_mcp(handler)
    client = MCPClient(MCPServerSpec(id="s", url=url), timeout_s=10)
    try:
        await client.start()
        # First request cannot carry one; everything after initialize must.
        assert got_session_headers[0] is None
        assert got_session_headers[-1] == "sess-42"
    finally:
        await client.stop()
        await cleanup()


@pytest.mark.asyncio
async def test_http_reads_sse_framed_response():
    """A spec-abiding server may answer a POST with text/event-stream instead
    of JSON. The frame we want may not be the first one on the wire."""
    from aiohttp import web

    async def handler(request):
        msg = await request.json()
        method, rid = str(msg.get("method") or ""), msg.get("id")
        if rid is None or method.startswith("notifications/"):
            return web.Response(status=202)
        frame = {"jsonrpc": "2.0", "id": rid, "result": _dispatch(method, rid, msg.get("params"))}
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(b": keepalive\n\n")                       # noise, skipped
        await resp.write(b'data: {"jsonrpc":"2.0","id":999}\n\n')  # wrong id, skipped
        await resp.write(f"data: {json.dumps(frame)}\n\n".encode())
        await resp.write_eof()
        return resp

    url, cleanup = await _serve_mcp(handler)
    client = MCPClient(MCPServerSpec(id="sse", url=url), timeout_s=10)
    try:
        await client.start()
        assert [t["name"] for t in client.tools] == ["get_project_summary", "echo"]
        assert await client.call_tool("echo", {"text": "over sse"}) == "over sse"
    finally:
        await client.stop()
        await cleanup()


@pytest.mark.asyncio
async def test_http_tool_error_frame_becomes_error_string():
    """A JSON-RPC error must read the same as it does over stdio, so the agent
    loop's self-correction path is transport-independent."""
    from aiohttp import web

    async def handler(request):
        msg = await request.json()
        method, rid = str(msg.get("method") or ""), msg.get("id")
        if rid is None or method.startswith("notifications/"):
            return web.Response(status=202)
        if method == "tools/call":
            return web.json_response(
                {"jsonrpc": "2.0", "id": rid,
                 "error": {"code": -32602, "message": "no project open"}}
            )
        return web.json_response(
            {"jsonrpc": "2.0", "id": rid, "result": _dispatch(method, rid, msg.get("params"))}
        )

    url, cleanup = await _serve_mcp(handler)
    client = MCPClient(MCPServerSpec(id="v", url=url), timeout_s=10)
    try:
        await client.start()
        tool = proxy_tools_for(client)[0]
        res = await tool.run(None)
        assert res.ok is False
        assert "no project open" in res.content
    finally:
        await client.stop()
        await cleanup()


@pytest.mark.asyncio
async def test_http_non_json_200_reads_like_every_other_mcp_error():
    """A proxy or captive portal answering 200 with HTML. The module's contract
    is that a failure reads `MCP <method> error: ...` whatever the transport —
    a bare JSONDecodeError would leak the implementation into what the agent
    loop self-corrects against."""
    from aiohttp import web

    async def handler(request):
        return web.Response(status=200, text="<html>proxy says no</html>",
                            content_type="text/html")

    url, cleanup = await _serve_mcp(handler)
    client = MCPClient(MCPServerSpec(id="proxied", url=url), timeout_s=10)
    try:
        with pytest.raises(RuntimeError, match=r"MCP initialize error: non-JSON"):
            await client.start()
    finally:
        await client.stop()
        await cleanup()


@pytest.mark.asyncio
async def test_connect_drops_unreachable_http_server():
    """The everyday case: Velorn is installed but not running. The registry
    must be left exactly as it was, with no exception escaping boot."""
    specs = [MCPServerSpec(id="velorn", url="http://127.0.0.1:1/mcp")]
    clients = await connect_mcp_servers(specs, timeout_s=2)
    assert clients == []


@pytest.mark.asyncio
async def test_http_bounds_reaching_the_host_separately_from_the_call():
    """The *other* unreachable case, and the dangerous one: a url — unlike a
    spawned command — can name a host that is asleep, off the VPN, or behind a
    DROP rule. Such a host does not refuse the connection the way a dead
    loopback port does; it swallows it. Since `connect_mcp_servers` is awaited
    inside the agent app's `setup()`, one stale row would otherwise stall app
    load for the whole `timeout_s` on every single boot.

    Pinned on the timeout rather than a real black-holed IP so it stays
    deterministic — networks differ on whether they DROP or REJECT.
    """
    t = _HttpTransport(MCPServerSpec(id="x", url="http://127.0.0.1:1/mcp"), 30.0)
    await t.open()
    try:
        timeout = t._session.timeout
        assert timeout.total == 30.0, "a whole tool call still gets its full budget"
        assert timeout.sock_connect == 5.0
        assert timeout.sock_connect < timeout.total
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_connect_releases_the_transport_when_cancelled(monkeypatch):
    """A cancel mid-handshake — the daemon shutting down while agent setup is
    still reaching a slow server — must not leak the session that was already
    opened. `CancelledError` is a BaseException, so the fail-soft `except
    Exception` in `connect_mcp_servers` does not see it.
    """
    from aiohttp import web

    release = asyncio.Event()                    # held open until teardown

    async def never_answers(request):
        await release.wait()
        return web.Response(status=204)

    url, cleanup = await _serve_mcp(never_answers)

    created: list[MCPClient] = []
    real_client = mcp_client_mod.MCPClient

    class _Recording(real_client):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            created.append(self)

    monkeypatch.setattr(mcp_client_mod, "MCPClient", _Recording)

    task = asyncio.create_task(
        mcp_client_mod.connect_mcp_servers(
            [MCPServerSpec(id="slow", url=url)], timeout_s=30
        )
    )
    await asyncio.sleep(0.25)                    # let it open + post
    assert created and created[0]._transport._session is not None
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert created[0]._transport._session is None, "aiohttp session left open"
    release.set()
    await cleanup()
