"""EmptyOS MCP Server — stdio JSON-RPC bridge to the EmptyOS tool registry.

Run as a subprocess by claude-cli when `--mcp-config` is set. Reads JSON-RPC
requests from stdin, proxies tool/call requests to the running EmptyOS daemon
at http://127.0.0.1:{EMPTYOS_PORT}/agent/api/mcp/tools/call, writes results
to stdout.

Claude-cli spawns this process per-turn. It must connect to a running daemon.

Usage (via --mcp-config):
    {
      "mcpServers": {
        "emptyos": {
          "command": "python",
          "args": ["-m", "emptyos.mcp_server"],
          "env": {"EMPTYOS_PORT": "9000"}
        }
      }
    }

MCP tool names inside claude-cli: mcp__emptyos__Bash, mcp__emptyos__Write, etc.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

EMPTYOS_PORT = os.environ.get("EMPTYOS_PORT", "9000")
BASE_URL = f"http://127.0.0.1:{EMPTYOS_PORT}"

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "emptyos", "version": "1.0.0"}

# Tools we expose through the MCP bridge — subset of the full registry that
# is meaningfully useful inside claude-cli's own agent loop.
# Read/Grep/Glob/WebSearch are already built-in to the CLI; we add the rest.
MCP_TOOL_NAMES = ["Bash", "Write", "Edit", "CallApp", "VaultQuery", "TaskList", "Fetch"]

_tool_schemas: list[dict] | None = None


def _fetch_tool_schemas() -> list[dict]:
    global _tool_schemas
    if _tool_schemas is not None:
        return _tool_schemas
    try:
        with urllib.request.urlopen(f"{BASE_URL}/agent/api/mcp/tools", timeout=5) as r:
            all_tools = json.loads(r.read())
        _tool_schemas = [t for t in all_tools if t["name"] in MCP_TOOL_NAMES]
    except Exception as e:
        _tool_schemas = []
        _log(f"[mcp_server] Failed to fetch tool schemas: {e}")
    return _tool_schemas


def _call_tool(name: str, arguments: dict) -> str:
    payload = json.dumps({"name": name, "arguments": arguments}).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}/agent/api/mcp/tools/call",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            result = json.loads(r.read())
        content = result.get("content", "")
        return str(content) if content is not None else ""
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return f"error: HTTP {e.code}: {body[:200]}"
    except Exception as e:
        return f"error: {e}"


def _handle(req: dict) -> dict:
    method = req.get("method", "")
    params = req.get("params") or {}

    if method == "initialize":
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        }

    if method == "notifications/initialized":
        return {}  # no-op notification

    if method == "tools/list":
        tools = _fetch_tool_schemas()
        return {"tools": tools}

    if method == "tools/call":
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            arguments = {}
        text = _call_tool(name, arguments)
        return {"content": [{"type": "text", "text": text}]}

    # Unknown method
    return {"error": {"code": -32601, "message": f"Method not found: {method}"}}


def _log(msg: str):
    print(msg, file=sys.stderr, flush=True)


def main():
    _log(f"[EmptyOS MCP Server] starting, daemon at {BASE_URL}")
    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            req = json.loads(raw_line)
        except json.JSONDecodeError as e:
            _log(f"[mcp_server] JSON parse error: {e}")
            continue

        req_id = req.get("id")
        try:
            result = _handle(req)
        except Exception as e:
            _log(f"[mcp_server] handler error: {e}")
            result = None
            resp = {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32603, "message": str(e)}}
            print(json.dumps(resp), flush=True)
            continue

        # Notifications (no id) don't get a response
        if req_id is None and req.get("method", "").startswith("notifications/"):
            continue

        if "error" in result:
            resp = {"jsonrpc": "2.0", "id": req_id, "error": result["error"]}
        else:
            resp = {"jsonrpc": "2.0", "id": req_id, "result": result}
        print(json.dumps(resp), flush=True)


if __name__ == "__main__":
    main()
