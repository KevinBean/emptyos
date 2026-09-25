"""ComfyUI plugin — local GPU media generation.

Connects to ComfyUI for image, video, depth, and audio workflows.
Also registers ``draw`` and ``animate`` capability providers.

Absorbed from AI Phone Agent's comfyui_api.py — supports FLUX, SDXL, SD1.5
checkpoints, LoRA, style presets with per-style params, GPU memory freeing.
"""

from __future__ import annotations

import json
import random
import re
import time
from contextlib import asynccontextmanager

import aiohttp

from emptyos.sdk import BasePlugin

# --- ACE-Step 1.5 conditioning domains ---
# Verbatim from GET /object_info/TextEncodeAceStepAudio1.5 (ComfyUI 0.33.0).
# These are strict ComfyUI COMBOs: an out-of-list value — including "" or
# "auto" — makes /prompt reject the ENTIRE workflow with `value_not_in_list`
# (ComfyUI execution.py), which reaches us only as an opaque "no prompt_id"
# syslog line and a "" return. So the domain is enforced here, before anything
# is queued, and a typo costs a readable error instead of a silent no-op.
#
# Note the asymmetry: `language` DOES have an "unknown" member, so "let the
# model decide" is delegable there. `keyscale` has no such member — every one
# of its 34 options is a concrete key — which is why auto is resolved locally
# in _resolve_keyscale() rather than passed through.
#
# The enum is a NUMERATOR ONLY — the denominator is implicitly 4, so "6" means
# 6/4, not 6/8, and compound duple cannot be requested directly. Express 6/8 as
# timesignature="3" at DOUBLE the bpm instead: 6/8 is two dotted-quarter beats,
# so a 76 bpm 6/8 lullaby is `timesignature="3", bpm=152`. Measured 2026-08-15
# against a Suno master of the same song: meter=6/bpm=76 detected 95.7 bpm
# (wrong feel), meter=3/bpm=152 detected 152.0 — identical to the reference.
ACESTEP_TIME_SIGNATURES = ("2", "3", "4", "6")

# 8 is what acestep_v1.5_turbo is distilled for, and the workflow pinned it
# there until 2026-08-15 on the theory that a distilled model is converged and
# exposing steps could only hurt. Tested by ear: 40 is audibly better, and it
# costs +2.5s on a 60s song (0.65s -> 3.10s of sampling, both ~12.5 it/s), so
# the cost the pin was protecting against does not exist. Worth remembering
# that the spectral proxy backed the wrong side — rolloff moved only 3.8k->4.3k
# across 8->40 steps, which read as "no difference" and was not.
ACESTEP_DEFAULT_STEPS = 40
ACESTEP_MAX_STEPS = 200

# Repaint runs on a DIFFERENT model than compose: ACE-Step 1.0 3.5B via the MW
# custom-node pack, because that pack owns every studio verb and core ComfyUI
# ships no 1.5 repaint node. Its own defaults are not reusable here.
#
# 60 rather than ACESTEP_DEFAULT_STEPS: 40 is tuned for the *distilled turbo*
# 1.5 checkpoint, and 1.0 is not distilled — 60 is what the pack's own example
# and its pipeline default both use.
ACESTEP_REPAINT_DEFAULT_STEPS = 60
# The pipeline skips every step before `n_min = int(infer_step * (1 - variance))`,
# so variance IS the strength dial. Measured 2026-08-15, repainting 20-40s of a
# 60s take, as log-mel correlation against the source (phase-invariant; see the
# metric warning in repaint_music). Only the WITHIN-run gap is controlled —
# absolute values shift between runs because lag alignment does:
#
#   variance   kept(before/after)   window    separation   runtime
#     0.01        0.878 / 0.928      0.891      -0.013       10s   <- no-op
#     0.50        0.974 / 0.978      0.829      +0.145       30s
#     0.95        0.876 / 0.927      0.548      +0.329       15s
#
# At the pack's own 0.01 default the window correlates no differently than the
# untouched audio — that is failure mode one, "a repaint that changes nothing",
# and it is the shipped default, so it must not be inherited.
#
# THE CEILING IS SET BY VOCALS, NOT BY THE MUSIC, and log-mel cannot see it.
# Measured 2026-08-16 on a take with known lyrics, repainting 20-40s, scored by
# transcribing the window and comparing words against the source's own
# transcript (`scripts/check_repaint_lyrics.py`):
#
#   variance   word-similarity   what the window sings
#     0.15         0.612         right words, smeared
#     0.30         0.778         right words, right place
#     0.50         0.235         RESTARTS the verse from line 1
#
# Above ~0.3 the model stops inheriting the take's lyric alignment and re-derives
# it, so the window sings real lyrics from the wrong part of the song. Every
# spectral metric rates that a healthy repaint (log-mel r=0.83) because the
# pitch and rhythm are unchanged — only a listener or a transcript catches it.
# Hence 0.3: the highest value measured to still hold the vocal line. Raise it
# only for an INSTRUMENTAL window, where there is no alignment to lose.
ACESTEP_REPAINT_DEFAULT_VARIANCE = 0.3
# The variance above which lyric alignment was observed to break. Not a hard
# limit — an instrumental section is free to exceed it — but crossing it with
# lyrics present earns a warning, because the failure is inaudible to the gate
# and obvious to the listener.
ACESTEP_REPAINT_LYRIC_SAFE_VARIANCE = 0.35
ACESTEP_KEYSCALES = (
    "C major", "C# major", "Db major", "D major",
    "D# major", "Eb major", "E major", "F major",
    "F# major", "Gb major", "G major", "G# major",
    "Ab major", "A major", "A# major", "Bb major",
    "B major", "C minor", "C# minor", "Db minor",
    "D minor", "D# minor", "Eb minor", "E minor",
    "F minor", "F# minor", "Gb minor", "G minor",
    "G# minor", "Ab minor", "A minor", "A# minor",
    "Bb minor", "B minor",
)
_ACESTEP_KEYSCALE_LOOKUP = {k.casefold(): k for k in ACESTEP_KEYSCALES}


def _resolve_keyscale(keyscale: str, seed: int) -> str:
    """Map a caller's keyscale onto a valid ACE-Step COMBO member.

    An empty request draws from the seed rather than falling back to a fixed
    key: same seed -> same key, so a track stays reproducible, while different
    tracks stop all landing in one. (Before 2026-08-15 the workflow hardcoded
    "E minor", so every track EmptyOS composed was in E minor.)
    """
    want = (keyscale or "").strip()
    if not want:
        return random.Random(seed).choice(ACESTEP_KEYSCALES)
    canonical = _ACESTEP_KEYSCALE_LOOKUP.get(want.casefold())
    if canonical is None:
        raise ValueError(
            f"unknown ACE-Step keyscale {keyscale!r}; expected one of "
            + ", ".join(ACESTEP_KEYSCALES)
        )
    return canonical


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
    # FLUX.2 Klein — the `is_flux2` marker routes _build_workflow down the
    # UNETLoader graph (see _build_flux2_workflow). 4B is Apache-2.0 (commercial-
    # safe); 9B is the FLUX Non-Commercial License (personal / best quality).
    # Both are guidance-distilled → few steps, no negative channel. Encoders are
    # NOT interchangeable: 4B pairs with qwen_3_4b, 9B with qwen_3_8b.
    "klein-4b": {
        "label": "Klein 4B (commercial)",
        "is_flux2": True,
        "unet": "flux-2-klein-4b-fp8.safetensors",
        "clip": "qwen_3_4b.safetensors",
        "sampler": "euler",
        "steps": 6,
    },
    "klein-9b": {
        "label": "Klein 9B (best, non-commercial)",
        "is_flux2": True,
        "unet": "flux-2-klein-9b-fp8.safetensors",
        "clip": "qwen_3_8b_fp8mixed.safetensors",
        "sampler": "euler",
        "steps": 8,
    },
}

# Appended to a FLUX.2 prompt when a text overlay is requested, so the model
# leaves clean space for the real DrawText+ layer instead of baking in (usually
# misspelled) letters — the distilled Klein models associate "brand"/"poster"
# with text and hallucinate gibberish otherwise.
FLUX2_TEXT_FREE_SUFFIX = (
    ", clean composition, generous negative space, no text, no letters, no words"
)


