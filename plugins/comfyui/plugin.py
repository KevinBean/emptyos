"""ComfyUI plugin — GPU image generation.

Connects to ComfyUI server for image generation workflows.
Also registers as a 'draw' capability provider.

Absorbed from AI Phone Agent's comfyui_api.py — supports FLUX, SDXL, SD1.5
checkpoints, LoRA, style presets with per-style params, GPU memory freeing.
"""

from __future__ import annotations

import json
import random

import aiohttp

from emptyos.sdk import BasePlugin

# --- Style Presets (from AI Phone Agent) ---
STYLE_PRESETS = {
    "photo": {
        "label": "Photorealistic",
        "checkpoint": "RealVisXL_V4.0.safetensors",
        "sampler": "dpmpp_2m_sde",
        "scheduler": "karras",
        "cfg": 5.0,
        "steps": 25,
        "prefix": "RAW photo, ",
        "suffix": ", 8k uhd, DSLR, film grain, Fujifilm XT3",
        "negative": "cartoon, anime, drawing, painting, illustration, cgi, 3d render",
    },
    "anime": {
        "label": "Anime",
        "checkpoint": "animagine-xl-3.1.safetensors",
        "sampler": "euler_ancestral",
        "scheduler": "normal",
        "cfg": 7.0,
        "steps": 28,
        "prefix": "masterpiece, best quality, ",
        "suffix": ", anime style, vibrant colors",
        "negative": "lowres, bad anatomy, bad hands, worst quality, low quality, photo, realistic",
    },
    "comic": {
        "label": "Comic Book",
        "lora": "Eldritch_Comics_for_Flux",
        "lora_strength": 0.85,
        "prefix": "comic book art, bold outlines, ",
        "suffix": ", graphic novel style, dynamic composition, vibrant colors",
    },
    "illustration": {
        "label": "Illustration",
        "lora": "FLUX-dev-lora-blended_realistic_illustration",
        "lora_strength": 0.7,
        "prefix": "detailed illustration, ",
        "suffix": ", semi-realistic, artstation, beautiful lighting",
    },
    "dream": {
        "label": "Dreamlike",
        "checkpoint": "dreamshaper_8.safetensors",
        "sampler": "dpmpp_sde",
        "scheduler": "karras",
        "cfg": 7.0,
        "steps": 30,
        "prefix": "dreamlike, ethereal, ",
        "suffix": ", fantasy art, magical atmosphere, soft glow",
        "negative": "ugly, blurry, low quality",
    },
    "cinematic": {
        "label": "Cinematic",
        "prefix": "cinematic still, ",
        "suffix": ", dramatic lighting, film grain, anamorphic lens",
    },
    "portrait": {
        "label": "Portrait",
        "checkpoint": "RealVisXL_V4.0.safetensors",
        "sampler": "dpmpp_2m_sde",
        "scheduler": "karras",
        "cfg": 5.0,
        "steps": 25,
        "prefix": "portrait photo, ",
        "suffix": ", shallow depth of field, natural lighting, 85mm lens, bokeh",
        "negative": "cartoon, anime, drawing, deformed",
        "width": 768,
        "height": 1024,
    },
    "minimalist": {
        "label": "Minimalist",
        "prefix": "",
        "suffix": ", minimalist, clean, simple shapes, flat design, modern",
    },
}


