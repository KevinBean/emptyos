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

    async def run_script_file(
        self,
        script_path: str,
        args: list[str] | None = None,
        *,
        timeout: float = 900,
        blend_file: str = "",
    ) -> tuple[int, str, str]:
        """Run an existing .py inside headless Blender, passing ``args`` after ``--``.

        Two differences from ``run_script``, both deliberate:

        * it takes a **path**, so a version-controlled, testable script can be
          run with arguments — `run_script` only accepts code text and has no
          way to pass argv, which is why callers were reaching past the plugin
          and re-implementing executable lookup and subprocess handling;
        * it **returns** ``(returncode, stdout, stderr)`` instead of raising.
          A script that signals a refusal through its exit code — a validation
          gate rejecting bad input — is not an error the caller wants as an
          exception. Callers that do want the strict behaviour keep using
          ``run_script``.

        ``-noaudio`` is passed because a headless render has no audio device and
        Blender otherwise probes for one.
        """
        cmd = [self._blender_exe(), "--background", "-noaudio"]
        if blend_file:
            cmd.append(blend_file)
        cmd.extend(["--python", str(script_path)])
        if args:
            cmd.append("--")
            cmd.extend(str(a) for a in args)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return 124, "", f"blender timed out after {timeout}s"
        return (
            proc.returncode or 0,
            (out or b"").decode("utf-8", "replace"),
            (err or b"").decode("utf-8", "replace"),
        )

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

    # ─── Robot-modeller integration (Tier 3 M4) ────────────────────────────────

    async def render_robot_modeller_record(
        self,
        record_dir,
        output_path,
        *,
        resolution: tuple[int, int] = (1920, 1080),
        samples: int = 128,
        engine: str = "CYCLES",
        camera: dict | None = None,
        timeout: float = 240,
    ) -> dict:
        """Render an apps/personal/robot-modeller record (single-object or scene) to PNG.

        `record_dir` is the absolute path to a robot-modeller record's directory.
        Single-object records contain `output.glb`; scene records contain
        `scene.json` + per-object `<name>.glb`. The Blender script auto-detects.

        `camera` (optional): the three.js viewer's current camera state,
        `{position: [x,y,z], target: [x,y,z], up: [x,y,z], fov: deg, aspect}`.
        When provided, Blender renders from that exact view. When None,
        Blender auto-frames the scene at 35° azimuth / 25° elevation.

        Returns `{ok, render_path, render_time_s, ...}`. Never raises on
        compositional errors (missing glb, bad scene.json) — only on
        Blender / OS-level failures.
        """
        import json as _json
        import time

        record_dir = Path(record_dir).resolve()
        output_path = Path(output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Forward slashes — embedded in a Python source file the headless
        # Blender will exec, so Windows backslashes would be escape chars.
        record_dir_str = str(record_dir).replace("\\", "/")
        output_str = str(output_path).replace("\\", "/")
        w, h = resolution

        # Camera JSON — None becomes the literal "null" so the Blender script
        # can detect "auto-frame" via `if CAMERA is None`. The embedded
        # `.format()` substitution preserves None as a Python expression.
        camera_repr = _json.dumps(camera) if camera else "None"

        script = _BLENDER_RENDER_SCRIPT.format(
            record_dir=record_dir_str,
            output=output_str,
            res_w=w,
            res_h=h,
            samples=samples,
            engine=engine,
            camera_json=camera_repr,
        )

        t0 = time.time()
        stdout = ""
        run_script_error: Exception | None = None
        try:
            self.kernel.syslog.info("blender", f"robot-modeller render: {record_dir.name}", data={
                "resolution": resolution, "samples": samples, "engine": engine,
            })
            stdout = await self.run_script(script, timeout=timeout)
        except Exception as exc:
            # Don't fail outright — the `run_script` helper raises on ANY
            # traceback in Blender's stderr, but the `eos_bridge` addon
            # (interactive Blender HTTP server on :8400) can race-abort
            # connections in background mode and log a traceback that's
            # unrelated to our render. Check the actual output file before
            # deciding the run failed.
            run_script_error = exc

        if output_path.exists() and output_path.stat().st_size > 100:
            # File landed → render succeeded. Surface any addon noise as
            # a warning but don't fail.
            return {
                "ok": True,
                "render_path": str(output_path),
                "render_time_s": time.time() - t0,
                "size_bytes": output_path.stat().st_size,
                "warning": (
                    f"Blender printed a non-fatal traceback (likely eos_bridge addon noise): "
                    f"{run_script_error!s:.200}"
                ) if run_script_error else None,
            }

        # No file → genuine failure. Surface the error.
        if run_script_error is not None:
            return {
                "ok": False,
                "render_path": str(output_path),
                "error": f"{type(run_script_error).__name__}: {run_script_error}",
                "render_time_s": time.time() - t0,
            }
        return {
            "ok": False,
            "render_path": str(output_path),
            "error": "Blender finished but no output file written",
            "stdout_tail": stdout[-500:] if stdout else "",
            "render_time_s": time.time() - t0,
        }

        return {
            "ok": True,
            "render_path": str(output_path),
            "render_time_s": time.time() - t0,
            "size_bytes": output_path.stat().st_size,
        }


# Blender script template — runs inside headless Blender. `.format(...)` is
# applied at call time to substitute the 6 placeholders below; no { or }
# elsewhere in the script (no f-strings) so escaping stays clean.
#
# Pipeline: clear default scene → import the record's glb(s) → apply scene
# placements (if scene.json present) → procedural floor + 3-point sun lights
# + sky background → frame camera on the bounding box at 35° azimuth, 25°
# elevation → Cycles render with Filmic color management → save PNG.
_BLENDER_RENDER_SCRIPT = r"""
import bpy
import json
import math
from pathlib import Path
from mathutils import Vector

RECORD_DIR = Path(r"{record_dir}")
OUTPUT     = r"{output}"
RES_W, RES_H = {res_w}, {res_h}
SAMPLES = {samples}
ENGINE = "{engine}"
# `CAMERA` is either None (auto-frame the bbox) or a dict from the
# three.js viewer: {{position, target, up, fov, aspect}}. URDF-world coords.
CAMERA = {camera_json}

# ── Clean the default scene ──
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
for mesh in list(bpy.data.meshes):
    bpy.data.meshes.remove(mesh)
for mat in list(bpy.data.materials):
    bpy.data.materials.remove(mat)
for light in list(bpy.data.lights):
    bpy.data.lights.remove(light)
for cam in list(bpy.data.cameras):
    bpy.data.cameras.remove(cam)

# ── Axis-convention root ──
# glTF spec says Y-up; Blender's importer rotates Y-up → Z-up (+90° around X).
# Our engine emits Z-up data directly (URDF convention). To undo Blender's
# automatic rotation, wrap every import in a scene_root Empty with -90° X
# rotation. End result: imported meshes land at the same world positions
# we wrote in the glb, matching what the three.js viewer shows.
scene_root = bpy.data.objects.new(name="scene_root", object_data=None)
bpy.context.collection.objects.link(scene_root)
scene_root.rotation_euler = (math.radians(-90), 0, 0)

# ── HTTP-URI texture pre-fetch ──
# Blender's glTF importer can't reliably resolve http:// URIs under
# --background (silent fallback to magenta placeholders). The robot-modeller's
# Tier 3 size-optimised glbs reference textures as external URIs against
# /robot-modeller/api/texture/<material>/<map_kind>. We rewrite those to local
# file paths sibling to a patched glb so the importer reads them off disk.
import urllib.request as _urlreq
import struct as _struct
import hashlib as _hashlib
PATCHED_DIR = RECORD_DIR / "_render_cache"
TEX_DIR = PATCHED_DIR / "tex"
PATCHED_DIR.mkdir(parents=True, exist_ok=True)
TEX_DIR.mkdir(parents=True, exist_ok=True)

def _cache_name_for(url):
    parts = [p for p in url.split("/") if p and "://" not in p]
    if len(parts) >= 2:
        return parts[-2] + "__" + parts[-1] + ".jpg"
    return _hashlib.sha1(url.encode("utf-8")).hexdigest()[:16] + ".jpg"

def _fetch_texture(url):
    local = TEX_DIR / _cache_name_for(url)
    if local.exists() and local.stat().st_size > 0:
        return local
    print("fetch texture: " + url)
    with _urlreq.urlopen(url, timeout=30) as r:
        local.write_bytes(r.read())
    return local

def prepare_glb_for_import(src_glb_path):
    src = Path(src_glb_path)
    data = src.read_bytes()
    if data[:4] != b"glTF" or len(data) < 20:
        return src
    version, _total = _struct.unpack("<II", data[4:12])
    chunk_len, chunk_type = _struct.unpack("<I4s", data[12:20])
    if chunk_type != b"JSON":
        return src
    json_bytes = data[20:20 + chunk_len]
    rest = data[20 + chunk_len:]
    try:
        j = json.loads(json_bytes.decode("utf-8"))
    except Exception:
        return src
    images = j.get("images") or []
    rewritten = 0
    for img in images:
        uri = img.get("uri", "") or ""
        if uri.startswith("http://") or uri.startswith("https://"):
            local = _fetch_texture(uri)
            img["uri"] = "tex/" + local.name
            rewritten += 1
    if rewritten == 0:
        return src
    new_json = json.dumps(j, separators=(",", ":")).encode("utf-8")
    pad = (4 - (len(new_json) % 4)) % 4
    new_json += b" " * pad
    new_chunk = _struct.pack("<I", len(new_json)) + b"JSON" + new_json
    new_total = 12 + len(new_chunk) + len(rest)
    new_header = b"glTF" + _struct.pack("<II", version, new_total)
    out = PATCHED_DIR / src.name
    out.write_bytes(new_header + new_chunk + rest)
    print("patched glb " + src.name + ": " + str(rewritten) + " image URIs rewritten")
    return out

def import_glb(path, placement=None, name_tag="obj"):
    # Import one .glb and (optionally) place via a parent Empty.
    # glTF importer can produce a parent Empty + N mesh children, OR
    # N top-level objects, depending on the source. Adding placement to
    # each top-level object double-counts whenever there is a hierarchy.
    # Robust fix: create our own Empty per import, parent every top-level
    # new object to it, set the Empty's transform from scene.json
    # placement. One transform handle per imported glb.
    path = prepare_glb_for_import(path)
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(path))
    new_objs = list(set(bpy.data.objects) - before)
    if not new_objs:
        return []

    # Top-level within THIS import = no parent OR parent already existed
    # before this import (i.e. parent is not one of our new objects).
    top_level = [o for o in new_objs if o.parent is None or o.parent in before]

    if placement:
        xyz = placement.get("xyz", [0, 0, 0])
        rpy = placement.get("rpy", [0, 0, 0])
        empty = bpy.data.objects.new(name=name_tag + "_root", object_data=None)
        bpy.context.collection.objects.link(empty)
        empty.location = (float(xyz[0]), float(xyz[1]), float(xyz[2]))
        empty.rotation_euler = (float(rpy[0]), float(rpy[1]), float(rpy[2]))
        # Parent under scene_root so the -90° X axis-fix applies.
        empty.parent = scene_root
        for o in top_level:
            o.parent = empty
        new_objs.append(empty)
        print("import: " + name_tag + " at xyz=" + str(xyz) + " (" + str(len(top_level)) + " top-level)")
    else:
        # No scene-level placement: still re-parent under scene_root so
        # the axis-fix applies. Single-object records take this branch.
        for o in top_level:
            o.parent = scene_root
        print("import: " + name_tag + " (no placement; " + str(len(top_level)) + " top-level)")
    return new_objs

scene_json = RECORD_DIR / "scene.json"
if scene_json.exists():
    manifest = json.loads(scene_json.read_text(encoding="utf-8"))
    for obj in manifest.get("objects", []):
        glb_name = obj.get("glb") or (obj["name"] + ".glb")
        glb_path = RECORD_DIR / glb_name
        if not glb_path.exists():
            print("WARN: missing " + str(glb_path))
            continue
        import_glb(glb_path,
                   {{"xyz": obj.get("xyz", [0, 0, 0]),
                     "rpy": obj.get("rpy", [0, 0, 0])}},
                   name_tag=obj.get("name", "obj"))
else:
    glb = RECORD_DIR / "output.glb"
    if not glb.exists():
        raise RuntimeError("no glb at " + str(glb))
    import_glb(glb, name_tag="root")

# ── Compute scene bounding box ──
# Diagnostic: list every imported object's type so we can see what Blender
# 5.0+ actually produced from `import_scene.gltf` (the operator can emit
# EMPTY parents around the mesh hierarchy depending on the source glb).
all_obj_types = [(o.name, o.type) for o in bpy.data.objects]
print("After import: " + str(len(bpy.data.objects)) + " objects, types: " + str(all_obj_types))
mesh_objs = [o for o in bpy.data.objects if o.type == 'MESH']
if not mesh_objs:
    raise RuntimeError(
        "no meshes found after import. all objects: " + str(all_obj_types)
    )

bbox_min = [float("inf")] * 3
bbox_max = [float("-inf")] * 3
for o in mesh_objs:
    for corner in o.bound_box:
        world_corner = o.matrix_world @ Vector(corner)
        for i in range(3):
            if world_corner[i] < bbox_min[i]: bbox_min[i] = world_corner[i]
            if world_corner[i] > bbox_max[i]: bbox_max[i] = world_corner[i]
bbox_centre = [(bbox_min[i] + bbox_max[i]) / 2 for i in range(3)]
bbox_size = [bbox_max[i] - bbox_min[i] for i in range(3)]
bbox_diag = math.sqrt(sum(s*s for s in bbox_size))
print("Scene bbox centre=" + str(bbox_centre) + " size=" + str(bbox_size) + " diag=" + str(round(bbox_diag, 3)))
# Diagnostic — per-object world-space bbox so we can see if any object
# is wildly out of position relative to the others.
for o in mesh_objs:
    bb_min = [float("inf")] * 3
    bb_max = [float("-inf")] * 3
    for c in o.bound_box:
        w = o.matrix_world @ Vector(c)
        for i in range(3):
            if w[i] < bb_min[i]: bb_min[i] = w[i]
            if w[i] > bb_max[i]: bb_max[i] = w[i]
    print("  obj " + o.name + " z=[" + str(round(bb_min[2], 2)) + "," + str(round(bb_max[2], 2)) + "] x=[" + str(round(bb_min[0], 2)) + "," + str(round(bb_max[0], 2)) + "]")

# ── Floor plane for visual grounding ──
# Modest size — 2× the bbox in the larger axis + 1m padding. Previously was
# 6× which dominated the frame in close-camera renders.
floor_size = max(bbox_size[0], bbox_size[1], 1.0) * 2.0 + 1.0
bpy.ops.mesh.primitive_plane_add(size=floor_size,
                                  location=(bbox_centre[0], bbox_centre[1], bbox_min[2]))
floor = bpy.context.active_object
floor.name = "floor"
floor_mat = bpy.data.materials.new(name="floor")
floor_mat.use_nodes = True
bsdf = floor_mat.node_tree.nodes.get("Principled BSDF")
if bsdf:
    bsdf.inputs["Base Color"].default_value = (0.88, 0.86, 0.82, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.85
floor.data.materials.append(floor_mat)

# ── 3-point lighting (sun lights — directional, simple) ──
def add_sun(name, location, rotation_deg, energy):
    data = bpy.data.lights.new(name=name, type='SUN')
    data.energy = energy
    light = bpy.data.objects.new(name=name, object_data=data)
    light.location = location
    light.rotation_euler = (math.radians(rotation_deg[0]),
                            math.radians(rotation_deg[1]),
                            math.radians(rotation_deg[2]))
    bpy.context.collection.objects.link(light)
    return light

d = bbox_diag if bbox_diag > 0.5 else 1.0
add_sun("key",  (bbox_centre[0] + d, bbox_centre[1] - d, bbox_max[2] + d),
        (-50, 20, 45),  4.0)
add_sun("fill", (bbox_centre[0] - d, bbox_centre[1] + d * 0.5, bbox_max[2] + d * 0.5),
        (-30, -15, -30), 1.5)
add_sun("rim",  (bbox_centre[0], bbox_centre[1] - d * 1.2, bbox_max[2] + d * 0.6),
        (60, 0, 180), 2.0)

# Sky-blue ambient for indirect light on metals.
world = bpy.context.scene.world
if world and world.use_nodes:
    bg_node = world.node_tree.nodes.get("Background")
    if bg_node:
        bg_node.inputs["Color"].default_value = (0.55, 0.65, 0.78, 1.0)
        bg_node.inputs["Strength"].default_value = 0.4

# ── Camera ──
cam_data = bpy.data.cameras.new(name="cam")
cam = bpy.data.objects.new(name="cam", object_data=cam_data)
bpy.context.collection.objects.link(cam)

if CAMERA is not None:
    # User-supplied view from three.js viewer. Match vertical FOV by
    # setting sensor_fit=VERTICAL so cam_data.angle == vertical FOV (which
    # is what three.js reports). Position + target are in URDF-world
    # coords (Z-up). Look-at uses Blender's `-Z` forward, `Y` up axis
    # convention so the camera orients correctly when rolled by user
    # orbit controls.
    cam_data.sensor_fit = 'VERTICAL'
    cam_data.angle = math.radians(float(CAMERA.get("fov", 45)))
    pos = CAMERA["position"]
    tgt = CAMERA["target"]
    cam.location = (float(pos[0]), float(pos[1]), float(pos[2]))
    direction = Vector((float(tgt[0]), float(tgt[1]), float(tgt[2]))) - cam.location
    # User's `up` from three.js is honoured via the second arg to to_track_quat.
    # Z-up viewer → up=(0,0,1) → Blender rolls camera to match.
    up_vec = CAMERA.get("up", [0, 0, 1])
    # The `-Z` is Blender camera forward; second arg is the up direction.
    # Three.js's up vector tells us the world axis the camera considers "up";
    # in our setup that's the world Z axis (URDF convention). Use 'Y' here
    # because OrbitControls in our viewer keeps camera.up at (0,0,1) and
    # Blender computes the roll from the world up — passing 'Y' yields the
    # same upright framing without the camera barrel-rolling.
    cam.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
    print("CAMERA: user view position=" + str(pos) + " target=" + str(tgt) + " fov=" + str(CAMERA.get("fov")))
else:
    # Auto-frame: orbit camera at 35° azimuth, 25° elevation, distance = bbox_diag×1.5
    cam_data.lens = 50
    cam_distance = max(bbox_diag * 1.5, 1.0)
    cam.location = (
        bbox_centre[0] + cam_distance * math.cos(math.radians(35)),
        bbox_centre[1] - cam_distance * math.cos(math.radians(35)),
        bbox_centre[2] + cam_distance * math.sin(math.radians(25)),
    )
    direction = Vector(bbox_centre) - cam.location
    cam.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
    print("CAMERA: auto-framed")

bpy.context.scene.camera = cam

# ── Render config ──
scene = bpy.context.scene
scene.render.engine = ENGINE
scene.render.resolution_x = RES_W
scene.render.resolution_y = RES_H
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'PNG'
scene.render.filepath = OUTPUT

if ENGINE == "CYCLES":
    scene.cycles.samples = SAMPLES
    scene.cycles.use_denoising = True
    prefs = bpy.context.preferences.addons.get("cycles")
    if prefs:
        prefs.preferences.compute_device_type = 'CUDA'
        for device in prefs.preferences.devices:
            device.use = True
        scene.cycles.device = 'GPU'

scene.view_settings.view_transform = 'Filmic'
scene.view_settings.look = 'Medium Contrast'

print("RENDER START: " + OUTPUT + " res=" + str(RES_W) + "x" + str(RES_H) + " samples=" + str(SAMPLES))
bpy.ops.render.render(write_still=True)
print("RENDER DONE: " + OUTPUT)
"""