class ComfyUIPlugin(BasePlugin):
    name = "comfyui"

    # How long a failed reachability probe suppresses the next one. Short
    # enough that a ComfyUI started after the daemon is picked up almost
    # immediately; long enough that a batch render against a dead server does
    # not pay a 3s timeout per image. See `available()`.
    _UNAVAILABLE_TTL = 10.0

    def __init__(self, kernel, manifest):
        super().__init__(kernel, manifest)
        self._session = None
        self._unavailable_until = 0.0

    def _host(self) -> str:
        return self.config("host", "http://localhost:8188")

    async def connect(self):
        self._session = aiohttp.ClientSession()
        self._draw_registered = False
        # Register UNCONDITIONALLY. Registration only declares that this plugin
        # *can* draw/animate; the capability chain calls `available()` before
        # executing and skips a provider that says no
        # (`emptyos/capabilities/__init__.py`), so an unreachable ComfyUI
        # behaves exactly as before — the chain falls through.
        #
        # Gating registration on boot-time reachability instead made local GPU
        # generation depend on a race: ComfyUI usually starts *after* EmptyOS,
        # so the providers were never registered and every `self.draw()` went to
        # the paid cloud fallback with the GPU idle. `ensure_available()` was
        # the workaround, but only 4 of 10 draw-calling apps remembered it —
        # app-gen, explore, kb, ppt, reader and scroll all silently billed
        # OpenAI. Observed live on 2026-07-28 with ComfyUI up and healthy.
        self._register_draw()
        if await self.available():
            print(f"[ComfyUI] Connected to {self._host()}")
        else:
            # NOT a fallback announcement. Since registration became
            # unconditional (2026-07-28) the providers are in the chain either
            # way and `available()` is re-checked per call, so a ComfyUI that
            # finishes booting a minute later simply starts serving — no app
            # action, no cloud spend. The old wording ("draw/animate fall
            # through to the cloud until it answers") described the pre-fix
            # behaviour and read as an active money leak long after it stopped
            # being one; it ranked on the syslog board for a week on that basis.
            #
            # Info, not warn: ComfyUI normally starts *after* EmptyOS, so this
            # is the expected boot ordering rather than a fault. A call that
            # genuinely lands while it is still down falls through to a cloud
            # provider, and that path has its own consent gate (CLAUDE.md
            # rule 18) — which is where spend should be refused, not here.
            msg = (
                f"not up yet at {self._host()} — providers are registered and "
                f"will serve as soon as it answers"
            )
            print(f"[ComfyUI] {msg}")
            self.kernel.syslog.info("comfyui", msg)

    def _register_draw(self):
        if self._draw_registered:
            return
        from emptyos.capabilities import Provider

        plugin = self

        class ComfyUIDrawProvider(Provider):
            name = "comfyui"

            # Without these, is_cloud read `bool("") and ...` -> always False,
            # so a ComfyUI pointed at a rented/public box was silently treated
            # as local and never hit the consent gate. Properties, not class
            # attrs, so they track a config change instead of freezing at boot.
            @property
            def host(self) -> str:
                return plugin._host()

            @property
            def trust(self) -> str:
                return plugin.config("trust", "")

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

            @property
            def host(self) -> str:
                return plugin._host()

            @property
            def trust(self) -> str:
                return plugin.config("trust", "")

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
                    seed=int(kwargs.get("seed", 0) or 0),
                    width=int(kwargs.get("width", 768)),
                    height=int(kwargs.get("height", 432)),
                    negative_prompt=str(kwargs.get("negative_prompt", "")),
                    workflow_snapshot_path=(
                        str(Path(dest).with_suffix(".workflow-api.json"))
                        if dest
                        else ""
                    ),
                )
                if not filename:
                    plugin.kernel.syslog.error(
                        "comfyui",
                        f"animate({workflow}) got no filename back from "
                        f"generate_from_workflow — the render may have "
                        f"succeeded; check ComfyUI's output directory",
                    )
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
        # Not the per-prompt timings — ComfyUI logs those itself to
        # ComfyUI/user/comfyui.log. What only exists here is whatever the process
        # writes before that logger initialises, or as it dies: a bad custom node,
        # a CUDA load failure, a traceback. DEVNULL discarded exactly the output
        # that explains an unattended revival, which is the one launch nobody
        # watched. Binary append — the handle is only ever a subprocess fd.
        log_path = Path(launcher_dir) / "comfyui.log"
        try:
            log_handle = log_path.open("ab")
        except OSError:
            log_handle = None
        try:
            # Fire-and-forget headless launch — Popen returns immediately.
            subprocess.Popen(  # noqa: ASYNC220
                [python_exe, "-s", main_py, "--windows-standalone-build"],
                cwd=launcher_dir,
                creationflags=subprocess.CREATE_NO_WINDOW,
                stdout=log_handle or subprocess.DEVNULL,
                stderr=subprocess.STDOUT if log_handle else subprocess.DEVNULL,
            )
            # The child holds its own inherited descriptor; keeping the parent's
            # copy open would pin the file in the daemon for the whole 60s wait.
            if log_handle is not None:
                log_handle.close()
                log_handle = None
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
        finally:
            if log_handle is not None:
                log_handle.close()
        return False

    async def ensure_available(self) -> bool:
        """Check if ComfyUI is running, auto-start if not.

        Re-registers **every** provider this plugin owns, not just ``draw``.
        ComfyUI usually finishes booting after EmptyOS, and ``connect()`` only
        registers providers it could reach at the time — so a lost race leaves
        the capability empty. Healing ``draw`` alone meant an app guarding with
        this call still hit "No available provider for capability 'animate'"
        (measured 2026-07-25: a 20-scene MV render generated all its stills via
        the plugin *service*, then failed the whole clips stage on the missing
        *capability*). Both registrars are idempotent.
        """
        if await self.available():
            if not self._draw_registered:
                self._register_draw()
            if not getattr(self, "_animate_registered", False):
                self._register_animate()
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
        """Live reachability probe, with a short NEGATIVE cache.

        The providers are now registered unconditionally at connect(), so this
        runs on every draw/animate resolution rather than once at boot. When
        ComfyUI is up the probe is milliseconds and the cache is irrelevant —
        a True is never cached, so a server that dies mid-run is noticed on the
        next call. When it is *down*, though, each call would otherwise wait out
        the full 3s timeout before the chain falls through: 20 stills = a minute
        of dead waiting. Caching only the False bounds that to one probe per
        window while keeping recovery fast — a ComfyUI that comes up is picked
        up within `_UNAVAILABLE_TTL` seconds.
        """
        now = time.monotonic()
        cached = getattr(self, "_unavailable_until", 0.0)
        if now < cached:
            return False
        try:
            async with self._session.get(
                f"{self._host()}/system_stats",
                timeout=aiohttp.ClientTimeout(total=3),
            ) as resp:
                ok = resp.status == 200
        except Exception:
            ok = False
        if not ok:
            self._unavailable_until = now + self._UNAVAILABLE_TTL
        return ok

    async def health_check(self) -> bool:
        return await self.available()

    @staticmethod
    def _version_tuple(value: object) -> tuple[int, ...]:
        """Return a comparison tuple for ComfyUI's numeric release versions."""
        parts = re.findall(r"\d+", str(value or ""))
        return tuple(int(part) for part in parts[:3])

    async def _ensure_runtime_compatible(self) -> None:
        """Reject a reachable but half-migrated ComfyUI before queueing work.

        Newer ComfyUI releases report each required companion package alongside
        the version actually imported by the server. A core-only checkout can
        still bind the port while loading stale or malformed packages; treating
        that as healthy wastes an entire render before the incompatibility
        surfaces. Older servers omit this telemetry, so package checking is
        naturally backward-compatible unless ``minimum_version`` is configured.
        """
        if not self.config("feature.runtime-compatibility.enabled", True):
            return
        now = time.monotonic()
        cached = getattr(self, "_runtime_compatibility_cache", None)
        if cached and now - cached[0] < 30.0:
            if cached[1]:
                raise RuntimeError(cached[1])
            return

        problem = ""
        try:
            async with self._session.get(
                f"{self._host()}/system_stats",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    return
                payload = await resp.json()
            system = payload.get("system") or {}
            current = str(system.get("comfyui_version") or "")
            minimum = str(self.config("minimum_version", "") or "")
            if (
                minimum
                and self._version_tuple(current)
                and self._version_tuple(current) < self._version_tuple(minimum)
            ):
                problem = (
                    f"ComfyUI {current} is below configured minimum {minimum}"
                )
            mismatches = []
            for item in system.get("comfy_package_versions") or []:
                required = item.get("required")
                installed = item.get("installed")
                if required and installed != required:
                    mismatches.append(
                        f"{item.get('name', '?')}={installed!r} "
                        f"(required {required})"
                    )
            if mismatches:
                problem = (
                    "ComfyUI runtime package mismatch: "
                    + ", ".join(mismatches)
                )
        except RuntimeError:
            raise
        except Exception as exc:
            # Reachability and prompt submission retain their existing failure
            # behaviour. This gate only hard-fails facts the server did report.
            self.kernel.syslog.warning(
                "comfyui", f"runtime compatibility check skipped: {exc}",
            )
            return

        self._runtime_compatibility_cache = (now, problem)
        if problem:
            raise RuntimeError(problem)

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

    #: Outputs are saved into ``<prefix>/<YYYY-MM>/`` subfolders so ComfyUI's
    #: output dir self-organizes by month instead of accumulating thousands of
    #: files flat (it reached 6k files / 6.3 GiB before the 2026-07 cleanup).
    #:
    #: Uses ``%year%-%month%``, NOT the ``%date:yyyy-MM%`` form seen in some
    #: ComfyUI docs/custom nodes: ``folder_paths.compute_vars`` in this build
    #: only substitutes %year%/%month%/%day%/%hour%/%minute%/%second%/%width%/
    #: %height%. An unrecognised token survives verbatim into the subfolder
    #: name, and ``%date:...%`` contains a colon — illegal in a Windows path —
    #: so it would fail every save rather than degrade. Verify against
    #: ``ComfyUI/folder_paths.py`` before adding a token here.
    @staticmethod
    def _dated_prefix(stem: str) -> str:
        return f"{stem}/%year%-%month%/{stem}"

    @staticmethod
    def _item_path(item: dict | None) -> str:
        """Flatten a ComfyUI output item to one ``subfolder/filename`` string.

        Callers (and every app consuming this plugin) pass that string straight
        back into :meth:`download_image` / :meth:`get_image_url`, which split it
        again — so the single-string return contract survives subfoldering.
        """
        if not item:
            return ""
        name = item.get("filename", "")
        # ComfyUI reports the subfolder with the OS separator (``eos\2026-07`` on
        # Windows); normalise so the returned string uses one separator and
        # ``_view_params`` can split it unambiguously.
        sub = (item.get("subfolder", "") or "").replace("\\", "/").strip("/")
        return f"{sub}/{name}" if sub and name else name

    @staticmethod
    def _view_params(filename: str) -> dict:
        """Split ``subfolder/name.png`` into ComfyUI /view query params.

        ``/view`` matches on basename and takes ``subfolder`` separately; sending
        the joined path as ``filename`` 404s, which would silently break every
        download once outputs live in subfolders.
        """
        sub, _, name = filename.rpartition("/")
        return {"filename": name, "subfolder": sub} if sub else {"filename": name}

    async def get_image_url(self, filename: str) -> str:
        from urllib.parse import urlencode

        return f"{self._host()}/view?{urlencode(self._view_params(filename))}"

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

    async def _submit_and_poll(
        self,
        workflow: dict,
        *,
        output_keys: tuple[str, ...],
        post_timeout: int = 300,
        max_polls: int = 120,
        poll_interval: float = 1.5,
        live_grace_polls: int = 0,
    ) -> dict | None:
        """Queue a workflow on ComfyUI (/prompt) and poll /history until an
        output under one of ``output_keys`` appears. Returns the first matching
        output item (a dict carrying filename/subfolder/type), or None on prompt
        rejection / no-output / timeout. After the nominal timeout, an optional
        grace window continues only while ComfyUI still reports this exact
        prompt as running or pending. This distinguishes a slow live diffusion
        job from a dead request without extending every failure path.

        Exceptions PROPAGATE — each caller keeps its own try/except so it can
        choose raise-vs-return-"" on failure (image gen raises to trigger the
        capability fallback chain; video/audio return ""). Does NOT free the GPU
        — callers decide whether to keep the model warm.
        """
        import asyncio

        async with self._session.post(
            f"{self._host()}/prompt",
            json={"prompt": workflow},
            timeout=aiohttp.ClientTimeout(total=post_timeout),
        ) as resp:
            data = await resp.json()
            prompt_id = data.get("prompt_id", "")
        if not prompt_id:
            self.kernel.syslog.error(
                "comfyui",
                f"/prompt rejected the workflow (no prompt_id) — {str(data)[:200]}",
            )
            return None
        base_polls = max(1, int(max_polls))
        grace_polls = max(0, int(live_grace_polls))
        total_polls = base_polls + grace_polls
        empty_finished = 0
        for poll_index in range(total_polls):
            await asyncio.sleep(poll_interval)
            async with self._session.get(f"{self._host()}/history/{prompt_id}") as resp:
                history = await resp.json()
            if prompt_id in history:
                entry = history[prompt_id] or {}
                outputs = entry.get("outputs", {})
                for _node_id, output in outputs.items():
                    for key in output_keys:
                        items = output.get(key) or []
                        if items:
                            return items[0]
                # A history entry can exist a moment before its outputs are
                # attached, so "present but empty" is not proof of failure —
                # giving up here threw away three completed Wan renders on
                # 2026-07-28 (09:31, 13:33, 21:30), each ~5 GPU-minutes, each
                # exactly one second after ComfyUI reported execution_success
                # with a valid ~1MB mp4 on disk. Only stop once the entry says
                # it is finished; otherwise keep polling within the budget.
                status = entry.get("status") or {}
                finished = bool(status.get("completed")) or str(
                    status.get("status_str") or "",
                ).lower() in {"success", "error"}
                if finished and outputs:
                    self.kernel.syslog.error(
                        "comfyui",
                        f"prompt {prompt_id} finished with outputs "
                        f"{ {k: list(v.keys()) for k, v in outputs.items()} } "
                        f"but none under {output_keys}",
                    )
                    return None  # genuinely done, nothing we can use
                if finished:
                    empty_finished += 1
                    if empty_finished > 20:
                        self.kernel.syslog.error(
                            "comfyui",
                            f"prompt {prompt_id} reported finished but never "
                            f"attached an output under {output_keys} — check "
                            f"ComfyUI's output directory before assuming OOM",
                        )
                        return None
                continue
            if (
                grace_polls
                and poll_index + 1 >= base_polls
                and (
                    poll_index + 1 == base_polls
                    or (poll_index + 1 - base_polls) % 10 == 0
                )
            ):
                try:
                    async with self._session.get(
                        f"{self._host()}/queue",
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as resp:
                        queue = await resp.json()
                    entries = (
                        list(queue.get("queue_running") or [])
                        + list(queue.get("queue_pending") or [])
                    )
                    is_live = any(
                        isinstance(entry, (list, tuple))
                        and len(entry) > 1
                        and entry[1] == prompt_id
                        for entry in entries
                    )
                    if not is_live:
                        self.kernel.syslog.error(
                            "comfyui",
                            f"prompt {prompt_id} left the queue without a "
                            f"usable output under {output_keys} — check "
                            f"ComfyUI's output directory before assuming OOM",
                        )
                        return None
                    if poll_index + 1 == base_polls:
                        self.kernel.syslog.info(
                            "comfyui",
                            f"prompt {prompt_id} exceeded nominal poll window; "
                            "continuing while ComfyUI reports it live",
                        )
                except Exception:
                    # Queue telemetry is advisory. A transient /queue failure
                    # must not turn a healthy long render into a false timeout.
                    pass
        self.kernel.syslog.error(
            "comfyui",
            f"prompt {prompt_id} gave up after {total_polls} polls "
            f"(~{total_polls * poll_interval:.0f}s) without a usable output",
        )
        return None  # timed out

    async def generate_from_workflow(
        self,
        workflow_key: str,
        prompt: str,
        image_filename: str = "",
        num_frames: int = 97,
        seed: int = 0,
        width: int = 768,
        height: int = 432,
        negative_prompt: str = "",
        template_path: str = "",
        workflow_snapshot_path: str = "",
        substitutions: dict | None = None,
        output_keys: tuple[str, ...] | None = None,
        preflight_kind: str = "",
    ) -> str:
        """Run any ComfyUI workflow from a JSON template, with placeholder
        substitution. Used for image-to-video (LTX-2 / Wan / SVD), depth
        parallax, and ACE-Step audio generation.

        Resolves the template path from ``[plugins.comfyui] {workflow_key}_workflow``
        unless ``template_path`` is given. Any string value in the JSON
        containing ``{prompt}`` / ``{image}`` / ``{seed}`` / ``{frames}`` is
        substituted. ``substitutions`` adds typed placeholders for workflow-
        specific values such as ``{duration}`` and ``{style}``. Returns the
        first matching output filename, or "" on failure.
        """
        import copy
        from pathlib import Path

        path_str = template_path or self.config(f"{workflow_key}_workflow", "")
        if not path_str:
            return ""

        await self._ensure_runtime_compatible()

        # Keep inference type explicit when a workflow is not visual. The
        # fallback preserves the historical depth/video behaviour.
        await self._preflight(
            preflight_kind
            or ("depth" if workflow_key == "depth" else "video")
        )

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

        placeholders = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "image": image_filename,
            "seed": int(seed),
            "frames": int(num_frames),
            "width": int(width),
            "height": int(height),
        }
        placeholders.update(substitutions or {})

        def _sub(node):
            if isinstance(node, dict):
                return {k: _sub(v) for k, v in node.items()}
            if isinstance(node, list):
                return [_sub(x) for x in node]
            if isinstance(node, str):
                # A whole-value placeholder retains its JSON type. This is
                # load-bearing for numeric ComfyUI inputs: `"duration":
                # "{duration}"` must become a float, not the string "30.0".
                if node.startswith("{") and node.endswith("}"):
                    key = node[1:-1]
                    if key in placeholders:
                        return placeholders[key]
                rendered = node
                for key, value in placeholders.items():
                    rendered = rendered.replace(
                        "{" + key + "}", str(value),
                    )
                return rendered
            return node

        workflow = _sub(copy.deepcopy(workflow))
        if workflow_snapshot_path:
            snapshot = Path(workflow_snapshot_path)
            try:
                snapshot.parent.mkdir(parents=True, exist_ok=True)
                snapshot.write_text(
                    json.dumps(workflow, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception as e:
                # A missing diagnostic snapshot must not discard an expensive
                # render. The video remains authoritative; syslog records the
                # observability gap.
                self.kernel.syslog.warning(
                    "comfyui",
                    f"workflow snapshot failed for {snapshot}: {e}",
                )

        try:
            self.kernel.syslog.info(
                "comfyui",
                f"Queuing video: {prompt[:60]}...",
                data={"frames": num_frames, "image": image_filename},
            )
            item = await self._submit_and_poll(
                workflow,
                output_keys=output_keys or ("videos", "gifs", "images"),
                post_timeout=600, max_polls=400, live_grace_polls=800,
            )
            return self._item_path(item)
        except Exception as e:
            self.kernel.syslog.error("comfyui", f"video generate failed: {e}")
            return ""

    async def generate_music(
        self,
        prompt: str,
        *,
        style: str = "",
        duration: float = 30.0,
        lyrics: str = "",
        language: str = "en",
        bpm: int = 120,
        keyscale: str = "",
        timesignature: str = "4",
        steps: int = ACESTEP_DEFAULT_STEPS,
        seed: int = 0,
        dest: str = "",
    ) -> str:
        """Generate ACE-Step 1.5 audio and optionally download it locally.

        The exact API workflow lives in ``music_workflow`` rather than being
        assembled ad hoc in Music Studio. A zero seed preserves the service's
        historical random-by-default convention.

        ``keyscale=""`` means auto — a key is drawn from the seed, because
        ACE-Step's COMBO has no "auto" member (see ``_resolve_keyscale``).
        ``timesignature`` is a STRING, not an int: the COMBO members are
        "2".."6" and ComfyUI compares by identity, so 4 != "4".

        Negative prompts are NOT supported by this workflow: its KSampler runs
        at ``cfg 1.0``, and ComfyUI skips the uncond branch entirely at cfg 1.0
        (``comfy/samplers.py``), so a negative prompt could not affect a single
        sample. Node 6 exists only to satisfy KSampler's required ``negative``
        input. Enabling it means raising cfg AND replacing node 6 with a second
        text encode — a different workflow, not a parameter.
        """
        from pathlib import Path

        duration = float(duration)
        if not 1.0 <= duration <= 1000.0:
            raise ValueError("ACE-Step duration must be between 1 and 1000 seconds")
        bpm = max(10, min(300, int(bpm)))
        if seed == 0:
            seed = random.randint(1, 2**32 - 1)

        # After the seed block on purpose: an explicit seed gives a stable
        # auto-key, seed=0 varies it per track.
        timesignature = str(timesignature).strip() or "4"
        if timesignature not in ACESTEP_TIME_SIGNATURES:
            raise ValueError(
                "ACE-Step timesignature must be one of "
                + ", ".join(ACESTEP_TIME_SIGNATURES)
            )
        steps = int(steps)
        if not 1 <= steps <= ACESTEP_MAX_STEPS:
            raise ValueError(f"ACE-Step steps must be 1..{ACESTEP_MAX_STEPS}")
        keyscale = _resolve_keyscale(keyscale, seed)
        # Logged because an auto-resolved key is otherwise invisible — it is
        # the only record of what the track was actually conditioned on.
        self.kernel.syslog.info(
            "comfyui",
            f"ACE-Step: key={keyscale} meter={timesignature}/4 "
            f"bpm={bpm} dur={duration:.0f}s steps={steps} seed={seed}",
        )

        filename = await self.generate_from_workflow(
            workflow_key="music",
            prompt=prompt,
            seed=seed,
            substitutions={
                "style": style,
                "duration": duration,
                "lyrics": lyrics,
                "language": language,
                "bpm": bpm,
                "keyscale": keyscale,
                "timesignature": timesignature,
                "steps": steps,
            },
            output_keys=("audio", "audios"),
            preflight_kind="audio",
        )
        if not filename or not dest:
            return filename
        destination = Path(dest)
        destination.parent.mkdir(parents=True, exist_ok=True)
        ok = await self.download_image(filename, destination)
        return str(destination) if ok else ""

    async def repaint_music(
        self,
        src_audio: str,
        *,
        repaint_start: float,
        repaint_end: float,
        prompt: str,
        lyrics: str,
        negative_prompt: str = "",
        variance: float = ACESTEP_REPAINT_DEFAULT_VARIANCE,
        steps: int = ACESTEP_REPAINT_DEFAULT_STEPS,
        guidance_scale: float = 15.0,
        cpu_offload: bool = False,
        seed: int = 0,
        dest: str = "",
    ) -> str:
        """Regenerate seconds ``[repaint_start, repaint_end]`` of an existing
        take and keep the rest. The audio-to-audio sibling of
        ``generate_music`` — this one starts from your audio, not from noise.

        ``src_audio`` is a LOCAL path; it is uploaded into ComfyUI's input/
        (``/upload/image`` is the generic upload endpoint and accepts audio).

        THE MODEL IS NOT THE ONE ``generate_music`` USES. Compose runs ACE-Step
        1.5 turbo through core ComfyUI; every studio verb belongs to the
        ComfyUI_ACE-Step (MW) pack, which loads ACE-Step 1.0 3.5B from
        ``models/TTS/ACE-Step-v1-3.5B``. There is no 1.5 repaint node on this
        install, so the window is voiced by a different model than the track
        around it and the seam is a listening call, not a parameter.

        The region OUTSIDE the window is preserved but NOT bit-identical: the
        pipeline encodes the whole track to latents, replaces only the masked
        frames, then decodes everything, so the kept audio is a VAE round-trip
        of the original.

        VERIFYING THAT COSTS MORE CARE THAN IT LOOKS. A hash mismatch on the
        kept region is the expected result, not a bug — but so is a *waveform*
        correlation of ~0. The round-trip reconstructs the waveform without
        preserving phase, and it shifted this file by 197 samples (4.1 ms), and
        raw-waveform Pearson r collapses to zero on either. Measured 2026-08-15:
        waveform r on the untouched head read -0.25 (which reads as "the whole
        track was destroyed") while log-mel r on the same audio read +0.97.
        Compare magnitude spectrograms, per-second, and read the profile rather
        than a segment average — the profile is what shows the edit landing on
        the seconds you asked for.

        ``prompt`` and ``lyrics`` are REQUIRED, and must be the ones the take
        was generated with — the whole song's, not the window's. They have no
        defaults because an empty ``lyrics`` is not a neutral choice: the model
        regenerates the window with no lyric conditioning and invents new
        vocals over it. Measured 2026-08-16, repainting 20-40s of a take whose
        words were known: empty lyrics scored 0.149 word-similarity against the
        source (pure gibberish), the correct lyrics scored 0.778. Pass "" only
        for a genuinely instrumental section, as a deliberate act.

        Even with correct lyrics the vocal is only held below
        ``ACESTEP_REPAINT_LYRIC_SAFE_VARIANCE``; above it the model re-derives
        lyric alignment and sings a different part of the song inside the
        window. That failure is invisible to every spectral check, so this
        method warns rather than trusting the measurement.

        ``variance`` is the strength dial and the thing most worth getting
        right; see ``ACESTEP_REPAINT_DEFAULT_VARIANCE``. Returns the ComfyUI
        filename, or the local path when ``dest`` is given, or "" on failure.
        """
        from pathlib import Path

        src = Path(src_audio)
        if not src.exists():
            raise FileNotFoundError(f"repaint source not found: {src}")

        repaint_start = float(repaint_start)
        repaint_end = float(repaint_end)
        if repaint_start < 0:
            raise ValueError("repaint_start must be >= 0")
        if repaint_end <= repaint_start:
            raise ValueError(
                f"repaint window is empty or inverted: "
                f"[{repaint_start}, {repaint_end}]"
            )
        if not 0.01 <= variance <= 1.0:
            raise ValueError("repaint variance must be between 0.01 and 1.0")
        steps = int(steps)
        if not 1 <= steps <= ACESTEP_MAX_STEPS:
            raise ValueError(f"repaint steps must be 1..{ACESTEP_MAX_STEPS}")

        if seed == 0:
            seed = random.randint(1, 2**32 - 1)

        # Warn, don't refuse: an instrumental window legitimately goes higher,
        # and only the caller knows whether this span has a vocal in it.
        if lyrics.strip() and variance > ACESTEP_REPAINT_LYRIC_SAFE_VARIANCE:
            self.kernel.syslog.warning(
                "comfyui",
                f"repaint variance {variance} is above "
                f"{ACESTEP_REPAINT_LYRIC_SAFE_VARIANCE} with lyrics present — "
                "the window will likely sing a different part of the song. "
                "Spectral checks cannot see this; listen to the window.",
            )

        uploaded = await self.upload_image(src)
        if not uploaded:
            self.kernel.syslog.error(
                "comfyui", f"repaint: failed to upload {src.name} to ComfyUI",
            )
            return ""

        # The window is what the caller is paying for, and it is silently
        # clamped to the source duration deeper in the node, so log it.
        self.kernel.syslog.info(
            "comfyui",
            f"ACE-Step repaint: {uploaded} window=[{repaint_start:.1f}s, "
            f"{repaint_end:.1f}s] variance={variance} steps={steps} seed={seed}",
        )

        filename = await self.generate_from_workflow(
            workflow_key="music_repaint",
            prompt=prompt,
            seed=seed,
            # Config wins, bundled template is the fallback, so a fresh clone
            # works without a toml entry (as generate_dialogue does).
            template_path=self.config("music_repaint_workflow", "") or str(
                Path(__file__).parent / "workflows" / "acestep_repaint.json"
            ),
            substitutions={
                "src_audio": uploaded,
                "lyrics": lyrics,
                "negative_prompt": negative_prompt,
                "repaint_start": int(repaint_start),
                "repaint_end": int(repaint_end),
                "repaint_variance": float(variance),
                "steps": steps,
                "guidance_scale": float(guidance_scale),
                "cpu_offload": bool(cpu_offload),
                # NOT `seed`: GenerationParameters only converts this into
                # `manual_seeds` when it is nonzero, and a literal 0 survives
                # into pipeline.__call__ — which has no `seed` parameter — so
                # the run dies with a TypeError *after* loading 7.6GB of
                # weights. Reuse the resolved seed; it is already nonzero.
                "param_seed": seed,
            },
            output_keys=("audio", "audios"),
            preflight_kind="audio",
        )
        if not filename or not dest:
            return filename
        destination = Path(dest)
        destination.parent.mkdir(parents=True, exist_ok=True)
        ok = await self.download_image(filename, destination)
        return str(destination) if ok else ""

    async def extend_music(
        self,
        src_audio: str,
        *,
        prompt: str,
        lyrics: str,
        left_seconds: float = 0.0,
        right_seconds: float = 0.0,
        negative_prompt: str = "",
        steps: int = ACESTEP_REPAINT_DEFAULT_STEPS,
        guidance_scale: float = 15.0,
        cpu_offload: bool = False,
        seed: int = 0,
        dest: str = "",
    ) -> str:
        """Grow an existing take at its head and/or tail — the intro/outro verb.

        Sibling of ``repaint_music``: same model, same upload path, same
        required ``prompt``/``lyrics`` contract and the same reason for it
        (an empty ``lyrics`` makes the model invent vocals).

        Three ways it is NOT repaint, all of which have bitten:

        * **No ``variance``.** ``ACEStepExtend`` exposes none, so there is no
          strength dial to pass and none to warn about. The mask covers only
          new territory, so the original audio is not at risk the way a
          repaint window is.
        * **The duration changes.** Output length is roughly
          ``source + left + right``. Any check that assumes equal length —
          ``scripts/measure_repaint.py`` does — must compare only the
          overlapping original span.
        * **The new material still draws from the WHOLE song's lyrics, and it
          REPEATS rather than continues.** Measured 2026-08-16, +15s on a 60s
          take whose words were known: the grown tail sang verse lines 3-4
          again — audio already present at 20-30s of the source. The original
          span was genuinely preserved (log-mel r=+0.950 across all 60s), so
          this is a lyric-selection behaviour, not a re-render. Treat extend as
          "grow more of this song", never "write the next section"; if the
          outro needs different words, that is an ``ACEStepEdit`` job.

        Cost note: a run is 10-20s when the ACE weights are already resident
        and ~280s when they are not. The first extend after any other model has
        used the GPU pays the reload, which is worth knowing before attributing
        it to the verb.

        Lengths are seconds added OUTSIDE the source; the node converts them
        to ``repaint_start=-left`` / ``repaint_end=duration+right`` internally.
        Returns the ComfyUI filename, the local path when ``dest`` is given,
        or "" on failure.
        """
        from pathlib import Path

        src = Path(src_audio)
        if not src.exists():
            raise FileNotFoundError(f"extend source not found: {src}")

        left_seconds = float(left_seconds)
        right_seconds = float(right_seconds)
        if left_seconds < 0 or right_seconds < 0:
            raise ValueError("extend lengths must be >= 0")
        if left_seconds <= 0 and right_seconds <= 0:
            raise ValueError(
                "extend needs a nonzero left_seconds or right_seconds — "
                "otherwise it would re-render the take without growing it"
            )
        # The node's own ceiling; beyond it ComfyUI rejects the prompt after
        # the upload has already been paid for.
        if left_seconds > 1000 or right_seconds > 1000:
            raise ValueError("extend lengths must be <= 1000 seconds")
        steps = int(steps)
        if not 1 <= steps <= ACESTEP_MAX_STEPS:
            raise ValueError(f"extend steps must be 1..{ACESTEP_MAX_STEPS}")

        if seed == 0:
            seed = random.randint(1, 2**32 - 1)

        uploaded = await self.upload_image(src)
        if not uploaded:
            self.kernel.syslog.error(
                "comfyui", f"extend: failed to upload {src.name} to ComfyUI",
            )
            return ""

        self.kernel.syslog.info(
            "comfyui",
            f"ACE-Step extend: {uploaded} +{left_seconds:.0f}s head "
            f"+{right_seconds:.0f}s tail steps={steps} seed={seed}",
        )

        filename = await self.generate_from_workflow(
            workflow_key="music_extend",
            prompt=prompt,
            seed=seed,
            template_path=self.config("music_extend_workflow", "") or str(
                Path(__file__).parent / "workflows" / "acestep_extend.json"
            ),
            substitutions={
                "src_audio": uploaded,
                "lyrics": lyrics,
                "negative_prompt": negative_prompt,
                "left_extend_length": int(left_seconds),
                "right_extend_length": int(right_seconds),
                "steps": steps,
                "guidance_scale": float(guidance_scale),
                "cpu_offload": bool(cpu_offload),
                # Same trap as repaint: a literal 0 here reaches a pipeline
                # with no `seed` argument and dies after the models load.
                "param_seed": seed,
            },
            output_keys=("audio", "audios"),
            preflight_kind="audio",
        )
        if not filename or not dest:
            return filename
        destination = Path(dest)
        destination.parent.mkdir(parents=True, exist_ok=True)
        ok = await self.download_image(filename, destination)
        return str(destination) if ok else ""

    async def generate_dialogue(
        self,
        script_text: str,
        speaker_refs: list[str] | None = None,
        dest: str = "",
        seed: int = 0,
        template_path: str = "",
    ) -> str:
        """Single-pass multi-speaker dialogue via the VibeVoice ComfyUI node.

        ``script_text`` is the full transcript with speaker labels
        (``"Speaker 1: ...\\nSpeaker 2: ..."``). ``speaker_refs`` is a list of
        LOCAL reference-voice wav paths, one per speaker slot (index 0 → speaker
        1); each is uploaded into ComfyUI's input/ for voice-cloning, "" entries
        are left zero-shot. Writes the produced audio to ``dest`` and returns
        ``str(dest)``, or "" on any failure (callers fall back to per-turn TTS).

        Targets the ComfyUI-VibeVoice custom node — node/class names are
        best-effort against its documented IO. Adjust the workflow template +
        ``[plugins.comfyui] vibevoice_workflow`` after inspecting
        ``GET /object_info/VibeVoiceTTS`` on your install. This is intentionally
        a contained plugin method (not a `speak` capability provider): the
        podcast app is the only consumer today and multi-speaker doesn't fit the
        single-utterance speak contract. Graduate to a capability when a 2nd
        consumer appears (CLAUDE.md rule 9).
        """
        import copy
        from pathlib import Path

        if not await self.available():
            return ""

        speaker_refs = speaker_refs or []

        path_str = template_path or self.config("vibevoice_workflow", "")
        if path_str:
            tmpl_path = Path(path_str)
            if not tmpl_path.is_absolute():
                tmpl_path = Path(self.kernel.config.path).parent / tmpl_path
        else:
            tmpl_path = Path(__file__).parent / "workflows" / "vibevoice_dialogue.json"
        if not tmpl_path.exists():
            self.kernel.syslog.warning("comfyui", f"vibevoice_workflow not found: {tmpl_path}")
            return ""

        # Upload each reference voice into ComfyUI input/; collect server-side
        # filenames (upload_image posts to /upload/image, which accepts any file).
        ref_names: list[str] = []
        for ref in speaker_refs:
            ref_names.append(await self.upload_image(ref) if ref else "")

        if seed == 0:
            seed = random.randint(0, 2**32 - 1)

        try:
            workflow = json.loads(tmpl_path.read_text(encoding="utf-8"))
        except Exception as e:
            self.kernel.syslog.error("comfyui", f"vibevoice_workflow parse error: {e}")
            return ""
        workflow = {k: v for k, v in workflow.items() if not k.startswith("_")}

        # Zero-shot pruning: for any speaker slot WITHOUT a reference wav, drop
        # its LoadAudio node + the speaker_N_voice connection so VibeVoice uses
        # its own natural voice. speaker_N_voice is an optional node input;
        # cloning a robotic/empty reference would degrade or fail the run.
        vv_nodes = [n for n in workflow.values()
                    if isinstance(n, dict) and n.get("class_type") == "VibeVoiceTTS"]
        for i, ref in enumerate(ref_names, start=1):
            if ref:
                continue
            ph = "{speaker%d}" % i
            for nid in [nid for nid, node in workflow.items()
                        if isinstance(node, dict)
                        and node.get("class_type") == "LoadAudio"
                        and node.get("inputs", {}).get("audio") == ph]:
                workflow.pop(nid, None)
            for vv in vv_nodes:
                vv.get("inputs", {}).pop("speaker_%d_voice" % i, None)

        def _sub(node):
            if isinstance(node, dict):
                return {k: _sub(v) for k, v in node.items()}
            if isinstance(node, list):
                return [_sub(x) for x in node]
            if isinstance(node, str):
                if node == "{seed}":
                    return int(seed)
                out = node.replace("{script}", script_text).replace("{seed}", str(seed))
                for i, nm in enumerate(ref_names, start=1):
                    out = out.replace("{speaker%d}" % i, nm)
                return out
            return node

        workflow = _sub(copy.deepcopy(workflow))

        try:
            self.kernel.syslog.info(
                "comfyui", f"Queuing VibeVoice dialogue ({len(script_text)} chars)"
            )
            item = await self._submit_and_poll(
                workflow, output_keys=("audio", "audios"),
                post_timeout=900, max_polls=600,
            )
            if not item:
                return ""

            # Fetch the produced audio, honoring subfolder/type from the output.
            params = {"filename": item.get("filename", "")}
            if item.get("subfolder"):
                params["subfolder"] = item["subfolder"]
            if item.get("type"):
                params["type"] = item["type"]
            async with self._session.get(
                f"{self._host()}/view",
                params=params,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status != 200:
                    return ""
                blob = await resp.read()
            dest_path = Path(dest)
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_bytes(blob)
            return str(dest_path)
        except Exception as e:
            self.kernel.syslog.error("comfyui", f"VibeVoice dialogue failed: {e}")
            return ""
        finally:
            await self.free_gpu()

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

    async def generate_guided_video(
        self,
        prompt: str,
        depth_dir: str,
        *,
        num_frames: int = 49,
        width: int = 832,
        height: int = 480,
        seed: int = 0,
        negative_prompt: str = "",
        template_path: str = "",
    ) -> str:
        """Render video whose CAMERA MOVE comes from a depth sequence, not a prompt.

        ``depth_dir`` is an absolute path to a directory of lossless per-frame
        depth PNGs (see ``scripts/blockout_depth_probe.py``). Geometry comes
        from that sequence; appearance comes from ``prompt``. Validated
        2026-07-28 — with the sequence attached the camera executes the move,
        and without it the identical seed/prompt produces a static shot
        (docs/GUIDED-GENERATION.md §11).

        Returns the produced video filename, or "" on failure — the same
        fail-soft contract as ``animate``/``generate_depth``, so a caller that
        does not log an empty return will silently drop the scene.

        Defaults match the validated envelope: Wan 2.1 1.3B is a 480p model, and
        1024x576 hard-crashed ComfyUI on a 16GB card. Frames must sit on the
        4n+1 grid and dimensions on the 32-grid; 49 / 832x480 satisfy both.
        """
        return await self.generate_from_workflow(
            workflow_key="vace",
            prompt=prompt,
            num_frames=num_frames,
            seed=seed,
            width=width,
            height=height,
            negative_prompt=negative_prompt,
            template_path=template_path,
            substitutions={"depth_dir": str(depth_dir).replace("\\", "/")},
            preflight_kind="video",
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
            # Own the stream lifetime explicitly. aiohttp consumes it during the
            # request but does not guarantee prompt closure of a caller-opened
            # file on Windows; the leaked handle aborted chained video hand-off.
            with p.open("rb") as stream:  # noqa: ASYNC230
                data = aiohttp.FormData()
                data.add_field(
                    "image",
                    stream,
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

    async def download_image(self, filename: str, dest, *, attempts: int = 3) -> bool:
        """Fetch a generated file from ComfyUI and write it to dest (Path or str).

        Retries, and records *why* a fetch failed. Both matter because this is
        the last step of an already-paid render: on 2026-07-28 two separate
        121-frame Wan generations (~5 GPU-minutes each) completed successfully
        and wrote valid 1.08MB mp4s, and this method returned False one second
        later — the bare ``except`` erased the reason, and the video caller
        reports the loss as "likely OOM or a workflow node error", which sent
        every investigation to the wrong place. ``/view`` served both files in
        4ms when asked again, so the failure was transient and one retry would
        have saved both renders.

        Still returns a bool — callers decide raise-vs-"" — but a failure is
        now always explained in syslog rather than silently swallowed.
        """
        import asyncio
        from pathlib import Path

        if not filename:
            return False
        reason = "unknown"
        for attempt in range(1, max(1, int(attempts)) + 1):
            try:
                async with self._session.get(
                    f"{self._host()}/view",
                    params=self._view_params(filename),
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status != 200:
                        reason = f"HTTP {resp.status}"
                        data = None
                    else:
                        data = await resp.read()
                if data is not None:
                    dest_path = Path(dest)
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    dest_path.write_bytes(data)
                    if attempt > 1:
                        self.kernel.syslog.info(
                            "comfyui",
                            f"/view fetch of {filename} succeeded on attempt "
                            f"{attempt} (first failure: {reason})",
                        )
                    return True
            except Exception as e:
                reason = repr(e)
            if attempt < max(1, int(attempts)):
                await asyncio.sleep(1.0 * attempt)
        self.kernel.syslog.error(
            "comfyui",
            f"/view fetch of {filename} failed after {attempts} attempts "
            f"({reason}) — the render itself may have succeeded; check "
            f"ComfyUI's output directory before assuming OOM",
        )
        return False

    #: Rough VRAM cost per job kind, in GB. Estimates, deliberately: they are
    #: overridable via ``[plugins.comfyui] vram_need_<key>`` and the check they
    #: feed is advisory, so a wrong number costs at worst one unnecessary ollama
    #: unload — never a refused render.
    _NEED_GB = {"image": 12.0, "video": 18.0, "depth": 2.0, "audio": 8.0}

    async def _preflight(self, kind: str) -> None:
        """Advisory VRAM check before queueing. Fail-soft; returns None always.

        Dark by default — set ``[plugins.comfyui] feature.gpu-arbiter.enabled``
        to turn it on. When off, this is a single config read and a return, so
        the queueing path is unchanged.

        Deliberately does not block. The behaviour it improves on was to queue
        blind and discover the problem as a ten-minute timeout; the win is
        evicting a resident LLM first and leaving a syslog line that explains
        the failure when it still happens.
        """
        try:
            if not self.config("feature.gpu-arbiter.enabled", False):
                return
            health = self.kernel.services.get("health") if self.kernel else None
            if not health or not hasattr(health, "gpu_reserve"):
                return
            need = float(self.config(f"vram_need_{kind}", self._NEED_GB.get(kind, 0.0)))
            if need <= 0:
                return
            verdict = await health.gpu_reserve(need)
            self.kernel.syslog.info(
                "comfyui",
                f"gpu preflight {kind}: need={need}GB "
                f"headroom={verdict.get('headroom_gb')}GB "
                f"reason={verdict.get('reason')} "
                f"freed={bool(verdict.get('freed'))}",
            )
        except Exception as e:  # never let the guard break a render
            try:
                self.kernel.syslog.warning("comfyui", f"gpu preflight skipped ({kind}): {e}")
            except Exception:
                pass

    @asynccontextmanager
    async def gpu_session(self, reason: str = ""):
        """Keep ComfyUI's models resident for the duration of a block.

        Without this, ``free_gpu()`` runs in the ``finally`` of *every* image
        generation, so a storyboard of N scenes evicts and reloads the same
        checkpoint N times. On a card with room to spare that is pure waste.

        Scope this per **stage**, not per run. The MV pipeline's clip stage
        genuinely needs the still-image model gone before the video model
        loads, so one run-wide session would be actively wrong — wrap
        ``stills`` and ``clips`` separately.

        Nestable; frees once on exit from the outermost block, and does so even
        if the body raises.
        """
        self._session_depth = getattr(self, "_session_depth", 0) + 1
        try:
            yield self
        finally:
            self._session_depth = max(0, getattr(self, "_session_depth", 1) - 1)
            if self._session_depth == 0:
                await self.free_gpu()

    async def _maybe_free_gpu(self):
        """Free VRAM unless we're inside a session with room to keep models.

        Replaces the unconditional ``free_gpu()`` in the per-generation
        ``finally`` blocks. Order matters:

        1. Residency flag off  -> free (byte-for-byte today's behaviour).
        2. Inside a session AND measured headroom clears the reserve -> skip.
        3. Otherwise -> free.

        Step 2 is expressed in **measured headroom**, not card size, which is
        what makes this non-regressing on a 16 GB box: there the still->video
        transition simply fails the headroom test and falls through to a free,
        with no special-casing anywhere.
        """
        try:
            if not self.config("feature.model-residency.enabled", False):
                await self.free_gpu()
                return
            if getattr(self, "_session_depth", 0) <= 0:
                await self.free_gpu()
                return
            health = self.kernel.services.get("health") if self.kernel else None
            status = await health.gpu_status() if health else None
            headroom = float((status or {}).get("headroom_gb", 0) or 0)
            floor = float(self.config("residency_min_vram_gb", 8.0))
            if headroom >= floor:
                return  # room to spare — keep the model warm for the next scene
        except Exception:
            pass  # any doubt -> fall through and free, i.e. today's behaviour
        await self.free_gpu()

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
        negative_extra: str = "",
        overlay_title: str = "",
        overlay_subtitle: str = "",
        overlay_font: str = "",
    ) -> dict:
        """Build ComfyUI workflow. Handles FLUX.1/SDXL/SD1.5 + LoRA, FLUX.2 Klein.

        ``negative_extra`` is appended to the style's negative prompt so callers
        can steer the *negative* channel (e.g. "text, logos, watermarks") instead
        of stuffing negation phrases into the positive prompt — where CLIP handles
        them poorly and they eat the 77-token CLIP-L budget.

        ``overlay_title`` / ``overlay_subtitle`` composite crisp text onto the
        final image via a ``DrawText+`` node — the reliable way to put legible
        text on a generated cover instead of trusting the model to render it
        (see _append_text_overlay). Opt-in; empty = no overlay.
        """
        if style.get("is_flux2"):
            return self._build_flux2_workflow(
                prompt, width, height, seed, style,
                overlay_title, overlay_subtitle, overlay_font,
            )

        ckpt = style.get("checkpoint", "flux1-dev-fp8.safetensors")
        is_flux = "flux" in ckpt.lower()

        steps = style.get("steps", 30 if is_flux else 25)
        cfg = style.get("cfg", 1.0 if is_flux else 7.0)
        sampler = style.get("sampler", "euler" if is_flux else "dpmpp_2m")
        scheduler = style.get("scheduler", "simple" if is_flux else "normal")
        negative = style.get("negative", "ugly, blurry, low quality, deformed")
        if negative_extra.strip():
            negative = f"{negative}, {negative_extra.strip()}" if negative else negative_extra.strip()

        workflow = {
            "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"batch_size": 1, "height": height, "width": width},
            },
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
            "9": {
                "class_type": "SaveImage",
                "inputs": {"filename_prefix": self._dated_prefix("eos"), "images": ["8", 0]},
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
        # Optional text overlay (no-op unless a title/subtitle was passed).
        workflow["9"]["inputs"]["images"] = self._append_text_overlay(
            workflow, ["8", 0], overlay_title, overlay_subtitle, overlay_font,
        )
        return workflow

    def _build_flux2_workflow(
        self,
        prompt: str,
        width: int,
        height: int,
        seed: int,
        style: dict,
        overlay_title: str = "",
        overlay_subtitle: str = "",
        overlay_font: str = "",
    ) -> dict:
        """FLUX.2 Klein graph — UNETLoader + flux2 CLIP + flux2 VAE + guidance-
        distilled sampler (no negative channel). Encoder pairing is carried by
        the preset (``clip``); do not swap it — 4B needs qwen_3_4b, 9B qwen_3_8b.

        When an overlay is requested the positive prompt is nudged toward clean,
        text-free art so the model stops baking in (usually misspelled) text and
        leaves room for the real DrawText+ layer.
        """
        unet = style.get("unet", "flux-2-klein-4b-fp8.safetensors")
        clip = style.get("clip", "qwen_3_4b.safetensors")
        vae = style.get("vae", "flux2-vae.safetensors")
        steps = style.get("steps", 6)
        sampler = style.get("sampler", "euler")

        text = prompt
        if overlay_title or overlay_subtitle:
            text = f"{prompt}{FLUX2_TEXT_FREE_SUFFIX}"

        workflow = {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
            "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": clip, "type": "flux2", "device": "default"}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
            "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": text}},
            "5": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        }
        decoded = self._flux2_sample_tail(
            workflow, model_ref=["1", 0], cond_ref=["4", 0], canvas_ref=["5", 0],
            vae_ref=["3", 0], steps=steps, sampler=sampler,
            width=width, height=height, seed=seed,
        )
        img_ref = self._append_text_overlay(
            workflow, decoded, overlay_title, overlay_subtitle, overlay_font,
        )
        workflow["20"] = {"class_type": "SaveImage", "inputs": {"filename_prefix": self._dated_prefix("eos"), "images": img_ref}}
        return workflow

    def _flux2_sample_tail(
        self,
        wf: dict,
        *,
        model_ref: list,
        cond_ref: list,
        canvas_ref: list,
        vae_ref: list,
        steps: int,
        sampler: str,
        width: int,
        height: int,
        seed: int,
    ) -> list:
        """Append the shared FLUX.2 sampling tail — Flux2Scheduler → KSamplerSelect
        → RandomNoise → BasicGuider → SamplerCustomAdvanced → VAEDecode — onto
        ``wf`` and return the decoded IMAGE ref. Both the text-to-image and the
        reference-edit builders end this way; keeping it in one place means a
        sampler fix can't drift between them. ``cond_ref`` carries the (already
        reference-augmented, for edits) conditioning; ``canvas_ref`` is the
        latent to sample (empty canvas for t2i/compose, the encoded reference for
        an in-place edit).
        """
        wf["sch"] = {"class_type": "Flux2Scheduler", "inputs": {"steps": steps, "width": width, "height": height}}
        wf["sel"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": sampler}}
        wf["noise"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}}
        wf["guider"] = {"class_type": "BasicGuider", "inputs": {"model": model_ref, "conditioning": cond_ref}}
        wf["samp"] = {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["noise", 0], "guider": ["guider", 0], "sampler": ["sel", 0],
            "sigmas": ["sch", 0], "latent_image": canvas_ref}}
        wf["dec"] = {"class_type": "VAEDecode", "inputs": {"samples": ["samp", 0], "vae": vae_ref}}
        return ["dec", 0]

    def _append_text_overlay(
        self,
        workflow: dict,
        image_ref: list,
        title: str = "",
        subtitle: str = "",
        font: str = "",
    ) -> list:
        """Composite crisp text onto ``image_ref`` via ``DrawText+`` (from the
        comfyui_essentials custom node) and return the new image output ref.

        No-op (returns ``image_ref`` unchanged) when neither title nor subtitle
        is given, so it's safe to call on every build. This is the reliable
        text-on-cover path — real font, perfect kerning, zero spelling risk —
        vs. asking a diffusion model to render letters. Default font ships with
        comfyui_essentials; pass ``font`` to use another (drop the .ttf/.otf in
        the node's fonts/ dir first).
        """
        title = (title or "").strip()
        subtitle = (subtitle or "").strip()
        if not title and not subtitle:
            return image_ref
        font = font or "ShareTechMono-Regular.ttf"
        ref = image_ref
        if title:
            workflow["t_title"] = {"class_type": "DrawText+", "inputs": {
                "img_composite": ref, "text": title, "font": font, "size": 150,
                "color": "#12202b", "background_color": "#00000000",
                "shadow_distance": 3, "shadow_blur": 8, "shadow_color": "#66ffffff",
                "horizontal_align": "center", "vertical_align": "center",
                "offset_x": 0, "offset_y": -45}}
            ref = ["t_title", 0]
        if subtitle:
            workflow["t_sub"] = {"class_type": "DrawText+", "inputs": {
                "img_composite": ref, "text": subtitle, "font": font, "size": 44,
                "color": "#37454f", "background_color": "#00000000",
                "shadow_distance": 0, "shadow_blur": 0, "shadow_color": "#000000",
                "horizontal_align": "center", "vertical_align": "center",
                "offset_x": 0, "offset_y": 85}}
            ref = ["t_sub", 0]
        return ref

    def _build_flux2_edit_workflow(
        self,
        instruction: str,
        width: int,
        height: int,
        seed: int,
        style: dict,
        ref_names: list,
        overlay_title: str = "",
        overlay_subtitle: str = "",
        overlay_font: str = "",
    ) -> dict:
        """FLUX.2 Klein reference-conditioned edit / compose graph.

        Each reference image is scaled → VAE-encoded → attached to the text
        conditioning via a chained ``ReferenceLatent`` (Kontext-style: the image
        rides along as conditioning while the instruction drives the change).

        - **One** ref → instruction edit / restyle; the ref latent IS the
          sampling canvas, so composition + size are preserved.
        - **Two+** refs → multi-reference compose onto a fresh canvas (combine
          subjects from several inputs into one new scene).
        """
        unet = style.get("unet", "flux-2-klein-9b-fp8.safetensors")
        clip = style.get("clip", "qwen_3_8b_fp8mixed.safetensors")
        vae = style.get("vae", "flux2-vae.safetensors")
        steps = style.get("steps", 8)
        sampler = style.get("sampler", "euler")

        wf = {
            "u": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
            "c": {"class_type": "CLIPLoader", "inputs": {"clip_name": clip, "type": "flux2", "device": "default"}},
            "v": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
            "txt": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["c", 0], "text": instruction}},
        }
        cond = ["txt", 0]
        ref_latents = []
        for i, name in enumerate(ref_names):
            wf[f"ld{i}"] = {"class_type": "LoadImage", "inputs": {"image": name}}
            wf[f"sc{i}"] = {"class_type": "FluxKontextImageScale", "inputs": {"image": [f"ld{i}", 0]}}
            wf[f"en{i}"] = {"class_type": "VAEEncode", "inputs": {"pixels": [f"sc{i}", 0], "vae": ["v", 0]}}
            wf[f"rl{i}"] = {"class_type": "ReferenceLatent", "inputs": {"conditioning": cond, "latent": [f"en{i}", 0]}}
            cond = [f"rl{i}", 0]
            ref_latents.append([f"en{i}", 0])

        if len(ref_latents) == 1:
            canvas = ref_latents[0]  # edit in place → preserve structure + dims
        else:
            wf["empty"] = {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}}
            canvas = ["empty", 0]

        decoded = self._flux2_sample_tail(
            wf, model_ref=["u", 0], cond_ref=cond, canvas_ref=canvas,
            vae_ref=["v", 0], steps=steps, sampler=sampler,
            width=width, height=height, seed=seed,
        )
        img_ref = self._append_text_overlay(wf, decoded, overlay_title, overlay_subtitle, overlay_font)
        wf["save"] = {"class_type": "SaveImage", "inputs": {"filename_prefix": self._dated_prefix("eos-edit"), "images": img_ref}}
        return wf

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
        negative: str = "",
        overlay_title: str = "",
        overlay_subtitle: str = "",
        overlay_font: str = "",
        image: str = "",
        seed: int = 0,
        **_kwargs,
    ) -> str:
        """Generate image. Returns filename.

        If style is given, applies preset (checkpoint, sampler, cfg, prefix/suffix, LoRA).
        ``negative`` steers the negative prompt channel (appended to the style's).
        ``overlay_title`` / ``overlay_subtitle`` composite legible text onto the
        result (e.g. a commercial cover with Klein 4B) — reliable text without
        trusting the model to spell. Needs the comfyui_essentials custom node.
        ``image`` (the ``draw`` capability's input-image slot) routes to
        ``edit_image`` — the prompt becomes the edit instruction, ``style`` picks
        the Klein model (default klein-9b). Use ``edit_image`` directly for
        multi-reference.

        ``seed`` of 0 (the default) keeps the historical per-call randomness, so
        existing callers are unaffected. Pass a value to make a still
        reproducible — e.g. an MV render walking ``base_seed + index * 1000``
        so a re-run regenerates the same frames.
        """
        if image:
            return await self.edit_image(
                image, prompt, model=(style or "klein-9b"),
                width=width, height=height,
                overlay_title=overlay_title, overlay_subtitle=overlay_subtitle,
                overlay_font=overlay_font,
            )

        await self._ensure_runtime_compatible()
        await self._preflight("image")

        if not seed:
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
            negative_extra=negative,
            overlay_title=overlay_title,
            overlay_subtitle=overlay_subtitle,
            overlay_font=overlay_font,
        )

        try:
            self.kernel.syslog.info(
                "comfyui",
                f"Queuing image: {prompt[:80]}...",
                data={"width": w, "height": h, "style": style},
            )
            # 120 polls x 1.5s = 180s, which a FLUX cover on a busy GPU overruns —
            # ComfyUI finishes and the caller has already given up, so the image is
            # rendered, paid for, and thrown away. Video/audio already override the
            # default for the same reason; image was the one path still on it.
            item = await self._submit_and_poll(
                workflow, output_keys=("images",), post_timeout=300, max_polls=400,
            )
            return self._item_path(item)
        except Exception as e:
            raise RuntimeError(f"ComfyUI generation failed: {e}") from e
        finally:
            await self._maybe_free_gpu()

    async def edit_image(
        self,
        src,
        instruction: str,
        *,
        model: str = "klein-9b",
        refs: list | None = None,
        width: int = 1024,
        height: int = 1024,
        overlay_title: str = "",
        overlay_subtitle: str = "",
        overlay_font: str = "",
        seed: int | None = None,
    ) -> str:
        """Reference-conditioned image edit / restyle / multi-reference compose
        via FLUX.2 Klein. Returns the output filename, or "" on failure.

        ``src`` (and each entry of ``refs``) may be a **local path** — uploaded
        to ComfyUI's input/ first — or a filename **already** in that input dir.
        One image → instruction edit / restyle (composition preserved). ``src``
        plus one or more ``refs`` → multi-reference compose (combine subjects
        into a new scene). ``instruction`` is the edit prompt. Editing needs a
        FLUX.2 Klein ``model`` preset (klein-9b default — cleaner edits than 4B).
        """
        from pathlib import Path

        style = STYLE_PRESETS.get(model, {})
        if not style.get("is_flux2"):
            raise ValueError(
                f"edit_image needs a FLUX.2 Klein model (klein-4b/klein-9b); got '{model}'"
            )

        await self._ensure_runtime_compatible()
        await self._preflight("image")

        ref_names: list[str] = []
        for s in [src, *(refs or [])]:
            if not s:
                continue
            if Path(str(s)).exists():
                name = await self.upload_image(s)
                if not name:
                    return ""
                ref_names.append(name)
            else:
                ref_names.append(str(s))  # assume already in ComfyUI input/
        if not ref_names:
            return ""

        seed = random.randint(0, 2**32 - 1) if seed is None else seed
        workflow = self._build_flux2_edit_workflow(
            instruction, width, height, seed, style, ref_names,
            overlay_title=overlay_title, overlay_subtitle=overlay_subtitle,
            overlay_font=overlay_font,
        )
        try:
            self.kernel.syslog.info(
                "comfyui",
                f"Editing image ({len(ref_names)} ref): {instruction[:70]}...",
                data={"model": model, "refs": len(ref_names)},
            )
            item = await self._submit_and_poll(
                workflow, output_keys=("images",), post_timeout=300, max_polls=400,
            )
            return self._item_path(item)
        except Exception as e:
            raise RuntimeError(f"ComfyUI edit failed: {e}") from e
        finally:
            await self._maybe_free_gpu()
