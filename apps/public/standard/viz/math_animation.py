"""viz — precise, frame-accurate math/explainer animation via the `manim`
plugin (ManimCommunity/manim). Deliberately SEPARATE from the generic
HTML-shape pipeline (`generation.py`/`streaming.py`/`routes.py`'s
`PRESETS`-driven flow) rather than a new `PRESETS` entry: those paths all
assume the LLM's output IS the artifact (single-file HTML, text-edited,
element-anchored, streamed). Here the LLM writes a Manim scene SCRIPT that
a separate render step compiles to an mp4 — a different pipeline shape, not
a new preset string. See `docs/OPEN-SOURCE-BORROWING-PLAN.md` §
ManimCommunity/manim (2026-08-22) for why this exists (viz's `anim-explainer`
is LLM-improvised WAAPI timing; `math-explainer` is static KaTeX — neither
can promise exact interpolation/easing or a synced multi-mobject morph).

Dark by default: `[apps.viz] feature.math-animation.enabled` (off = the verb
below always refuses; no UI/PRESETS/SHAPE_META change, so the existing shape
picker and every other endpoint are byte-identical). Flip on once the `manim`
plugin's user-home venv is set up (`.claude/rules/environment.md`) and a few
generations have been eyeballed.

Persists into the SAME `outputs/<id>/` record-dir convention as the HTML
shapes (`_record_dir`/`_rel_html`/`_rel_record` from `generation.py`) so the
artifact shows up in `list_items`/`api_get`/`api_delete` like any other viz
record, and serves via the ALREADY-EXISTING `GET /api/video/{rid}` route
(routes.py — checks only `record_dir / "scene.mp4"`, shape-agnostic) rather
than adding a second video-serving path. `scene.html` is a thin `<video>`
wrapper, not an LLM-authored artifact — so element-edit/multipass/figure-
export never apply (this shape is deliberately absent from `PRESETS`, which
is what keeps it out of every generic-HTML code path automatically).

Cross-module callers reach the entry point via ``self.generate_math_animation``
after re-binding in app.py. Reaches into `generation.py`'s bound helpers
(`self._record_dir`, `self._rel_html`, `self._rel_record`) and `shared.py`'s
`_new_id`/`_now_iso`/`_artifact_title`.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import cli_command, web_route

from .shared import _artifact_title, _new_id, _now_iso

if TYPE_CHECKING:
    from .app import VizApp  # noqa: F401 — for type hints only


# ─── Bind to VizApp class as ──────────────────────────────────────────
#   generate_math_animation = _math_animation.generate_math_animation
#   api_generate_math_animation = _math_animation.api_generate_math_animation
#   cli_math_animation      = _math_animation.cli_math_animation
#   _math_animation_enabled = _math_animation._math_animation_enabled
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────


# System prompt for the Manim scene-authoring turn. Per CLAUDE.md rule 12
# this is a module-top constant. NO MathTex/Tex — the plugin's user-home venv
# does not install a LaTeX toolchain (see plugins/manim/plugin.py's docstring);
# a scene using them fails at render time with a real manim error, which is
# an honest degrade, but the system prompt steers away from it up front so
# the common case just works.
VIZ_MATH_ANIMATION_SYSTEM = """\
You write a single Manim Community Edition (v0.19+) scene for a precise,
frame-accurate, deterministically-timed explainer animation.

Output ONLY one fenced Python code block, nothing else — no prose before or
after. The block must define exactly one class named `Scene1` subclassing
`manim.Scene` with a `construct(self)` method:

```python
from manim import *

class Scene1(Scene):
    def construct(self):
        ...
```

Rules:
- Import only from `manim` (the fence above is the only import line you need
  — `from manim import *` brings in every Mobject/Animation this environment
  ships).
- Do NOT use `MathTex` or `Tex` — no LaTeX toolchain is installed. Use `Text`
  (Pango-rendered, no LaTeX needed) for any words or symbols, and plain
  shapes (`Circle`, `Square`, `Line`, `Arrow`, `NumberPlane`, `Dot`, `Axes`,
  `Rectangle`, `Triangle`, `Polygon`, `VGroup`, ...) for everything else.
  Unicode math symbols (×, ÷, →, ², √, π, θ, Δ, ≈, ≤, ≥) work fine inside
  `Text(...)`.
- Use `self.play(...)` for every visible change — a mobject that appears via
  `self.add(...)` alone has no entrance and looks like a jump cut. Use
  `Create`, `Write`, `FadeIn`/`FadeOut`, `Transform`, `ReplacementTransform`,
  `MoveToTarget`/`.animate`, and `rate_func=` (default `smooth`) for easing.
- No file I/O, no network calls, no imports beyond `manim` — the scene runs
  in an isolated subprocess and its only job is to construct + animate
  mobjects.
- Keep the whole animation under ~20 seconds of `run_time` — this is an
  explainer clip, not a film.
