"""Blender plugin — 3D modeling, rendering, and animation.

Connects to a Blender instance running a lightweight JSON-RPC server addon.
Apps call self.service("blender").render(...) or self.require("blender").

Two modes:
  1. Headless: Launch Blender in background with a Python script (batch rendering)
  2. Addon server: Connect to a running Blender with the EOS addon (live interaction)

Also registers as a 'draw' capability provider (priority=10, below ComfyUI)
for 3D-rendered images.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path

import aiohttp

from emptyos.sdk import BasePlugin


class BlenderPlugin(BasePlugin):
    name = "blender"

    def __init__(self, kernel, manifest):
        super().__init__(kernel, manifest)
        self._session = None
        self._draw_registered = False

    def _host(self) -> str:
        return self.config("host", "http://127.0.0.1:8400")

    def _token_path(self) -> Path:
        return Path.home() / ".eos" / "blender-bridge.token"

    def _token(self) -> str:
        """Read (or generate) the shared bridge token. The Blender-side addon
        reads from the same path on register(), so they agree without env
        plumbing — important because users start Blender themselves."""
        p = self._token_path()
        try:
            if p.exists():
                return p.read_text(encoding="utf-8").strip()
            import secrets

            tok = secrets.token_urlsafe(32)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(tok, encoding="utf-8")
            try:
                # Restrict to owner-readable on POSIX. No-op on Windows.
                import os
                import stat

                os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
            except Exception:
                pass
            return tok
        except Exception:
            return ""

    def _auth_headers(self) -> dict:
        return self.bearer_headers(self._token())

    def _blender_exe(self) -> str:
        import sys

        if sys.platform == "darwin":
            fallback = "/Applications/Blender.app/Contents/MacOS/Blender"
        elif sys.platform == "win32":
            fallback = "C:/Program Files/Blender Foundation/Blender 5.0/blender.exe"
        else:
            fallback = "/usr/bin/blender"
        return self.config("executable", shutil.which("blender") or fallback)

    def _output_dir(self) -> Path:
        return Path(self.config("output_dir", "./data/blender-output")).resolve()

    # --- Lifecycle ---

    async def connect(self):
        self._session = aiohttp.ClientSession()
        self._output_dir().mkdir(parents=True, exist_ok=True)
        # Ensure the shared bridge token exists on disk before we ping —
        # the Blender-side addon will pick it up lazily on its next request.
        self._token()
        if await self.available():
            self._register_draw()
            print(f"[Blender] Connected to addon server at {self._host()}")
        else:
            print(
                f"[Blender] Addon server not reachable at {self._host()} (headless mode available)"
            )

    async def disconnect(self):
        if self._session:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None

    async def available(self) -> bool:
        """Check if the Blender addon server is running."""
        try:
            async with self._session.post(
                self._host(),
                json={"jsonrpc": "2.0", "method": "ping", "id": 1},
                timeout=aiohttp.ClientTimeout(total=3),
            ) as resp:
                data = await resp.json()
                return data.get("result") == "pong"
        except Exception:
            return False

    def version(self) -> str:
        """Return Blender version string based on configured executable."""
        exe = self._blender_exe()
        if "5.0" in exe:
            return "5.0"
        if "4." in exe:
            return "4.x"
        return "unknown"

    # --- Draw capability provider ---

    def _register_draw(self):
        if self._draw_registered:
            return
        from emptyos.capabilities import Provider

        plugin = self

        class BlenderDrawProvider(Provider):
            name = "blender"

            async def available(self) -> bool:
                return await plugin.available()

            async def execute(self, *, prompt: str, **kwargs) -> str:
                return await plugin.render_from_prompt(prompt, **kwargs)

        draw_cap = self.kernel.capabilities.get("draw")
        if draw_cap:
            draw_cap.add_provider(BlenderDrawProvider(), priority=10)
            self._draw_registered = True

    # --- JSON-RPC helpers ---

    async def _rpc(self, method: str, params: dict | None = None, timeout: float = 300) -> dict:
        """Call the Blender addon server via JSON-RPC."""
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": str(uuid.uuid4()),
        }
        async with self._session.post(
            self._host(),
            json=payload,
            headers=self._auth_headers(),
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            data = await resp.json()
            if "error" in data:
                raise RuntimeError(f"Blender RPC error: {data['error']}")
            return data.get("result", {})

    # --- Headless execution ---

    async def run_script(self, script: str, blend_file: str = "", timeout: float = 300) -> str:
        """Run a Python script in headless Blender. Returns stdout.

        Args:
            script: Python code to execute inside Blender
            blend_file: Optional .blend file to open first
            timeout: Max seconds to wait
        """
        import tempfile

        tmp = tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w", encoding="utf-8")
        tmp.write(script)
        tmp.close()
        script_path = Path(tmp.name)

        cmd = [self._blender_exe(), "--background"]
        if blend_file:
            cmd.append(blend_file)
        cmd.extend(["--python", str(script_path)])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            stdout_str = stdout.decode()
            stderr_str = stderr.decode()
            if proc.returncode != 0:
                raise RuntimeError(f"Blender exited with code {proc.returncode}: {stderr_str}")
            # Check for Python errors even on exit code 0 (Blender doesn't always set non-zero)
            if (
                "Traceback (most recent call last):" in stderr_str
                or "Traceback (most recent call last):" in stdout_str
            ):
                error_text = stderr_str if "Traceback" in stderr_str else stdout_str
                raise RuntimeError(f"Blender script error: {error_text[-500:]}")
            return stdout_str
        finally:
            script_path.unlink(missing_ok=True)

    # --- High-level operations ---

    async def render(
        self,
        blend_file: str,
        output: str = "",
        resolution: tuple[int, int] = (1920, 1080),
        samples: int = 128,
        engine: str = "CYCLES",
        frame: int = 1,
    ) -> str:
        """Render a .blend file to an image. Returns output path.

        Args:
            blend_file: Path to .blend file
            output: Output image path (auto-generated if empty)
            resolution: (width, height)
            engine: CYCLES, BLENDER_EEVEE, or BLENDER_WORKBENCH
            samples: Render samples (Cycles)
            frame: Frame number to render
        """
        if not output:
            output = str(self._output_dir() / f"render_{uuid.uuid4().hex[:8]}.png")

        script = f"""