class ComfyUIPlugin(BasePlugin):
    name = "comfyui"

    def __init__(self, kernel, manifest):
        super().__init__(kernel, manifest)
        self._session = None

    def _host(self) -> str:
        return self.config("host", "http://localhost:8188")

    async def connect(self):
        self._session = aiohttp.ClientSession()
        self._draw_registered = False
        if await self.available():
            self._register_draw()
            print(f"[ComfyUI] Connected to {self._host()}")
        else:
            print(f"[ComfyUI] Not reachable at {self._host()} (use ensure_available to auto-start)")

    def _register_draw(self):
        if self._draw_registered:
            return
        from emptyos.capabilities import Provider

        plugin = self

        class ComfyUIDrawProvider(Provider):
            name = "comfyui"

            async def available(self) -> bool:
                return await plugin.available()

            async def health(self) -> dict:
                if await plugin.available():
                    return {"available": True, "reason": None, "recovery": None}
                return {
                    "available": False,
                    "reason": f"ComfyUI service unreachable at {plugin._host()}",
                    "recovery": {
                        "kind": "service",
                        "id": "comfyui",
                        "url": plugin._host(),
                        "hint": "Set [plugins.comfyui] launcher in emptyos.toml, then restart",
                    },
                }

            async def execute(self, *, prompt: str, **kwargs) -> str:
                return await plugin.generate(prompt, **kwargs)

        draw_cap = self.kernel.capabilities.get("draw")
        draw_cap.add_provider(ComfyUIDrawProvider(), priority=0)
        self._draw_registered = True
        self._register_animate()

    def _register_animate(self):
        if getattr(self, "_animate_registered", False):
            return
        animate_cap = self.kernel.capabilities.get("animate")
        if animate_cap is None:
            return
        import tempfile
        from pathlib import Path

        from emptyos.capabilities import Provider

        plugin = self

        class ComfyUIAnimateProvider(Provider):
            name = "comfyui-ltx"

            async def available(self) -> bool:
                # ComfyUI uptime = animate is possible. Workflow availability
                # is checked at execute time; missing workflow returns "" and
                # callers fall back (e.g. visual.py to Ken Burns).
                return await plugin.available()

            async def health(self) -> dict:
                if await plugin.available():
                    return {"available": True, "reason": None, "recovery": None}
                return {
                    "available": False,
                    "reason": f"ComfyUI service unreachable at {plugin._host()}",
                    "recovery": {
                        "kind": "service",
                        "id": "comfyui",
                        "url": plugin._host(),
                        "hint": "Set [plugins.comfyui] launcher in emptyos.toml, then restart",
                    },
                }

            async def execute(
                self,
                *,
                prompt: str,
                image: str = "",
                num_frames: int = 24,
                dest: str = "",
                workflow: str = "video",
                **kwargs,
            ) -> str:
                # workflow="video" → reads [plugins.comfyui] video_workflow
                # workflow="parallax" → reads [plugins.comfyui] parallax_workflow
                # Any value works; the config key is `{workflow}_workflow`.
                config_key = f"{workflow}_workflow"
                tmpl = plugin.config(config_key, "")
                if not tmpl:
                    plugin.kernel.syslog.warning(
                        "comfyui",
                        f"animate({workflow}) called but [plugins.comfyui] {config_key} not configured",
                    )
                    return ""
                filename = await plugin.generate_from_workflow(
                    workflow_key=workflow,
                    prompt=prompt,
                    image_filename=image,
                    num_frames=num_frames,
                    width=int(kwargs.get("width", 768)),
                    height=int(kwargs.get("height", 432)),
                )
                if not filename:
                    return ""
                if dest:
                    dest_path = Path(dest)
                else:
                    suffix = Path(filename).suffix or ".mp4"
                    fd, tmp = tempfile.mkstemp(prefix="anim-", suffix=suffix)
                    import os

                    os.close(fd)
                    dest_path = Path(tmp)
                ok = await plugin.download_image(filename, dest_path)
                return str(dest_path) if ok else ""

        animate_cap.add_provider(ComfyUIAnimateProvider(), priority=0)
        self._animate_registered = True

    async def auto_start(self) -> bool:
        """Launch ComfyUI if not running. Returns True when ready."""
        if await self.available():
            return True
        import asyncio
        import subprocess
        from pathlib import Path

        launcher = self.config("launcher", "")
        if not launcher:
            print("[ComfyUI] No launcher configured (set comfyui.launcher in emptyos.toml)")
            return False
        launcher_path = Path(launcher)
        launcher_dir = str(launcher_path.parent)
        # Read the bat to find the actual python command, run it directly (no extra window)
        python_exe = str(launcher_path.parent / "python_embeded" / "python.exe")
        main_py = str(launcher_path.parent / "ComfyUI" / "main.py")
        try:
            # Fire-and-forget headless launch — Popen returns immediately.
            subprocess.Popen(  # noqa: ASYNC220
                [python_exe, "-s", main_py, "--windows-standalone-build"],
                cwd=launcher_dir,
                creationflags=subprocess.CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print("[ComfyUI] Starting...")
            for _ in range(30):  # wait up to 60s
                await asyncio.sleep(2)
                if await self.available():
                    self._register_draw()
                    print("[ComfyUI] Ready")
                    return True
            print("[ComfyUI] Timed out waiting for startup")
        except Exception as e:
            print(f"[ComfyUI] Failed to start: {e}")
        return False

    async def ensure_available(self) -> bool:
        """Check if ComfyUI is running, auto-start if not."""
        if await self.available():
            if not self._draw_registered:
                self._register_draw()
            return True
        return await self.auto_start()

    async def disconnect(self):
        if self._session:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None

    async def available(self) -> bool:
        try:
            async with self._session.get(
                f"{self._host()}/system_stats",
                timeout=aiohttp.ClientTimeout(total=3),
            ) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def health_check(self) -> bool:
        return await self.available()

    async def get_models(self) -> list[str]:
        try:
            async with self._session.get(
                f"{self._host()}/object_info/CheckpointLoaderSimple"
            ) as resp:
                data = await resp.json()
                return (
                    data.get("CheckpointLoaderSimple", {})
                    .get("input", {})
                    .get("required", {})
                    .get("ckpt_name", [[]])[0]
                )
        except Exception:
            return []

    async def get_image_url(self, filename: str) -> str:
        return f"{self._host()}/view?filename={filename}"

    async def generate_video(
        self,
        prompt: str,
        image_filename: str = "",
        num_frames: int = 97,
        seed: int = 0,
        template_path: str = "",
    ) -> str:
        """Convenience wrapper for the legacy video workflow path."""
        return await self.generate_from_workflow(
            workflow_key="video",
            prompt=prompt,
            image_filename=image_filename,
            num_frames=num_frames,
            seed=seed,
            template_path=template_path,
        )

    async def generate_from_workflow(
        self,
        workflow_key: str,
        prompt: str,
        image_filename: str = "",
        num_frames: int = 97,
        seed: int = 0,
        width: int = 768,
        height: int = 432,
        template_path: str = "",
    ) -> str:
        """Run any ComfyUI workflow from a JSON template, with placeholder
        substitution. Used for image-to-video (LTX-2 / Wan / SVD), depth
        parallax, and anything else that fits the "image in, mp4 out" shape.

        Resolves the template path from ``[plugins.comfyui] {workflow_key}_workflow``
        unless ``template_path`` is given. Any string value in the JSON
        containing ``{prompt}`` / ``{image}`` / ``{seed}`` / ``{frames}`` is
        substituted. Returns the first video/gif/image filename produced,
        or "" on failure.
        """
        import asyncio
        import copy
        from pathlib import Path

        path_str = template_path or self.config(f"{workflow_key}_workflow", "")
        if not path_str:
            return ""
        tmpl_path = Path(path_str)
        if not tmpl_path.is_absolute():
            tmpl_path = Path(self.kernel.config.path).parent / tmpl_path
        if not tmpl_path.exists():
            self.kernel.syslog.warning("comfyui", f"video_workflow not found: {tmpl_path}")
            return ""

        if seed == 0:
            seed = random.randint(0, 2**32 - 1)

        try:
            workflow = json.loads(tmpl_path.read_text(encoding="utf-8"))
        except Exception as e:
            self.kernel.syslog.error("comfyui", f"video_workflow parse error: {e}")
            return ""

        # Strip top-level meta keys (e.g. _comment, _requires) — ComfyUI
        # treats every top-level entry as a node and rejects unknowns.
        workflow = {k: v for k, v in workflow.items() if not k.startswith("_")}

        def _sub(node):
            if isinstance(node, dict):
                return {k: _sub(v) for k, v in node.items()}
            if isinstance(node, list):
                return [_sub(x) for x in node]
            if isinstance(node, str):
                # Numeric placeholders ({seed}, {frames}) often appear as a
                # JSON *string* — `"length": "{frames}"` — because raw JSON
                # can't write a bare integer as a placeholder. If the entire
                # string is one of those, return the int directly so ComfyUI
                # gets the type it expects (LTX scheduler refuses string
                # values where it wants ints).
                if node == "{seed}":
                    return int(seed)
                if node == "{frames}":
                    return int(num_frames)
                if node == "{width}":
                    return int(width)
                if node == "{height}":
                    return int(height)
                return (
                    node.replace("{prompt}", prompt)
                    .replace("{image}", image_filename)
                    .replace("{seed}", str(seed))
                    .replace("{frames}", str(num_frames))
                    .replace("{width}", str(width))
                    .replace("{height}", str(height))
                )
            return node

        workflow = _sub(copy.deepcopy(workflow))

        try:
            self.kernel.syslog.info(
                "comfyui",
                f"Queuing video: {prompt[:60]}...",
                data={"frames": num_frames, "image": image_filename},
            )
            async with self._session.post(
                f"{self._host()}/prompt",
                json={"prompt": workflow},
                timeout=aiohttp.ClientTimeout(total=600),
            ) as resp:
                data = await resp.json()
                prompt_id = data.get("prompt_id", "")

            for _ in range(400):  # up to ~10 min
                await asyncio.sleep(1.5)
                async with self._session.get(f"{self._host()}/history/{prompt_id}") as resp:
                    history = await resp.json()
                    if prompt_id in history:
                        outputs = history[prompt_id].get("outputs", {})
                        for _node_id, output in outputs.items():
                            for key in ("videos", "gifs", "images"):
                                items = output.get(key, [])
                                if items:
                                    return items[0].get("filename", "")
                        return ""
            return ""
        except Exception as e:
            self.kernel.syslog.error("comfyui", f"video generate failed: {e}")
            return ""

    async def generate_depth(self, image_filename: str, template_path: str = "") -> str:
        """Run a depth-only workflow on an input image. Returns the depth-map
        filename produced by the SaveImage node, or "" on failure.

        Caller is expected to have ``image_filename`` already in ComfyUI's
        ``input/`` directory (or to use ``upload_image`` first). The default
        workflow lives at ``plugins/comfyui/workflows/depth_parallax.json``.
        """
        return await self.generate_from_workflow(
            workflow_key="parallax",
            prompt="",
            image_filename=image_filename,
            num_frames=1,
            seed=0,
            template_path=template_path,
        )

    async def upload_image(self, src_path, name: str = "") -> str:
        """Upload a local image into ComfyUI's input/ folder and return the
        server-side filename usable by LoadImage. Used when the image we
        want to depth-process isn't already in ComfyUI's input dir.
        """
        from pathlib import Path

        p = Path(src_path)
        if not p.exists():
            return ""
        name = name or p.name
        try:
            data = aiohttp.FormData()
            # Brief blocking open — aiohttp streams the file from here.
            data.add_field(
                "image",
                p.open("rb"),  # noqa: ASYNC230
                filename=name,
                content_type="application/octet-stream",
            )
            data.add_field("overwrite", "true")
            async with self._session.post(
                f"{self._host()}/upload/image",
                data=data,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    return ""
                body = await resp.json()
                return body.get("name") or name
        except Exception:
            return ""

    async def download_image(self, filename: str, dest) -> bool:
        """Fetch a generated file from ComfyUI and write it to dest (Path or str)."""
        from pathlib import Path

        if not filename:
            return False
        try:
            async with self._session.get(
                f"{self._host()}/view",
                params={"filename": filename},
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status != 200:
                    return False
                data = await resp.read()
            dest_path = Path(dest)
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_bytes(data)
            return True
        except Exception:
            return False

    async def free_gpu(self):
        """Unload models + free VRAM after generation."""
        try:
            async with self._session.post(
                f"{self._host()}/free",
                json={"unload_models": True, "free_memory": True},
                timeout=aiohttp.ClientTimeout(total=10),
            ):
                pass
        except Exception:
            pass

    # --- Workflow Builder ---

    def _build_workflow(
        self,
        prompt: str,
        width: int,
        height: int,
        seed: int,
        style: dict,
        lora: str = "",
        lora_strength: float = 0.8,
    ) -> dict:
        """Build ComfyUI workflow. Handles FLUX, SDXL, SD1.5 + LoRA."""
        ckpt = style.get("checkpoint", "flux1-dev-fp8.safetensors")
        is_flux = "flux" in ckpt.lower()

        steps = style.get("steps", 30 if is_flux else 25)
        cfg = style.get("cfg", 1.0 if is_flux else 7.0)
        sampler = style.get("sampler", "euler" if is_flux else "dpmpp_2m")
        scheduler = style.get("scheduler", "simple" if is_flux else "normal")
        negative = style.get("negative", "ugly, blurry, low quality, deformed")

        workflow = {
            "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"batch_size": 1, "height": height, "width": width},
            },
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
            "9": {
                "class_type": "SaveImage",
                "inputs": {"filename_prefix": "eos", "images": ["8", 0]},
            },
        }

        # LoRA support
        lora_name = lora or style.get("lora", "")
        if lora_name and is_flux:
            lora_file = (
                lora_name if lora_name.endswith(".safetensors") else f"{lora_name}.safetensors"
            )
            ls = style.get("lora_strength", lora_strength)
            workflow["10"] = {
                "class_type": "LoraLoader",
                "inputs": {
                    "model": ["4", 0],
                    "clip": ["4", 1],
                    "lora_name": lora_file,
                    "strength_model": ls,
                    "strength_clip": ls,
                },
            }
            model_input, clip_input = ["10", 0], ["10", 1]
        else:
            model_input, clip_input = ["4", 0], ["4", 1]

        workflow["6"] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": clip_input, "text": prompt},
        }
        workflow["7"] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": clip_input, "text": negative},
        }
        workflow["3"] = {
            "class_type": "KSampler",
            "inputs": {
                "cfg": cfg,
                "denoise": 1.0,
                "latent_image": ["5", 0],
                "model": model_input,
                "negative": ["7", 0],
                "positive": ["6", 0],
                "sampler_name": sampler,
                "scheduler": scheduler,
                "seed": seed,
                "steps": steps,
            },
        }
        return workflow

    # --- Generate ---

    async def generate(
        self,
        prompt: str,
        width: int = 1024,
        height: int = 1024,
        steps: int = 30,
        cfg: float = 0,
        model: str = "",
        style: str = "",
        lora: str = "",
        lora_strength: float = 0.8,
    ) -> str:
        """Generate image. Returns filename.

        If style is given, applies preset (checkpoint, sampler, cfg, prefix/suffix, LoRA).
        """
        import asyncio

        seed = random.randint(0, 2**32 - 1)
        style_preset = STYLE_PRESETS.get(style, {})

        # Apply style prefix/suffix
        prefix = style_preset.get("prefix", "")
        suffix = style_preset.get("suffix", "")
        styled_prompt = f"{prefix}{prompt}{suffix}"

        # Override dimensions from style
        w = style_preset.get("width", width)
        h = style_preset.get("height", height)

        # Build workflow with style params
        workflow = self._build_workflow(
            styled_prompt,
            w,
            h,
            seed,
            style_preset,
            lora=lora,
            lora_strength=lora_strength,
        )

        try:
            self.kernel.syslog.info(
                "comfyui",
                f"Queuing image: {prompt[:80]}...",
                data={"width": w, "height": h, "style": style},
            )
            async with self._session.post(
                f"{self._host()}/prompt",
                json={"prompt": workflow},
                timeout=aiohttp.ClientTimeout(total=300),
            ) as resp:
                data = await resp.json()
                prompt_id = data.get("prompt_id", "")

            # Poll for completion
            for _ in range(120):
                await asyncio.sleep(1.5)
                async with self._session.get(f"{self._host()}/history/{prompt_id}") as resp:
                    history = await resp.json()
                    if prompt_id in history:
                        outputs = history[prompt_id].get("outputs", {})
                        for node_id, output in outputs.items():
                            images = output.get("images", [])
                            if images:
                                return images[0].get("filename", "")
                        return ""
            return ""
        except Exception as e:
            raise RuntimeError(f"ComfyUI generation failed: {e}") from e
        finally:
            await self.free_gpu()
