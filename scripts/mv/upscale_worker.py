"""Upscale a folder of PNG frames with a spandrel (ESRGAN-family) model, then
resize to the target size with Lanczos.

Runs under ComfyUI's embedded python, which has torch + spandrel + CUDA; it is
never imported by the daemon or the tests. Called by ``upscale_crops.py``:

    <comfy python> upscale_worker.py <in_dir> <out_dir> <model.pt> <width> <height>
"""
import sys
from pathlib import Path

import numpy as np
import spandrel
import torch
from PIL import Image


def main() -> int:
    src, dst, model_path = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    w, h = int(sys.argv[4]), int(sys.argv[5])
    dst.mkdir(parents=True, exist_ok=True)
    m = spandrel.ModelLoader().load_from_file(model_path).cuda().eval().half()
    with torch.no_grad():
        for f in sorted(src.glob("*.png")):
            a = (torch.from_numpy(np.asarray(Image.open(f).convert("RGB"))).permute(2, 0, 1)[None]
                 .float().div(255).cuda().half())
            o = m(a).clamp(0, 1)[0].permute(1, 2, 0).float().cpu().numpy()
            Image.fromarray((o * 255 + 0.5).astype(np.uint8)).resize((w, h), Image.LANCZOS).save(dst / f.name)
    print("done", len(list(dst.glob("*.png"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
