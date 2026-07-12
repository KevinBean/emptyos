# blender-mcp

MCP shim over the EmptyOS Blender bridge. Lets Claude Desktop / Cursor / any
MCP client drive a running Blender through the hardened loopback addon at
`plugins/blender/addon/eos_bridge.py`.

This service does **not** run as a daemon on a vhost — it's stdio-only, spawned
by the MCP client. No EmptyOS daemon needed; only the Blender addon must be
loaded.

## Prerequisites

1. Blender 4.0+ with the EmptyOS Bridge addon installed and enabled
   (`plugins/blender/addon/eos_bridge.py`). The addon listens on
   `127.0.0.1:8400`.
2. Python 3.11+.
3. Either the EmptyOS daemon has run once on this machine (which writes
   `~/.eos/blender-bridge.token`), or `EOS_BRIDGE_TOKEN` is exported.

## Install

```bash
cd services/blender-mcp
pip install -e .
```

## Wire into Claude Desktop

Add this entry to `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "blender": {
      "command": "python",
      "args": ["-m", "server"],
      "cwd": "D:/emptyos/services/blender-mcp"
    }
  }
}
```

Restart Claude Desktop. The Blender tools (`ping`, `scene_info`, `list_objects`,
`import_model`, `export_model`, `set_material`, `render`) appear under the
connector menu.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `EOS_BLENDER_URL` | `http://127.0.0.1:8400` | Bridge endpoint |
| `EOS_BRIDGE_TOKEN` | (read from `~/.eos/blender-bridge.token`) | Bearer token |

## Tools exposed

| Tool | Maps to RPC method |
|---|---|
| `ping` | `ping` |
| `scene_info` | `scene_info` |
| `list_objects` | `list_objects` |
| `import_model` | `import_model` |
| `export_model` | `export_model` |
| `set_material` | `set_material` |
| `viewport_screenshot` | `viewport_screenshot` |
| `render` | `render_scene` |

The tool surface is intentionally a 1:1 mirror of the bridge's RPC methods.
Adding a new tool = adding the method to the addon + a `@mcp.tool()` wrapper
here. Auth, main-thread dispatch, and error shaping are handled by the bridge.
