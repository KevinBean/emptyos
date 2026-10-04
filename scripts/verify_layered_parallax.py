"""End-to-end verification that layered parallax produces actual depth-aware motion.

Synth: a 1280x720 image with three vertical bands carrying clear visual markers,
each band painted into a distinct depth band of the depth map. After running
layered_parallax_clip, we extract first / middle / last frames and compute the
per-band pixel motion. Pass criteria: FG marker moves >> MG marker >> BG marker.

Run: ``python scripts/verify_layered_parallax.py``
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# Paint three vertical stripes into both image and depth.
W, H = 1280, 720
BANDS = [
    # (label, x_start, x_end, color_rgb, depth_value)
    # Depth values chosen at each layer's full-alpha zone so the marker
    # belongs cleanly to one layer (FG smoothstep edge1=0.78, MG full at
    # ~0.50, BG full ≤ 0.28).
    ("BG-far", 100, 280, (60, 80, 200), 0.10),
    ("MG-mid", 550, 730, (200, 100, 80), 0.50),
    ("FG-near", 1000, 1180, (40, 220, 100), 0.90),
]
MARKER_BAR_H = 20  # tall row near top to track horizontally


def synth_image_and_depth(out_dir: Path) -> tuple[Path, Path]:
    img = np.full((H, W, 3), 30, dtype=np.uint8)  # dark base
    dep = np.full((H, W), 50, dtype=np.uint8)  # mid depth base ≈ 0.20

    for label, x0, x1, rgb, dval in BANDS:
        # Full-height stripe so the layer alpha picks it up cleanly.
        img[:, x0:x1] = rgb
        dep[:, x0:x1] = int(dval * 255)
        # Top marker bar — narrow horizontal band we'll track frame-to-frame.
        img[40 : 40 + MARKER_BAR_H, x0:x1] = [255, 255, 255]

    img_path = out_dir / "synth-image.png"
    dep_path = out_dir / "synth-depth.png"
    Image.fromarray(img, mode="RGB").save(img_path)
    Image.fromarray(dep, mode="L").save(dep_path)
    return img_path, dep_path


def find_marker_centroid(frame: np.ndarray, x_search_lo: int, x_search_hi: int) -> float:
    """Return the x-centroid of the white marker bar within an x-range, in the
    top marker row (rows 40..60). Searching inside the band's expected x-range
    even after parallax shift; we widen the search window beyond the original
    stripe edges so we can detect motion in either direction.
    """
    row = frame[40 : 40 + MARKER_BAR_H, :, :]  # (MARKER_BAR_H, W, 3)
    # White marker = roughly equal high values across channels.
    is_marker = (row > 220).all(axis=-1).any(axis=0)  # W booleans
    xs = np.where(is_marker)[0]
    in_window = xs[(xs >= x_search_lo) & (xs < x_search_hi)]
    if not len(in_window):
        return float("nan")
    return float(in_window.mean())


def extract_frames(video: Path, n_samples: int = 5) -> list[np.ndarray]:
    """Pull n evenly-spaced frames from the encoded video into numpy arrays."""
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_frames",
            "-of",
            "default=nokey=1:noprint_wrappers=1",
            str(video),
        ],
        capture_output=True,
        text=True,
    )
    total = int((probe.stdout or "0").strip() or "0")
    if not total:
        # Fallback: count frames by decoding once (slower).
        probe2 = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-count_frames",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=nb_read_frames",
                "-of",
                "default=nokey=1:noprint_wrappers=1",
                str(video),
            ],
            capture_output=True,
            text=True,
        )
        total = int((probe2.stdout or "0").strip() or "0")
    if not total:
        raise RuntimeError("Could not count frames in encoded video")

    indices = np.linspace(0, total - 1, n_samples).astype(int)
    frames = []
    with tempfile.TemporaryDirectory() as td:
        for i, idx in enumerate(indices):
            out = Path(td) / f"f-{i}.png"
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    "error",
                    "-i",
                    str(video),
                    "-vf",
                    f"select=eq(n\\,{idx})",
                    "-vframes",
                    "1",
                    str(out),
                ],
                check=True,
            )
            frames.append(np.asarray(Image.open(out).convert("RGB")))
    return frames


async def main() -> int:
    # The app dir has a hyphen, can't `import apps.music-studio`. Load directly.
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "ms_assembler",
        ROOT / "apps" / "music-studio" / "assembler.py",
    )
    asm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(asm)

    work = Path(tempfile.mkdtemp(prefix="parallax-verify-"))
    print(f"[setup] scratch dir: {work}")
    img_path, dep_path = synth_image_and_depth(work)
    out_mp4 = work / "layered.mp4"

    print("[render] running layered_parallax_clip(2.0s, 30fps)...")
    ok = await asm.layered_parallax_clip(img_path, dep_path, 2.0, out_mp4)
    if not ok:
        print("[FAIL] layered_parallax_clip returned False")
        return 1
    if not out_mp4.exists():
        print("[FAIL] output mp4 not produced")
        return 1
    print(f"[render] ok -> {out_mp4} ({out_mp4.stat().st_size} bytes)")

    print("[probe] extracting first / mid / last frame...")
    frames = extract_frames(out_mp4, n_samples=5)
    if len(frames) < 3:
        print("[FAIL] fewer than 3 frames extracted")
        return 1

    # Track the white marker centroid in each band across frames.
    print("\n[measure] per-band marker x-centroid by frame:")
    print(f"  {'band':<10} {'orig':>6}  " + "  ".join(f"f{i}" for i in range(len(frames))))
    motion_per_band = {}
    for label, x0, x1, _rgb, _d in BANDS:
        # Search a window wider than original stripe to catch parallax shift.
        lo = max(0, x0 - 200)
        hi = min(W, x1 + 200)
        centroids = [find_marker_centroid(f, lo, hi) for f in frames]
        orig_c = (x0 + x1) / 2.0
        diffs = [c - orig_c if not np.isnan(c) else float("nan") for c in centroids]
        motion_per_band[label] = diffs
        print(
            f"  {label:<10} {orig_c:>6.0f}  "
            + "  ".join(f"{d:+5.1f}" if not np.isnan(d) else "  nan" for d in diffs)
        )

    # Pass criteria.
    bg = motion_per_band["BG-far"]
    mg = motion_per_band["MG-mid"]
    fg = motion_per_band["FG-near"]

    def amp(diffs):
        valid = [d for d in diffs if not np.isnan(d)]
        if len(valid) < 2:
            return 0.0
        return max(valid) - min(valid)

    bg_amp, mg_amp, fg_amp = amp(bg), amp(mg), amp(fg)
    print(
        f"\n[summary] motion amplitude (peak-to-peak px):"
        f"  BG={bg_amp:.1f}  MG={mg_amp:.1f}  FG={fg_amp:.1f}"
    )

    fails = []
    if fg_amp < 30:
        fails.append(
            f"FG amplitude only {fg_amp:.1f}px — expected >= 30 (8% of 1280 = ~100, allowing for half-cycle motion)"
        )
    if not (fg_amp > mg_amp > bg_amp):
        fails.append(
            f"Layer ordering wrong: FG({fg_amp:.1f}) > MG({mg_amp:.1f}) > BG({bg_amp:.1f}) does NOT hold"
        )
    if mg_amp / max(0.1, bg_amp) < 1.8:
        fails.append(
            f"MG/BG ratio {mg_amp / max(0.1, bg_amp):.2f} too small — bands aren't separating"
        )

    if fails:
        print("\n[FAIL]")
        for f in fails:
            print(f"  - {f}")
        return 1

    print("\n[PASS] depth-aware layered parallax verified:")
    print(f"  FG/BG ratio = {fg_amp / max(0.1, bg_amp):.2f}x (> 1.8 required)")
    print("  Layer ordering FG > MG > BG holds.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
