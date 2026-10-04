"""Manim plugin — precise, frame-accurate programmatic animation.

Wraps ManimCommunity/manim (a scene-graph animation engine — `Mobject`s +
declarative `Animation` objects that are pure functions of alpha in [0,1],
Scene owns the clock, rendered to mp4 via Cairo + ffmpeg). Runs in a
user-home Python 3.12 venv, same shape as `plugins/cadquery` and
`plugins/markitdown` (`.claude/rules/environment.md` § User-home Python
envs) — the daemon interpreter never imports manim directly.

Why this exists: `apps/public/standard/viz`'s `anim-explainer` (Web
Animations API, LLM-improvised timing) and `math-explainer` (static KaTeX)
shapes cannot promise exact interpolation/easing or a LaTeX-typeset morph —
that's the one thing this plugin adds. See
`docs/OPEN-SOURCE-BORROWING-PLAN.md` § ManimCommunity/manim (2026-08-22)
for the full borrow verdict and `.claude/rules/environment.md` for the venv
setup command.

    [plugins.manim]
    python_exe = "C:/Users/<you>/AppData/Local/eos/envs/manim-3.12/Scripts/python.exe"

If unset, the plugin tries the canonical default
`%LOCALAPPDATA%/eos/envs/manim-3.12/Scripts/python.exe` (Windows) or
`~/.local/eos/envs/manim-3.12/bin/python` (POSIX).

Public service (apps reach via `self.require("manim")`):
    async def render_scene(self, source, *, scene_class="Scene1",
                           quality="m", out_dir=None,
                           background_color=None, timeout=180.0) -> dict
        Run `source` (a self-contained Manim scene script) through the
        runner; returns {ok, stage, path?, error?}.

LaTeX note: `MathTex`/`Tex` mobjects need a separate LaTeX toolchain
(MiKTeX/TeX Live) that this plugin does NOT install or require — Manim's
shape-based mobjects (Text via Pango, Circle/Square/Line/Arrow/NumberPlane,
etc.) render without it. `available()` never checks for LaTeX; a scene
using MathTex on a machine without LaTeX fails at render time with a clear
manim error in the returned report, not a plugin-level failure.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from emptyos.sdk import BasePlugin
from emptyos.sdk.userhome_venv import default_venv_python, probe_launch, run_json_script


def _default_python_exe() -> str:
    """Canonical user-home path for the 3.12 venv.
    See .claude/rules/environment.md § User-home Python envs."""
    return default_venv_python("manim", "3.12")


class ManimPlugin(BasePlugin):
    name = "manim"

    def __init__(self, kernel, manifest):
        super().__init__(kernel, manifest)
        self._python_exe: str = ""
        self._runner: str = ""
        # Launch-probe result: None = not yet probed, True/False = interpreter
        # actually launched (or didn't) at connect(). File-existence is NOT the
        # same as launchable — a corrupt venv / wrong-arch exe passes
        # Path.exists() but raises on create_subprocess_exec.
        self._launch_ok: bool | None = None
        self._launch_err: str = ""

    async def connect(self) -> None:
        self._python_exe = self.config("python_exe", "") or _default_python_exe()
        self._runner = str(Path(__file__).parent / "runner.py")

        if not Path(self._python_exe).exists():
            print(
                f"[manim] python_exe not found: {self._python_exe}\n"
                f"        Run: uv venv --python 3.12 \"{Path(self._python_exe).parent.parent}\" "
                f"&& uv pip install --python \"{self._python_exe}\" manim\n"
                f"        Or set [plugins.manim] python_exe in emptyos.toml"
            )
        elif not Path(self._runner).exists():
            print(f"[manim] runner.py missing at {self._runner} (corrupted install?)")
        else:
            await self._probe_launch()
            if self._launch_ok:
                print(f"[manim] ready — python_exe={self._python_exe}")
            else:
                print(
                    f"[manim] python_exe exists but failed to launch: {self._python_exe}\n"
                    f"        {self._launch_err}\n"
                    f"        Reinstall the 3.12 venv (see .claude/rules/environment.md)."
                )

    async def _probe_launch(self) -> None:
        """One-shot `python_exe -c "import manim"` — confirms both the
        interpreter starts AND manim itself imports. Never raises; result
        cached in self._launch_ok / _launch_err."""
        self._launch_ok, self._launch_err = await probe_launch(
            self._python_exe, ["-c", "import manim"], timeout=20.0
        )

    async def available(self) -> bool:
        # File-existence AND not a *known* launch failure. `_launch_ok is None`
        # (unprobed) is treated as available to preserve prior behaviour.
        return (
            bool(self._python_exe)
            and Path(self._python_exe).exists()
            and Path(self._runner).exists()
            and self._launch_ok is not False
        )

    async def health_check(self) -> bool:
        """Round-trip a trivial scene render to confirm the 3.12 venv has
        manim + ffmpeg working end-to-end (not just importable)."""
        if not await self.available():
            return False
        probe_source = (
            "from manim import Scene, Circle\n\n"
            "class HealthProbe(Scene):\n"
            "    def construct(self):\n"
            "        self.add(Circle())\n"
        )
        try:
            res = await self.render_scene(
                probe_source, scene_class="HealthProbe", quality="l", timeout=60.0
            )
            return bool(res.get("ok"))
        except Exception:
            return False

    async def render_scene(
        self,
        source: str,
        *,
        scene_class: str = "Scene1",
        quality: str = "m",
        out_dir: str | Path | None = None,
        background_color: str | None = None,
        timeout: float = 180.0,
    ) -> dict:
        """Render a self-contained Manim scene script to mp4.

        `source` must define a class named `scene_class` subclassing
        `manim.Scene` with a `construct(self)` method — the caller's (LLM's)
        responsibility, same trust model as `cadquery.run_script`: this runs
        ANY Python the caller generated, in an isolated venv subprocess, not
        a sandboxed interpreter. `quality`: "l"/"m"/"h"/"k" (low/medium/high/
        4k — see `manim.constants.QUALITIES`). Returns
        `{ok, stage, path?, error?}` — `path` is the rendered mp4's absolute
        path under `out_dir` (or a temp dir if `out_dir` is None, which the
        caller must copy out of before the `with` block in the caller
        closes — prefer passing `out_dir` explicitly).
        """
        if not await self.available():
            return {
                "ok": False,
                "stage": "plugin",
                "error": f"manim plugin unavailable - python_exe missing at {self._python_exe}",
            }

        with tempfile.TemporaryDirectory(prefix="manim-") as tmp:
            tmp_path = Path(tmp)
            source_path = tmp_path / "scene.py"
            source_path.write_text(source, encoding="utf-8")

            final_out = Path(out_dir).resolve() if out_dir else (tmp_path / "out")
            final_out.mkdir(parents=True, exist_ok=True)

            spec = {
                "source_path": str(source_path),
                "scene_class": scene_class,
                "out_dir": str(final_out),
                "quality": quality,
                "background_color": background_color,
            }
            payload, res = await run_json_script(
                self._python_exe, self._runner, tmp_path / "spec.json", spec,
                timeout=timeout, domain_label="Manim interpreter", action_label="render",
            )
            if res.launch_failed:
                self._launch_ok = False
                self._launch_err = res.launch_exc
            return payload