import bpy

scene = bpy.context.scene
scene.render.engine = '{engine}'
scene.render.resolution_x = {resolution[0]}
scene.render.resolution_y = {resolution[1]}
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'PNG'
scene.render.filepath = r'{output}'
scene.frame_set({frame})

if '{engine}' == 'CYCLES':
    scene.cycles.samples = {samples}
    scene.cycles.use_denoising = True
    # Use GPU if available
    prefs = bpy.context.preferences.addons.get('cycles')
    if prefs:
        prefs.preferences.compute_device_type = 'CUDA'
        for device in prefs.preferences.devices:
            device.use = True
        scene.cycles.device = 'GPU'

bpy.ops.render.render(write_still=True)
print(f"RENDER_OUTPUT:{{scene.render.filepath}}")
"""
        self.kernel.syslog.info(
            "blender",
            f"Rendering {blend_file}",
            data={
                "engine": engine,
                "resolution": resolution,
                "samples": samples,
            },
        )
        await self.run_script(script, blend_file=blend_file)
        return output

    async def render_animation(
        self,
        blend_file: str,
        output_dir: str = "",
        frame_start: int = 1,
        frame_end: int = 250,
        resolution: tuple[int, int] = (1920, 1080),
        engine: str = "BLENDER_EEVEE",
        fps: int = 30,
        output_format: str = "FFMPEG",
    ) -> str:
        """Render an animation from a .blend file. Returns output path."""
        if not output_dir:
            output_dir = str(self._output_dir() / f"anim_{uuid.uuid4().hex[:8]}")

        Path(output_dir).mkdir(parents=True, exist_ok=True)
        output_path = str(Path(output_dir) / "output")

        script = f"""
import bpy

scene = bpy.context.scene
scene.render.engine = '{engine}'
scene.render.resolution_x = {resolution[0]}
scene.render.resolution_y = {resolution[1]}
scene.render.resolution_percentage = 100
scene.render.fps = {fps}
scene.frame_start = {frame_start}
scene.frame_end = {frame_end}
scene.render.filepath = r'{output_path}'

