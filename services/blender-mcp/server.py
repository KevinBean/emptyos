"""Blender MCP shim — bridges MCP clients to the EmptyOS Blender JSON-RPC addon.

Lets Claude Desktop / Cursor / any MCP client drive a running Blender via the
hardened loopback bridge in `plugins/blender/addon/eos_bridge.py`. No EmptyOS
daemon required — this process talks directly to Blender on 127.0.0.1:8400 and
shares the same `~/.eos/blender-bridge.token` the daemon uses.

Run:
    uv run python -m server          # or `python server.py` after `pip install -e .`

Wire into Claude Desktop via mcp_config.json — see README.md.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

BRIDGE_URL = os.environ.get("EOS_BLENDER_URL", "http://127.0.0.1:8400")
TOKEN_PATH = Path.home() / ".eos" / "blender-bridge.token"

mcp = FastMCP("blender")


def _token() -> str:
    tok = os.environ.get("EOS_BRIDGE_TOKEN", "").strip()
    if tok:
        return tok
    try:
        return TOKEN_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


async def _rpc(method: str, params: dict | None = None, timeout: float = 120) -> Any:
    headers = {}
    if (tok := _token()):
        headers["Authorization"] = f"Bearer {tok}"
    payload = {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": method}
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(BRIDGE_URL, json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()
    if "error" in data:
        err = data["error"]
        raise RuntimeError(f"Blender RPC error ({err.get('code')}): {err.get('message')}")
    return data.get("result", {})


@mcp.tool()
async def ping() -> str:
    """Check that Blender is running with the EmptyOS Bridge addon enabled."""
    return await _rpc("ping")


@mcp.tool()
async def scene_info() -> dict:
    """Return the active scene's name, frame range, fps, engine, resolution, and object count."""
    return await _rpc("scene_info")


@mcp.tool()
async def list_objects() -> list[dict]:
    """List every object in the active scene with name, type, location, rotation, scale, visibility."""
    result = await _rpc("list_objects")
    return result.get("objects", [])


@mcp.tool()
async def import_model(path: str, format: str = "auto") -> dict:
    """Import a 3D model into the current scene.

    Args:
        path: Absolute path to the source file.
        format: One of obj, fbx, glb, gltf, stl, ply. "auto" infers from the extension.
    """
    return await _rpc("import_model", {"path": path, "format": format})


@mcp.tool()
async def export_model(path: str, format: str = "glb") -> dict:
    """Export the current scene to a file.

    Args:
        path: Absolute output path.
        format: One of obj, fbx, glb, gltf, stl.
    """
    return await _rpc("export_model", {"path": path, "format": format})


@mcp.tool()
async def set_material(
    object: str,
    color: list[float] | None = None,
    metallic: float | None = None,
    roughness: float | None = None,
    name: str | None = None,
) -> dict:
    """Apply a Principled-BSDF material to a named object.

    Args:
        object: Name of the target object in the scene.
        color: RGB or RGBA float list (0..1). Optional.
        metallic: 0..1. Optional.
        roughness: 0..1. Optional.
        name: Material name; defaults to "eos_<object>".
    """
    mat: dict[str, Any] = {}
    if color is not None:
        mat["color"] = color
    if metallic is not None:
        mat["metallic"] = metallic
    if roughness is not None:
        mat["roughness"] = roughness
    if name:
        mat["name"] = name
    return await _rpc("set_material", {"object": object, "material": mat})


@mcp.tool()
async def viewport_screenshot(
    path: str = "", width: int = 0, height: int = 0
) -> dict:
    """Capture the active viewport (or camera POV when headless) to a PNG.

    Fast OpenGL render — much cheaper than a full Cycles/EEVEE pass. Use this
    for visual feedback loops; use `render` for final-quality output.

    Args:
        path: Absolute output path. Defaults to a tempdir file.
        width: Override scene resolution_x. 0 = keep scene setting.
        height: Override scene resolution_y. 0 = keep scene setting.
    """
    params: dict = {}
    if path:
        params["path"] = path
    if width:
        params["width"] = width
    if height:
        params["height"] = height
    return await _rpc("viewport_screenshot", params)


@mcp.tool()
async def render(width: int = 1024, height: int = 1024) -> dict:
    """Render the current scene to a PNG and return the output path.

    The scene must already be set up (camera, lights, geometry) — this does not
    generate content from a prompt; it renders what's loaded in Blender.
    """
    return await _rpc("render_scene", {"width": width, "height": height})


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
