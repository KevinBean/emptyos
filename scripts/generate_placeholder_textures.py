"""Generate placeholder baseColor.jpg files for the robot-modeller texture library.

For each non-procedural entry in `engines.articulated.materials.TEXTURE_LIBRARY`,
write a 256×256 JPG to `engines/articulated/textures/<name>/baseColor.jpg`.
The image is a solid tint with a small noise overlay so it doesn't read as
pure flat color — gives the eye a sense of "surface" while real PBR maps
aren't yet bundled.

Idempotent: skips any file that already exists. Run from repo root:

    python scripts/generate_placeholder_textures.py

To regenerate from scratch (e.g. after manifest tint changes), pass `--force`.

When real CC0 PBR textures replace these placeholders, drop them at the same
paths (`engines/articulated/textures/<name>/baseColor.jpg`) — the manifest
references the same filename, so no code changes are needed. Adding `normal.jpg`
and `roughness.jpg` later is similarly path-based; update the `TEXTURE_LIBRARY`
manifest to reference them when present.

This script must be runnable from the *daemon* Python (3.13) — it doesn't
need cadquery. Uses Pillow which the daemon already has.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

# Daemon-side import — works without the cadquery venv.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engines.articulated.materials import TEXTURE_LIBRARY, textures_root  # noqa: E402

try:
    from PIL import Image, ImageDraw
except ImportError as exc:
    raise SystemExit(
        "Pillow is required: pip install pillow"
    ) from exc


SIZE = 256
NOISE_AMPLITUDE = 12  # ±12 in each RGB channel — keeps the tint dominant


def _tint_to_rgb(tint: list[float]) -> tuple[int, int, int]:
    r = max(0, min(255, int(tint[0] * 255)))
    g = max(0, min(255, int(tint[1] * 255)))
    b = max(0, min(255, int(tint[2] * 255)))
    return (r, g, b)


def _generate(tint: list[float], seed: int) -> Image.Image:
    """Solid tint + per-pixel noise. Per-material seed keeps output stable
    across regenerations as long as the tint doesn't move."""
    rng = random.Random(seed)
    base = _tint_to_rgb(tint)
    img = Image.new("RGB", (SIZE, SIZE), base)
    px = img.load()
    for y in range(SIZE):
        for x in range(SIZE):
            r, g, b = base
            r = max(0, min(255, r + rng.randint(-NOISE_AMPLITUDE, NOISE_AMPLITUDE)))
            g = max(0, min(255, g + rng.randint(-NOISE_AMPLITUDE, NOISE_AMPLITUDE)))
            b = max(0, min(255, b + rng.randint(-NOISE_AMPLITUDE, NOISE_AMPLITUDE)))
            px[x, y] = (r, g, b)
    return img


def main() -> None:
    force = "--force" in sys.argv
    root = textures_root()
    root.mkdir(parents=True, exist_ok=True)

    n_written = 0
    n_skipped = 0
    n_skipped_procedural = 0

    for name, entry in TEXTURE_LIBRARY.items():
        if entry.get("procedural", False):
            n_skipped_procedural += 1
            continue
        rel_path = entry.get("base_color_map")
        if not rel_path:
            print(f"  [skip] {name}: no base_color_map in manifest")
            continue
        out_path = root / rel_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.exists() and not force:
            n_skipped += 1
            print(f"  [keep] {name}: {out_path.relative_to(root.parent.parent)} ({out_path.stat().st_size}B)")
            continue
        tint = entry.get("tint", [0.7, 0.7, 0.7, 1.0])
        # Deterministic seed per name so regenerations don't churn pixels.
        img = _generate(tint, seed=hash(name) & 0xFFFFFFFF)
        img.save(out_path, "JPEG", quality=88)
        n_written += 1
        print(f"  [write] {name}: {out_path.relative_to(root.parent.parent)} ({out_path.stat().st_size}B)")

    print()
    print(f"Done. {n_written} written, {n_skipped} kept, {n_skipped_procedural} procedural-only (skipped).")


if __name__ == "__main__":
    main()
