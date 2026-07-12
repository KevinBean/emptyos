"""AI-generated Plekto icon candidates via ComfyUI FLUX Schnell.

Generates 4 distinct prompt variants × N seeds each, writes to
brand/plekto/_ai/{variant}_{seed}.png. Workflow + ComfyUI client pattern
borrowed from the Home Portal icon generator (separate project).

Usage:
    python brand/plekto/_ai_generate.py           # default: 4 variants, 2 seeds each
    python brand/plekto/_ai_generate.py --seeds 1 # one seed per variant (fastest)
    python brand/plekto/_ai_generate.py --variants knot,rings  # subset
"""
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

import requests

COMFYUI_URL = "http://localhost:8188"
OUT_DIR = Path(__file__).resolve().parent / "_ai"
OUT_DIR.mkdir(exist_ok=True)

# Plekto = πλέκω, "to weave". Brand vibe: craftsman / developer-tool / quietly clever.
# All prompts use the "no text no letters no words" suffix because FLUX
# hallucinates garbage glyphs otherwise.
COMMON_NEG = "no text, no letters, no words, no typography, centered composition"
STYLE = "Flat minimalist app icon, clean vector style, single subject, square format, simple shapes, matte finish"

VARIANTS = {
    "knot": (
        f"{STYLE}, geometric Celtic-style trefoil knot made of three interlocking "
        f"strands, dark teal on off-white, hand-drawn craftsman feel, {COMMON_NEG}"
    ),
    "rings": (
        f"{STYLE}, three interlocking rings in Borromean configuration, "
        f"geometric line art, monochrome dark teal, {COMMON_NEG}"
    ),
    "weave": (
        f"{STYLE}, minimal twill weave pattern with three horizontal and three "
        f"vertical strands interlaced, monochrome slate and teal accent, "
        f"top-down view, {COMMON_NEG}"
    ),
    "braid": (
        f"{STYLE}, abstract braid emblem with three woven threads forming a "
        f"closed loop, minimal geometric, dark teal and white, modern developer "
        f"tool aesthetic, {COMMON_NEG}"
    ),
}


def flux_workflow(prompt: str, seed: int, width: int = 1024, height: int = 1024) -> dict:
    return {
        "4": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": "flux1-schnell-fp8.safetensors"}},
        "6": {"class_type": "CLIPTextEncode",
              "inputs": {"text": prompt, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode",
              "inputs": {"text": "", "clip": ["4", 1]}},
        "5": {"class_type": "EmptyLatentImage",
              "inputs": {"width": width, "height": height, "batch_size": 1}},
        "3": {"class_type": "KSampler",
              "inputs": {"model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0],
                         "latent_image": ["5", 0], "seed": seed, "steps": 4,
                         "cfg": 1.0, "sampler_name": "euler",
                         "scheduler": "simple", "denoise": 1.0}},
        "8": {"class_type": "VAEDecode",
              "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage",
              "inputs": {"images": ["8", 0], "filename_prefix": "plekto_brand"}},
    }


def queue_and_fetch(prompt: str, seed: int) -> bytes:
    """Submit one FLUX job, wait for completion, return PNG bytes."""
    workflow = flux_workflow(prompt, seed)
    r = requests.post(f"{COMFYUI_URL}/prompt",
                      json={"prompt": workflow}, timeout=15)
    r.raise_for_status()
    prompt_id = r.json()["prompt_id"]

    deadline = time.time() + 180
    while time.time() < deadline:
        r = requests.get(f"{COMFYUI_URL}/history/{prompt_id}", timeout=10)
        hist = r.json()
        if prompt_id in hist:
            for node_out in hist[prompt_id]["outputs"].values():
                if "images" in node_out:
                    info = node_out["images"][0]
                    img = requests.get(
                        f"{COMFYUI_URL}/view",
                        params={"filename": info["filename"],
                                "subfolder": info.get("subfolder", ""),
                                "type": info.get("type", "output")},
                        timeout=15)
                    img.raise_for_status()
                    return img.content
            raise RuntimeError(f"no image in output for {prompt_id}")
        time.sleep(1.5)
    raise TimeoutError(f"job {prompt_id} did not complete in 180s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=2,
                    help="Seeds per variant (default 2 = 8 images total)")
    ap.add_argument("--variants", default=",".join(VARIANTS),
                    help="Comma-separated variant subset")
    ap.add_argument("--size", type=int, default=1024)
    args = ap.parse_args()

    selected = [v.strip() for v in args.variants.split(",") if v.strip() in VARIANTS]
    print(f"Generating {len(selected)} variants × {args.seeds} seeds = "
          f"{len(selected) * args.seeds} images at {args.size}×{args.size}\n")

    manifest = []
    for variant in selected:
        prompt = VARIANTS[variant]
        for i in range(args.seeds):
            seed = random.randint(1, 2**31 - 1)
            out = OUT_DIR / f"{variant}_{seed}.png"
            print(f"  {variant} seed={seed} ... ", end="", flush=True)
            t0 = time.time()
            try:
                png_bytes = queue_and_fetch(prompt, seed)
                out.write_bytes(png_bytes)
                print(f"{out.name}  ({len(png_bytes)//1024} KB, {time.time()-t0:.1f}s)")
                manifest.append({"variant": variant, "seed": seed,
                                 "file": out.name, "prompt": prompt})
            except Exception as e:
                print(f"FAIL: {e}")

    (OUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nWrote {len(manifest)} images + manifest.json to {OUT_DIR}")


if __name__ == "__main__":
    main()
