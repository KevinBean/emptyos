"""Face detection (SCRFD) and identity embedding (ArcFace) over ONNX, no insightface package.

Both come from insightface's ``buffalo_l`` model files, run directly through
onnxruntime on the CPU. The package itself is not used:
- ComfyUI's embedded python has insightface, but its onnxruntime is broken
  (``ImportError: OrtEpAssignedNode``, 2026-09-19);
- there is no insightface wheel for the daemon's Python 3.13.

So this carries the two small pieces the package would provide: the SCRFD
decoder and the 5-point alignment. It runs on CPU so it never competes with
ComfyUI for the GPU.

Model files resolve from ``mv_config`` ``models_dir`` →
``insightface/models/buffalo_l/``. The insightface models are licensed for
non-commercial research use; they are never bundled with EmptyOS.
Deps: numpy, cv2, onnxruntime — imported on first use.
"""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mv_config  # noqa: E402

# ArcFace canonical 5-point template for a 112x112 crop.
ARC_DST = [[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
           [41.5493, 92.3655], [70.7299, 92.2041]]
DET_SIZE, DET_THRESH, NMS_THRESH = 640, 0.5, 0.4
STRIDES, ANCHORS = (8, 16, 32), 2


@lru_cache(maxsize=None)
def _session(name: str, models_dir: str | None = None):
    import onnxruntime as ort

    base = mv_config.require("models_dir", models_dir) / "insightface" / "models" / "buffalo_l"
    return ort.InferenceSession(str(base / name), providers=["CPUExecutionProvider"])


def nms(boxes, scores, thresh: float = NMS_THRESH) -> list[int]:
    import numpy as np

    order, keep = scores.argsort()[::-1], []
    while order.size:
        i = order[0]
        keep.append(i)
        xx1 = np.maximum(boxes[i, 0], boxes[order[1:], 0])
        yy1 = np.maximum(boxes[i, 1], boxes[order[1:], 1])
        xx2 = np.minimum(boxes[i, 2], boxes[order[1:], 2])
        yy2 = np.minimum(boxes[i, 3], boxes[order[1:], 3])
        inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)

        def area(b):
            return (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])

        iou = inter / (area(boxes[[i]])[0] + area(boxes[order[1:]]) - inter)
        order = order[1:][iou <= thresh]
    return keep


def detect(img, *, models_dir: str | None = None) -> list[tuple]:
    """SCRFD → ``[(box[x1,y1,x2,y2], score, kps[5,2]), ...]`` in image coords."""
    import cv2
    import numpy as np

    det = _session("det_10g.onnx", models_dir)
    h, w = img.shape[:2]
    scale = DET_SIZE / max(h, w)
    rs = cv2.resize(img, (int(w * scale), int(h * scale)))
    canvas = np.zeros((DET_SIZE, DET_SIZE, 3), np.uint8)
    canvas[:rs.shape[0], :rs.shape[1]] = rs
    blob = cv2.dnn.blobFromImage(canvas, 1 / 128.0, (DET_SIZE, DET_SIZE), (127.5,) * 3, swapRB=True)
    out = det.run(None, {det.get_inputs()[0].name: blob})
    boxes, scores, kpss = [], [], []
    for k, s in enumerate(STRIDES):
        sc, bb, kp = out[k][:, 0], out[k + 3] * s, out[k + 6] * s
        n = DET_SIZE // s
        cy, cx = np.mgrid[:n, :n]
        centers = np.repeat(np.stack([cx, cy], -1).reshape(-1, 2) * s, ANCHORS, axis=0).astype(np.float32)
        m = sc >= DET_THRESH
        c, b = centers[m], bb[m]
        boxes.append(np.stack([c[:, 0] - b[:, 0], c[:, 1] - b[:, 1], c[:, 0] + b[:, 2], c[:, 1] + b[:, 3]], -1))
        scores.append(sc[m])
        kpss.append(c[:, None, :] + kp[m].reshape(-1, 5, 2))
    boxes, scores, kpss = np.concatenate(boxes) / scale, np.concatenate(scores), np.concatenate(kpss) / scale
    return [(boxes[i], float(scores[i]), kpss[i]) for i in nms(boxes, scores)]


def largest(faces):
    return max(faces, key=lambda f: (f[0][2] - f[0][0]) * (f[0][3] - f[0][1])) if faces else None


def embed(img, kps, *, models_dir: str | None = None):
    """Unit-length ArcFace embedding of the face at ``kps``, plus its aligned crop."""
    import cv2
    import numpy as np

    rec = _session("w600k_r50.onnx", models_dir)
    m, _ = cv2.estimateAffinePartial2D(kps.astype(np.float32), np.array(ARC_DST, np.float32),
                                       method=cv2.LMEDS)
    crop = cv2.warpAffine(img, m, (112, 112), borderValue=0)
    blob = cv2.dnn.blobFromImage(crop, 1 / 127.5, (112, 112), (127.5,) * 3, swapRB=True)
    v = rec.run(None, {rec.get_inputs()[0].name: blob})[0][0]
    return v / np.linalg.norm(v), crop


def read_image(path: Path):
    """cv2.imread fails silently on non-ASCII paths on Windows; decode from bytes instead."""
    import cv2
    import numpy as np

    img = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"cannot read image {path}")
    return img
