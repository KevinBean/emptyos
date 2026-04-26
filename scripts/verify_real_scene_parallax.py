"""End-to-end verify that layered parallax actually animates a real MV scene
with depth-aware motion. Runs the FULL pipeline:

  1. Read a real scene-NN.png from a vault MV folder.
  2. Upload to ComfyUI input/.
  3. Submit the depth_parallax.json workflow, poll until done.
  4. Download the depth map.
  5. Render a layered_parallax_clip from the real image + depth.
  6. Extract first / last frame, compute per-pixel frame-difference,
     and average inside masks derived from the depth map's BG / MG / FG bands.
  7. Pass = FG band shows meaningfully more motion than BG band.

Usage:
    python scripts/verify_real_scene_parallax.py [scene_png_path]
    (defaults to the most recent mv-*/scene-01.png in the configured songs dir)
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import aiohttp
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

COMFYUI = "http://localhost:8188"
WORKFLOW = ROOT / "plugins" / "comfyui" / "workflows" / "depth_parallax.json"


def load_assembler():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ms_assembler", ROOT / "apps" / "music-studio" / "assembler.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def find_default_scene() -> Path | None:
    """Find the newest mv-* folder under the songs dir and return its scene-01.png."""
    import tomllib
    with open(ROOT / "emptyos.toml", "rb") as f:
        cfg = tomllib.load(f)
    notes = Path(cfg.get("notes", {}).get("path", ""))
    if not notes.exists():
        return None
    songs_dir = notes / "10_Projects/YouTube-Music-Channel/songs"
    if not songs_dir.exists():
        songs_dir = notes / "Music/songs"
    if not songs_dir.exists():
        return None
    mv_dirs = []
    for song_dir in songs_dir.iterdir():
        if not song_dir.is_dir():
            continue
        for mv in song_dir.iterdir():
            if mv.is_dir() and mv.name.startswith("mv-"):
                scene = mv / "scene-01.png"
                if scene.exists():
                    mv_dirs.append((mv.stat().st_mtime, scene))
    if not mv_dirs:
        return None
    mv_dirs.sort(reverse=True)
    return mv_dirs[0][1]


async def upload_to_comfyui(session: aiohttp.ClientSession, src: Path, name: str) -> str:
    data = aiohttp.FormData()
    data.add_field("image", src.open("rb"), filename=name,
                   content_type="application/octet-stream")
    data.add_field("overwrite", "true")
    async with session.post(f"{COMFYUI}/upload/image", data=data) as resp:
        if resp.status != 200:
            return ""
        body = await resp.json()
        return body.get("name") or name


async def run_depth(session: aiohttp.ClientSession, server_image: str) -> str:
    """Submit the depth workflow, poll for completion, return the saved
    depth filename (in ComfyUI's output/)."""
    wf = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    wf = {k: v for k, v in wf.items() if not k.startswith("_")}
    # Substitute {image}.
    def _sub(node):
        if isinstance(node, dict):
            return {k: _sub(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_sub(x) for x in node]
        if isinstance(node, str):
            return node.replace("{image}", server_image)
        return node
    wf = _sub(wf)

    async with session.post(f"{COMFYUI}/prompt", json={"prompt": wf}) as resp:
        body = await resp.json()
        prompt_id = body.get("prompt_id", "")
        if body.get("node_errors"):
            print(f"[FAIL] ComfyUI rejected workflow: {body['node_errors']}")
            return ""
    if not prompt_id:
        return ""

    print(f"[depth] queued prompt {prompt_id}, polling...")
    for i in range(120):  # up to ~3 min
        await asyncio.sleep(1.5)
        async with session.get(f"{COMFYUI}/history/{prompt_id}") as resp:
            history = await resp.json()
            if prompt_id in history:
                outputs = history[prompt_id].get("outputs", {})
                for nid, out in outputs.items():
                    imgs = out.get("images", [])
                    if imgs:
                        return imgs[0].get("filename", "")
                return ""
    return ""


async def download(session: aiohttp.ClientSession, filename: str, dest: Path) -> bool:
    async with session.get(f"{COMFYUI}/view", params={"filename": filename}) as resp:
        if resp.status != 200:
            return False
        dest.write_bytes(await resp.read())
    return True


def extract_frame(video: Path, idx: int, out: Path):
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
         "-vf", f"select=eq(n\\,{idx})", "-vframes", "1", str(out)],
        check=True,
    )


def count_frames(video: Path) -> int:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames", "-of",
         "default=nokey=1:noprint_wrappers=1", str(video)],
        capture_output=True, text=True,
    )
    return int((r.stdout or "0").strip() or "0")


