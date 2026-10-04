"""Manim scene runner — executes inside the manim-3.12 user-home venv.

Invoked as: python runner.py <spec.json>

spec.json: {source_path, scene_class, out_dir, quality, background_color?}

Contract mirrors plugins/cadquery/runner.py: structured JSON on stdout,
exit 0 = rendered ok, exit 2 = handled error (payload has {ok: False, ...}),
any other exit / non-JSON stdout = unhandled crash (caller reports raw
stdout/stderr). Never writes outside `out_dir` / a tempdir the caller owns.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import traceback
from pathlib import Path

QUALITY_MAP = {
    "l": "low_quality",
    "m": "medium_quality",
    "h": "high_quality",
    "k": "fourk_quality",
}


def _fail(stage: str, error: str, **extra) -> int:
    print(json.dumps({"ok": False, "stage": stage, "error": error, **extra}))
    return 2


def main() -> int:
    if len(sys.argv) < 2:
        return _fail("plugin", "runner.py requires a spec.json path argument")

    spec_path = Path(sys.argv[1])
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return _fail("plugin", f"could not read spec: {exc}")

    source_path = Path(spec["source_path"])
    scene_class = spec["scene_class"]
    out_dir = Path(spec["out_dir"])
    quality = QUALITY_MAP.get(spec.get("quality", "m"), "medium_quality")
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        from manim import config
    except Exception as exc:  # noqa: BLE001
        return _fail("import", f"manim import failed: {type(exc).__name__}: {exc}")

    config.media_dir = str(out_dir)
    config.verbosity = "ERROR"
    config.quality = quality
    config.disable_caching = False  # keep manim's own partial-movie-file cache on

    bg = spec.get("background_color")
    if bg:
        config.background_color = bg

    mod_name = "eos_manim_scene"
    try:
        mod_spec = importlib.util.spec_from_file_location(mod_name, source_path)
        if mod_spec is None or mod_spec.loader is None:
            return _fail("load", f"could not load spec for {source_path}")
        module = importlib.util.module_from_spec(mod_spec)
        sys.modules[mod_name] = module
        mod_spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001
        return _fail(
            "load",
            f"{type(exc).__name__}: {exc}",
            traceback=traceback.format_exc()[-4000:],
        )

    scene_cls = getattr(module, scene_class, None)
    if scene_cls is None:
        return _fail("load", f"scene class '{scene_class}' not found in source")

    try:
        scene = scene_cls()
        scene.render()
        movie_path = scene.renderer.file_writer.movie_file_path
    except Exception as exc:  # noqa: BLE001
        return _fail(
            "render",
            f"{type(exc).__name__}: {exc}",
            traceback=traceback.format_exc()[-4000:],
        )

    if not movie_path or not Path(movie_path).exists():
        return _fail("render", "scene.render() completed but produced no movie file")

    print(json.dumps({"ok": True, "stage": "render", "path": str(movie_path)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
