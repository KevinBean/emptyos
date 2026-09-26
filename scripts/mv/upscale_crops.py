"""Pre-render every cropped insert of a shots spec at full frame, matched in detail.

    python scripts/mv/upscale_crops.py shots.json

A crop insert is a 3-5x enlargement, and it must not look softer or sharper
than the full-frame shots around it. Measured during the 〈說得太急〉 rescue,
as mean horizontal luma gradient:

- plain Lanczos: 2.66;
- ESRGAN 4xRealisticRescaler: 5.38;
- full-frame shots: 2.61-4.00, median 2.91.

At a 0.5 ESRGAN blend the dark street crop turned painterly, with edges that
read as drawn, and 4xNMKDSuperscale cross-hatched. So each insert is
``blend`` x ESRGAN + (1 - blend) x Lanczos, default 0.3. That removes the
compression blocks without drawn edges, and lands inside the full-frame range.
The shared film grain in the grade then sits on every shot alike.

Output: ``<upscale_dir>/<clip>_<in>.mp4``, covering exactly the shot's source
range, which is what ``render_shots.py`` reads, plus a ``.json`` stamp of what
it was made from. Settings live in the spec (``"upscale": {"blend": 0.3,
"model": "<file>.pt"}``). An insert whose stamp still matches is kept; one made
for a different crop, range, blend or model is rebuilt, and ``render_shots``
refuses to use a stale one. Each insert is encoded to a temp file and moved into
place only when complete, so an interrupted run never leaves a short insert.
Paths come from ``mv_config``: ``comfy_python`` runs the worker, and the model
is read from ``models_dir/upscale_models``.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mv_config  # noqa: E402
import render_shots  # noqa: E402

DEFAULT_MODEL = "4xRealisticrescaler_100000G.pt"


def blend_command(lanczos_dir: Path, esrgan_dir: Path, out: Path, *, fps: int, width: int,
                  height: int, blend: float) -> list[str]:
    return ["ffmpeg", "-v", "error", "-y",
            "-framerate", str(fps), "-i", str(lanczos_dir / "%05d.png"),
            "-framerate", str(fps), "-i", str(esrgan_dir / "%05d.png"),
            "-filter_complex",
            f"[0]scale={width}:{height}:flags=lanczos,format=gbrp[l];[1]format=gbrp[e];"
            f"[e][l]blend=all_mode=normal:all_opacity={blend},format=yuv420p",
            "-c:v", "libx264", "-crf", "12", "-preset", "slow", str(out)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("spec", type=Path)
    ap.add_argument("--comfy-python")
    ap.add_argument("--models-dir")
    args = ap.parse_args(argv)
    spec = render_shots.Spec.load(args.spec)
    up = spec.get("upscale") or {}
    blend, model_name = float(up.get("blend", 0.3)), str(up.get("model", DEFAULT_MODEL))
    if not 0.0 <= blend <= 1.0:
        print("upscale_crops: upscale.blend must be between 0 and 1", file=sys.stderr)
        return 1
    todo = [s for s in spec.get("shots") or [] if s.get("crop")]
    if not todo:
        print("no crop shots in the spec")
        return 0
    todo = [s for s in todo if render_shots.read_stamp(spec.crop_path(s["clip"], s["in"]))
            != render_shots.crop_stamp(spec, s)]
    if not todo:
        print("every crop insert is current")
        return 0
    py = mv_config.require("comfy_python", args.comfy_python)
    model = Path(model_name)
    if not model.is_file():
        model = mv_config.require("models_dir", args.models_dir) / "upscale_models" / model_name
    if not model.is_file():
        print(f"upscale_crops: model not found: {model}", file=sys.stderr)
        return 2
    fps = spec.fps
    width, height = int(spec.get("width", 1920)), int(spec.get("height", 1080))
    for s in todo:
        out = spec.crop_path(s["clip"], s["in"])
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(dir=out.parent))
        try:
            (tmp / "in").mkdir()
            n0, n1 = int(round(s["in"] * fps)), int(round(s["out"] * fps))
            subprocess.run(["ffmpeg", "-v", "error", "-i", str(spec.clip_path(s["clip"])), "-vf",
                            f"trim=start_frame={n0}:end_frame={n1},setpts=PTS-STARTPTS,crop={s['crop']}",
                            str(tmp / "in" / "%05d.png")], check=True)
            w = subprocess.run([str(py), str(HERE / "upscale_worker.py"), str(tmp / "in"), str(tmp / "es"),
                                str(model), str(width), str(height)], capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            if w.returncode:
                print(f"upscale_crops: the upscale worker failed on {s['clip']}:\n{w.stderr[-1500:]}",
                      file=sys.stderr)
                return 1
            staged = tmp / out.name
            subprocess.run(blend_command(tmp / "in", tmp / "es", staged, fps=fps, width=width,
                                         height=height, blend=blend), check=True)
            os.replace(staged, out)
            out.with_name(out.name + ".json").write_text(
                json.dumps(render_shots.crop_stamp(spec, s)), encoding="utf-8")
            print("made", out.name)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