async def main(scene_path: Path) -> int:
    if not scene_path.exists():
        print(f"[FAIL] scene image not found: {scene_path}")
        return 1
    print(f"[setup] using scene: {scene_path}")

    work = Path(tempfile.mkdtemp(prefix="real-parallax-"))
    print(f"[setup] scratch: {work}")

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600)) as sess:
        # Health check
        try:
            async with sess.get(f"{COMFYUI}/system_stats") as r:
                if r.status != 200:
                    print(f"[FAIL] ComfyUI not reachable at {COMFYUI}")
                    return 1
        except Exception as e:
            print(f"[FAIL] ComfyUI unreachable: {e}")
            return 1

        # 1. upload
        upload_name = f"verify-{int(time.time())}.png"
        server_name = await upload_to_comfyui(sess, scene_path, upload_name)
        if not server_name:
            print("[FAIL] upload failed")
            return 1
        print(f"[upload] {server_name}")

        # 2. depth
        t0 = time.time()
        depth_filename = await run_depth(sess, server_name)
        if not depth_filename:
            print("[FAIL] depth workflow returned empty")
            return 1
        print(f"[depth] done in {time.time() - t0:.1f}s -> {depth_filename}")

        # 3. download depth
        depth_path = work / "depth.png"
        if not await download(sess, depth_filename, depth_path):
            print("[FAIL] depth download failed")
            return 1
        print(f"[depth] saved {depth_path} ({depth_path.stat().st_size} bytes)")

    # 4. render layered parallax
    asm = load_assembler()
    out_mp4 = work / "layered.mp4"
    print("[render] layered_parallax_clip(2.0s)...")
    ok = await asm.layered_parallax_clip(scene_path, depth_path, 2.0, out_mp4)
    if not ok or not out_mp4.exists():
        print("[FAIL] render failed")
        return 1
    n = count_frames(out_mp4)
    print(f"[render] ok -> {out_mp4} ({out_mp4.stat().st_size} bytes, {n} frames)")

    # 5. extract first + last frame
    first_p = work / "first.png"
    last_p = work / "last.png"
    extract_frame(out_mp4, 0, first_p)
    extract_frame(out_mp4, n - 1, last_p)
    first = np.asarray(Image.open(first_p).convert("RGB")).astype(np.int16)
    last = np.asarray(Image.open(last_p).convert("RGB")).astype(np.int16)
    H, W = first.shape[:2]

    # Per-pixel frame difference (mean over RGB).
    diff = np.abs(last - first).mean(axis=-1)  # (H, W) float

    # Resize depth to match output dims, build masks from same band thresholds
    # the assembler uses.
    dep = np.asarray(
        Image.open(depth_path).convert("L").resize((W, H), Image.LANCZOS)
    ).astype(np.float32) / 255.0
    bg_mask = dep < 0.28
    mg_mask = (dep >= 0.30) & (dep <= 0.50)
    fg_mask = dep > 0.78

    def stats(mask):
        if mask.sum() == 0:
            return float("nan"), 0
        return float(diff[mask].mean()), int(mask.sum())

    bg_m, bg_n = stats(bg_mask)
    mg_m, mg_n = stats(mg_mask)
    fg_m, fg_n = stats(fg_mask)

    print(f"\n[measure] mean |frame_last - frame_0| inside depth masks:")
    print(f"  BG (depth < 0.28):   mean={bg_m:6.2f}  pixels={bg_n}")
    print(f"  MG (0.30–0.50):      mean={mg_m:6.2f}  pixels={mg_n}")
    print(f"  FG (depth > 0.78):   mean={fg_m:6.2f}  pixels={fg_n}")

    # Pass criteria — FG should move noticeably more than BG.
    fails = []
    if any(np.isnan(x) for x in (bg_m, mg_m, fg_m)):
        # Some bands may be empty for unusual scenes; only require non-empty FG vs BG.
        if not np.isnan(fg_m) and not np.isnan(bg_m):
            if fg_m / max(0.1, bg_m) < 1.5:
                fails.append(f"FG/BG motion ratio {fg_m / max(0.1, bg_m):.2f} too small (need >= 1.5)")
        else:
            fails.append("FG or BG mask is empty — scene depth distribution doesn't have both")
    else:
        if fg_m < bg_m:
            fails.append(f"FG motion {fg_m:.2f} < BG motion {bg_m:.2f} — depth-aware effect inverted or absent")
        if fg_m / max(0.1, bg_m) < 1.5:
            fails.append(f"FG/BG ratio {fg_m / max(0.1, bg_m):.2f} too small (need >= 1.5)")

    if fails:
        print("\n[FAIL]")
        for f in fails:
            print(" -", f)
        print(f"\nDepth + clip kept for inspection at: {work}")
        return 1

    print(f"\n[PASS] real-scene parallax verified:")
    print(f"  FG/BG motion ratio = {fg_m / max(0.1, bg_m):.2f}x")
    print(f"  Output kept at: {out_mp4}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        scene = Path(sys.argv[1])
    else:
        scene = find_default_scene()
        if not scene:
            print("No scene-01.png found in any vault mv-* folder. Pass an explicit path.")
            sys.exit(2)
    sys.exit(asyncio.run(main(scene)))