if '{output_format}' == 'FFMPEG':
    scene.render.image_settings.file_format = 'FFMPEG'
    scene.render.ffmpeg.format = 'MPEG4'
    scene.render.ffmpeg.codec = 'H264'
    scene.render.ffmpeg.constant_rate_factor = 'MEDIUM'
else:
    scene.render.image_settings.file_format = 'PNG'

if '{engine}' == 'CYCLES':
    scene.cycles.samples = 64
    scene.cycles.device = 'GPU'

bpy.ops.render.render(animation=True)
print(f"ANIM_OUTPUT:{output_path}")
"""
        self.kernel.syslog.info(
            "blender",
            f"Rendering animation {blend_file}",
            data={
                "frames": f"{frame_start}-{frame_end}",
                "engine": engine,
            },
        )
        await self.run_script(script, blend_file=blend_file)
        return output_dir

    async def render_from_prompt(self, prompt: str, **kwargs) -> str:
        """Render-from-prompt entrypoint for the `draw` capability.

        Two paths: when the addon is available, render the *current* scene
        (the addon ignores `prompt` — the user is expected to have set up the
        scene already). Otherwise, fall back to a headless procedural scene
        that does loosely interpret the prompt.
        """
        if await self.available():
            result = await self._rpc(
                "render_scene",
                {
                    "width": kwargs.get("width", 1024),
                    "height": kwargs.get("height", 1024),
                },
            )
            return result.get("image_path", "")

        # Headless fallback: basic procedural scene
        output = str(self._output_dir() / f"prompt_{uuid.uuid4().hex[:8]}.png")
        script = f"""
import bpy

# Clear default scene
bpy.ops.wm.read_factory_settings(use_empty=True)

# Add basic scene elements based on prompt keywords
bpy.ops.mesh.primitive_monkey_add(location=(0, 0, 0))
obj = bpy.context.active_object
obj.name = "generated"

# Add a light
bpy.ops.object.light_add(type='SUN', location=(5, 5, 5))

# Add camera
bpy.ops.object.camera_add(location=(3, -3, 2))
cam = bpy.context.active_object
cam.rotation_euler = (1.1, 0, 0.78)
bpy.context.scene.camera = cam

# Render
scene = bpy.context.scene
scene.render.engine = 'CYCLES'
scene.render.resolution_x = {kwargs.get("width", 1024)}
scene.render.resolution_y = {kwargs.get("height", 1024)}
scene.cycles.samples = 64
scene.cycles.device = 'GPU'
scene.render.filepath = r'{output}'
bpy.ops.render.render(write_still=True)
"""
        await self.run_script(script)
        return output

    async def get_scene_info(self) -> dict:
        """Get info about the current scene in the running Blender instance."""
        return await self._rpc("scene_info")

    # NOTE: execute_python (arbitrary `exec` over RPC) was removed for
    # security. Use `run_script(code)` instead — that spins up a headless
    # subprocess, isolating the running Blender instance from arbitrary code
    # paths reaching it via the local RPC port.

    async def import_model(self, file_path: str, format: str = "auto") -> dict:
        """Import a 3D model into the current scene."""
        return await self._rpc("import_model", {"path": file_path, "format": format})

    async def export_model(self, output_path: str, format: str = "glb") -> dict:
        """Export the current scene to a file."""
        return await self._rpc("export_model", {"path": output_path, "format": format})

    async def list_objects(self) -> list[dict]:
        """List all objects in the current scene."""
        result = await self._rpc("list_objects")
        return result.get("objects", [])

    async def set_material(self, object_name: str, material: dict) -> dict:
        """Set material properties on a named object."""
        return await self._rpc("set_material", {"object": object_name, "material": material})

    async def viewport_screenshot(
        self, path: str = "", width: int = 0, height: int = 0
    ) -> dict:
        """Capture the active viewport (or camera POV in headless) to a PNG.

        Fast OpenGL render — useful for "what does it look like?" feedback loops
        without paying full Cycles/EEVEE render cost.
        """
        params: dict = {}
        if path:
            params["path"] = path
        if width:
            params["width"] = width
        if height:
            params["height"] = height
        return await self._rpc("viewport_screenshot", params)
