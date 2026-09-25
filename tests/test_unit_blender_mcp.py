"""Smoke tests for services/blender-mcp/server.py.

Booting real Blender in CI is heavy and fragile (GPU drivers, addon install,
viewport contexts). Instead, mock the JSON-RPC bridge with a tiny stdlib HTTP
server and verify the MCP shim's tool functions round-trip correctly. This
catches: tool-registration drift, RPC parameter shape changes, auth header
plumbing, and error mapping — the things most likely to silently break.

Real-Blender integration is exercised manually via the README's Claude Desktop
flow.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SHIM_DIR = ROOT / "services" / "blender-mcp"
sys.path.insert(0, str(SHIM_DIR))


_MOCK_RESPONSES: dict = {
    "ping": "pong",
    "scene_info": {
        "name": "Scene",
        "frame_current": 1,
        "frame_start": 1,
        "frame_end": 250,
        "fps": 24,
        "engine": "CYCLES",
        "resolution": [1920, 1080],
        "object_count": 3,
    },
    "list_objects": {
        "objects": [
            {"name": "Cube", "type": "MESH", "location": [0, 0, 0]},
            {"name": "Camera", "type": "CAMERA", "location": [7, -7, 5]},
        ]
    },
    "import_model": {"imported": "/tmp/x.glb", "format": "glb"},
    "export_model": {"exported": "/tmp/y.glb", "format": "glb"},
    "set_material": {"material": "eos_Cube", "object": "Cube"},
    "viewport_screenshot": {"image_path": "/tmp/viewport.png"},
    "render_scene": {"image_path": "/tmp/render.png"},
}

_RECEIVED: list[dict] = []


class _BridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, *a, **k):  # silence stderr spam
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n))
        _RECEIVED.append(
            {
                "method": req.get("method"),
                "params": req.get("params"),
                "auth": self.headers.get("Authorization", ""),
            }
        )
        method = req.get("method", "")
        result = _MOCK_RESPONSES.get(method)
        if result is None:
            body = {
                "jsonrpc": "2.0",
                "id": req.get("id"),
                "error": {"code": -32601, "message": f"unknown {method}"},
            }
        else:
            body = {"jsonrpc": "2.0", "id": req.get("id"), "result": result}
        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture(scope="module")
def mock_bridge():
    srv = HTTPServer(("127.0.0.1", 0), _BridgeHandler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    os.environ["EOS_BLENDER_URL"] = f"http://127.0.0.1:{port}"
    os.environ["EOS_BRIDGE_TOKEN"] = "test-token"
    # Force re-import so the new env vars are picked up if the module was
    # imported by an earlier test session.
    sys.modules.pop("server", None)
    yield port
    srv.shutdown()


@pytest.fixture(autouse=True)
def _reset_received():
    _RECEIVED.clear()
    yield


def _run(coro):
    return asyncio.run(coro)


def test_tools_registered(mock_bridge):
    import server

    expected = {
        "ping",
        "scene_info",
        "list_objects",
        "import_model",
        "export_model",
        "set_material",
        "viewport_screenshot",
        "render",
    }
    # FastMCP keeps registered tools on the server; reach in via list_tools.
    tool_names = _run(server.mcp.list_tools())
    names = {t.name for t in tool_names}
    missing = expected - names
    assert not missing, f"missing tools: {missing}"


def test_ping(mock_bridge):
    import server

    assert _run(server.ping()) == "pong"
    assert _RECEIVED[-1]["method"] == "ping"
    assert _RECEIVED[-1]["auth"] == "Bearer test-token"


def test_scene_info(mock_bridge):
    import server

    info = _run(server.scene_info())
    assert info["name"] == "Scene"
    assert info["object_count"] == 3


def test_list_objects(mock_bridge):
    import server

    objs = _run(server.list_objects())
    assert isinstance(objs, list)
    assert objs[0]["name"] == "Cube"


def test_import_model(mock_bridge):
    import server

    out = _run(server.import_model(path="/tmp/x.glb", format="glb"))
    assert out["format"] == "glb"
    sent = _RECEIVED[-1]
    assert sent["method"] == "import_model"
    assert sent["params"] == {"path": "/tmp/x.glb", "format": "glb"}


def test_set_material_strips_unset_fields(mock_bridge):
    import server

    _run(server.set_material(object="Cube", color=[0.8, 0.2, 0.2]))
    sent = _RECEIVED[-1]
    assert sent["method"] == "set_material"
    assert sent["params"]["object"] == "Cube"
    # Only `color` should be in material — unset args must not show up as None.
    assert sent["params"]["material"] == {"color": [0.8, 0.2, 0.2]}


def test_viewport_screenshot_default(mock_bridge):
    import server

    out = _run(server.viewport_screenshot())
    assert out["image_path"].endswith(".png")
    sent = _RECEIVED[-1]
    assert sent["method"] == "viewport_screenshot"
    # No args set → params dict empty (server picks defaults).
    assert sent["params"] == {}


def test_viewport_screenshot_with_size(mock_bridge):
    import server

    _run(server.viewport_screenshot(width=512, height=512))
    sent = _RECEIVED[-1]
    assert sent["params"] == {"width": 512, "height": 512}


def test_render_maps_to_render_scene(mock_bridge):
    import server

    out = _run(server.render(width=800, height=600))
    assert out["image_path"]
    sent = _RECEIVED[-1]
    assert sent["method"] == "render_scene"
    assert sent["params"] == {"width": 800, "height": 600}


def test_rpc_error_raises(mock_bridge):
    import server

    with pytest.raises(RuntimeError, match="unknown"):
        _run(server._rpc("does_not_exist"))