- Do not call `.render()` yourself — the caller does that.
"""


_PY_FENCE_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)


def _extract_python(text: str) -> str:
    """Pull the first fenced code block out of the model's reply; falls back
    to the raw text (stripped) if no fence is present — mirrors
    `_extract_html`'s salvage posture in shared.py, simplified for the
    single-fence contract this system prompt asks for."""
    match = _PY_FENCE_RE.search(text or "")
    if match:
        return match.group(1).strip()
    return (text or "").strip()


def _math_animation_enabled(self) -> bool:
    """Dark flag (default off). Off -> generate_math_animation always
    refuses; no other code path changes (this shape is absent from PRESETS,
    so the flag isn't even the only gate — see the module docstring).

    Read through `setting_or_config`, not `app_config` — the flag is
    declared in `[provides.settings]`, and the panel writes it to the
    settings service under the schema key verbatim (see figures.py's
    `_figure_enabled` for the same pattern + rationale,
    `.claude/rules/app-ui-patterns.md`).
    """
    return bool(self.setting_or_config(
        "viz.feature.math-animation.enabled", False,
        config_key="feature.math-animation.enabled",
    ))


async def generate_math_animation(
    self, prompt: str, *, quality: str = "m", source: str = "",
) -> dict:
    """Prompt -> Manim scene script -> rendered mp4 -> a viz record.

    Returns the same top-level shape as `generation.generate()`
    (`{ok, id, shape, prompt, ...}` on success / `{ok: False, error}` on
    failure) so callers that already handle viz's generic result shape don't
    need a special case. `shape` is always `"math-animation"` on the
    returned record, even though it's not a `PRESETS` key — the frontmatter
    field is free text, only the generic-HTML code paths key off `PRESETS`.
    """
    prompt = (prompt or "").strip()
    if not prompt:
        return {"ok": False, "error": "prompt is required"}
    if not self._math_animation_enabled():
        return {
            "ok": False,
            "error": "math-animation is disabled — set "
                     "[apps.viz] feature.math-animation.enabled = true "
                     "(needs the manim plugin's user-home venv, see "
                     ".claude/rules/environment.md)",
        }

    manim = self.service("manim")
    if manim is None or not await manim.available():
        return {
            "ok": False,
            "error": "manim plugin unavailable — set up the user-home venv "
                     "(uv venv --python 3.12 %LOCALAPPDATA%/eos/envs/manim-3.12 "
                     "&& uv pip install --python <that venv> manim)",
        }

    raw = await self.think(
        f"Write the Scene1 class for this brief:\n\n{prompt}",
        system=VIZ_MATH_ANIMATION_SYSTEM,
        domain="code",
        temperature=0.4,
        max_tokens=8192,
        min_ability="standard",
    )
    source_code = _extract_python(raw if isinstance(raw, str) else str(raw))
    if "class Scene1" not in source_code:
        return {
            "ok": False,
            "error": "model did not produce a Scene1 class — try a simpler brief",
        }

    rid = _new_id()
    record_dir = self._record_dir(rid)
    record_dir.mkdir(parents=True, exist_ok=True)

    report = await manim.render_scene(
        source_code,
        scene_class="Scene1",
        quality=quality,
        out_dir=record_dir / "_manim",
    )
    if not report.get("ok"):
        return {
            "ok": False,
            "error": f"manim render failed ({report.get('stage', '?')}): "
                     f"{report.get('error', 'unknown error')}",
        }

    import shutil
    rendered = Path(report["path"])
    mp4_path = record_dir / "scene.mp4"
    shutil.copy2(rendered, mp4_path)
    shutil.rmtree(record_dir / "_manim", ignore_errors=True)

    (record_dir / "scene.py").write_text(source_code, encoding="utf-8")

    video_url = f"/viz/api/video/{rid}"
    html = (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<style>html,body{margin:0;height:100%;background:#111;"
        "display:flex;align-items:center;justify-content:center}"
        "video{max-width:100%;max-height:100%}</style></head><body>"
        f"<video src=\"{video_url}\" controls autoplay loop muted playsinline>"
        "</video></body></html>"
    )
    (record_dir / "scene.html").write_text(html, encoding="utf-8")

    now = _now_iso()
    fm = {
        "tags": ["viz"],
        "title": _artifact_title(prompt, fallback=f"Math animation {rid}"),
        "viz_id": rid,
        "shape": "math-animation",
        "prompt": prompt,
        "author": "ai",
        "created": now,
        "updated": now,
        "size_kb": round(mp4_path.stat().st_size / 1024, 1),
        "history": [{"ts": now, "prompt": prompt}],
    }
    if source:
        fm["source"] = source
    title = fm["title"]
    body = (
        f"# {title}\n\n**Brief:** {prompt}\n\n"
        f"[Open scene.html]({self._rel_html(rid)}) · "
        f"[Scene source](scene.py)\n"
    )
    self.vault_create_note(self._rel_record(rid), fm, body)

    await self.emit("viz:created", {"id": rid, "shape": "math-animation"})
    return {
        "ok": True,
        "id": rid,
        "shape": "math-animation",
        "prompt": prompt,
        "created": now,
        "updated": now,
        "record_dir": str(record_dir),
        "html_path": self._rel_html(rid),
        "record_path": self._rel_record(rid),
        "video_url": video_url,
        "size_kb": fm["size_kb"],
    }


@web_route("POST", "/api/generate-math-animation")
async def api_generate_math_animation(self, request) -> dict:
    body = await request.json()
    return await self.generate_math_animation(
        (body.get("prompt") or "").strip(),
        quality=body.get("quality", "m"),
        source=body.get("source", ""),
    )


@cli_command("math")
async def cli_math_animation(self, prompt: str, quality: str = "m") -> None:
    """CLI: eos viz math "<brief>" — generate a precise Manim animation."""
    result = await self.generate_math_animation(prompt, quality=quality)
    if result.get("ok"):
        print(f"OK  id={result['id']}  {result['record_path']}")
    else:
        print(f"ERROR  {result.get('error', 'unknown error')}")
