"""Unit tests for inbound MCP (P1.3) — the EOS loop consuming external MCP servers.

Covers the pure config/proxy layer plus a real stdio round-trip against a
minimal echo MCP server spawned as a subprocess. No daemon.
"""

from __future__ import annotations

import sys
import textwrap

import pytest

from emptyos.sdk.agent_tools.mcp_proxy import MCPProxyTool, proxy_tools_for
from emptyos.sdk.mcp_client import MCPClient, MCPServerSpec, specs_from_config


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
