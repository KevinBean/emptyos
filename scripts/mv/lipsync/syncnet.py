"""SyncNet v2 lip-sync score (LSE-D / LSE-C), as used in Wav2Lip's evaluation.

    python scripts/mv/lipsync/syncnet.py <video.mp4> <audio.wav> "<label>" [--region left|right]
        [--vshift 15] [--json out.json]

  LSE-D  mean embedding distance at the best offset; lower is better.
  LSE-C  median(distance over offsets) − min(distance); higher is more confident sync.
  offset positive = audio leads video (syncnet_python convention: vshift − argmin).

A score alone is not a verdict — wrong audio can reach 4.5 on a short clip.
Judge with ``lipsync_check.py``, which scores a decoy line too.

Provenance (moved from the One More Hour lip-sync harness, 2026-09):
  Model:   SyncNetModel.S from joonson/syncnet_python, verbatim.
  Scoring: calc_pdist and the offset/min/median of syncnet_python's
           SyncNetInstance.evaluate (5-frame BGR 224x224 stacks, 20 MFCC
           frames per window, zero-padded ±vshift) — the same logic Wav2Lip's
           LSE evaluation copies. Nothing here is taken from Wav2Lip itself,
           whose code is non-commercial.

  syncnet_python is MIT licensed: "Copyright (c) 2016-present Joon Son Chung."
  Permission is hereby granted, free of charge, to any person obtaining a copy
  of that software, to deal in it without restriction, subject to including the
  copyright notice and permission notice in copies or substantial portions. The
  full licence text is at github.com/joonson/syncnet_python/blob/master/LICENSE.md.
  Crop:    syncnet_python run_pipeline crop_video (crop_scale 0.4, box size
           smoothed by a 13-frame median, frame padded with 110). SyncNet v2
           takes the full face crop in colour.
  Faces:   SCRFD (face_onnx) instead of S3FD — similar boxes, so absolute
           numbers can differ slightly from published LSE values.
  MFCC:    python_speech_features.mfcc defaults reimplemented in numpy
           (not copied; that package is not installed here).

Weights: ``models_dir/syncnet/syncnet_v2.model`` (from the VGG syncnet page,
sha256 961e8696…605442). Run with the main Python (torch + cv2 + onnxruntime +
soundfile), not ComfyUI's, whose onnxruntime is broken. CPU only.

Assumes the audio starts at video t=0; video other than 25 fps is resampled
by nearest frame.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import face_onnx  # noqa: E402
import mv_config  # noqa: E402

FPS, SR = 25, 16000


def build_model():
    """SyncNetModel.S (joonson/syncnet_python), unchanged."""
    import torch.nn as nn

    class S(nn.Module):
        def __init__(self, num_layers_in_fc_layers=1024):
            super().__init__()
            self.netcnnaud = nn.Sequential(
                nn.Conv2d(1, 64, kernel_size=(3, 3), stride=(1, 1), padding=(1, 1)), nn.BatchNorm2d(64), nn.ReLU(inplace=True),
                nn.MaxPool2d(kernel_size=(1, 1), stride=(1, 1)),
                nn.Conv2d(64, 192, kernel_size=(3, 3), stride=(1, 1), padding=(1, 1)), nn.BatchNorm2d(192), nn.ReLU(inplace=True),
                nn.MaxPool2d(kernel_size=(3, 3), stride=(1, 2)),
                nn.Conv2d(192, 384, kernel_size=(3, 3), padding=(1, 1)), nn.BatchNorm2d(384), nn.ReLU(inplace=True),
                nn.Conv2d(384, 256, kernel_size=(3, 3), padding=(1, 1)), nn.BatchNorm2d(256), nn.ReLU(inplace=True),
                nn.Conv2d(256, 256, kernel_size=(3, 3), padding=(1, 1)), nn.BatchNorm2d(256), nn.ReLU(inplace=True),
                nn.MaxPool2d(kernel_size=(3, 3), stride=(2, 2)),
                nn.Conv2d(256, 512, kernel_size=(5, 4), padding=(0, 0)), nn.BatchNorm2d(512), nn.ReLU(),
            )
            self.netfcaud = nn.Sequential(nn.Linear(512, 512), nn.BatchNorm1d(512), nn.ReLU(),
                                          nn.Linear(512, num_layers_in_fc_layers))
            self.netfclip = nn.Sequential(nn.Linear(512, 512), nn.BatchNorm1d(512), nn.ReLU(),
                                          nn.Linear(512, num_layers_in_fc_layers))
            self.netcnnlip = nn.Sequential(
                nn.Conv3d(3, 96, kernel_size=(5, 7, 7), stride=(1, 2, 2), padding=0), nn.BatchNorm3d(96), nn.ReLU(inplace=True),
                nn.MaxPool3d(kernel_size=(1, 3, 3), stride=(1, 2, 2)),
                nn.Conv3d(96, 256, kernel_size=(1, 5, 5), stride=(1, 2, 2), padding=(0, 1, 1)), nn.BatchNorm3d(256), nn.ReLU(inplace=True),
                nn.MaxPool3d(kernel_size=(1, 3, 3), stride=(1, 2, 2), padding=(0, 1, 1)),
                nn.Conv3d(256, 256, kernel_size=(1, 3, 3), padding=(0, 1, 1)), nn.BatchNorm3d(256), nn.ReLU(inplace=True),
                nn.Conv3d(256, 256, kernel_size=(1, 3, 3), padding=(0, 1, 1)), nn.BatchNorm3d(256), nn.ReLU(inplace=True),
                nn.Conv3d(256, 256, kernel_size=(1, 3, 3), padding=(0, 1, 1)), nn.BatchNorm3d(256), nn.ReLU(inplace=True),
                nn.MaxPool3d(kernel_size=(1, 3, 3), stride=(1, 2, 2)),
                nn.Conv3d(256, 512, kernel_size=(1, 6, 6), padding=0), nn.BatchNorm3d(512), nn.ReLU(inplace=True),
            )

        def forward_aud(self, x):
            return self.netfcaud(self.netcnnaud(x).flatten(1))

        def forward_lip(self, x):
            return self.netfclip(self.netcnnlip(x).flatten(1))

    return S()


def load_model(weights: Path | None = None):
    import torch

    weights = weights or mv_config.require("models_dir") / "syncnet" / "syncnet_v2.model"
    net = build_model()
    state = torch.load(str(weights), map_location="cpu", weights_only=True)
    # The 2016 checkpoint predates BatchNorm's num_batches_tracked buffer; that
    # buffer is unused in eval(), so it is the only difference tolerated.
    res = net.load_state_dict(state, strict=False)
    bad = [k for k in res.missing_keys if not k.endswith("num_batches_tracked")] + list(res.unexpected_keys)
    if bad:
        raise ValueError(f"weights do not match SyncNetModel.S: {bad[:5]}")
    return net.eval()


# ── MFCC (python_speech_features.mfcc defaults) ─────────────────────────────

def _hz2mel(hz):
    import numpy as np
    return 2595 * np.log10(1 + hz / 700.0)


def _mel2hz(mel):
    return 700 * (10 ** (mel / 2595.0) - 1)


def mfcc(sig, sr=SR, winlen=0.025, winstep=0.01, numcep=13, nfilt=26, nfft=512, preemph=0.97, ceplifter=22):
    import numpy as np

    sig = np.asarray(sig, np.float64)
    sig = np.append(sig[0], sig[1:] - preemph * sig[:-1])
    flen, fstep = int(round(winlen * sr)), int(round(winstep * sr))
    n = 1 if len(sig) <= flen else 1 + int(math.ceil((len(sig) - flen) / fstep))
    padded = np.concatenate([sig, np.zeros(int((n - 1) * fstep + flen) - len(sig))])
    idx = np.arange(flen)[None, :] + (np.arange(n) * fstep)[:, None]
    frames = padded[idx]                                     # rectangular window
    pspec = (np.abs(np.fft.rfft(frames, nfft)) ** 2) / nfft
    energy = pspec.sum(1)
    energy[energy == 0] = np.finfo(float).eps
    mel = np.linspace(_hz2mel(0), _hz2mel(sr / 2), nfilt + 2)
    bins = np.floor((nfft + 1) * _mel2hz(mel) / sr).astype(int)
    fb = np.zeros((nfilt, nfft // 2 + 1))
    for j in range(nfilt):
        for i in range(bins[j], bins[j + 1]):
            fb[j, i] = (i - bins[j]) / (bins[j + 1] - bins[j])
        for i in range(bins[j + 1], bins[j + 2]):
            fb[j, i] = (bins[j + 2] - i) / (bins[j + 2] - bins[j + 1])
    feat = pspec @ fb.T
    feat[feat == 0] = np.finfo(float).eps
    feat = np.log(feat)
    # orthonormal DCT-II (scipy.fftpack.dct type 2, norm='ortho'), first numcep
    N = nfilt
    k, m = np.arange(numcep)[:, None], np.arange(N)[None, :]
    D = np.cos(np.pi * k * (2 * m + 1) / (2 * N)) * np.sqrt(2.0 / N)
    D[0] /= np.sqrt(2)
    feat = feat @ D.T
    feat *= 1 + (ceplifter / 2.0) * np.sin(np.pi * np.arange(numcep) / ceplifter)
    feat[:, 0] = np.log(energy)
    return feat                                              # [frames, 13]


def load_audio(path):
    import numpy as np
    import soundfile as sf

    x, sr = sf.read(str(path), dtype="float64", always_2d=True)
    x = x.mean(1)
    if sr != SR:   # FFT resample (scipy.signal.resample equivalent)
        n_out = int(round(len(x) * SR / sr))
        X = np.fft.rfft(x)
        Y = np.zeros(n_out // 2 + 1, complex)
        m = min(len(X), len(Y))
        Y[:m] = X[:m]
        x = np.fft.irfft(Y, n_out) * (n_out / len(x))
    return np.clip(x * 32768.0, -32768, 32767)                 # int16 scale, as wavfile.read gives


# ── Video + face crop ───────────────────────────────────────────────────────

def read_frames(path):
    import cv2

    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or FPS
    frames = []
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        frames.append(fr)
    cap.release()
    if not frames:
        raise ValueError(f"cannot read {path}")
    if abs(fps - FPS) > 0.01:   # nearest-frame resample to 25 fps
        n = int(len(frames) * FPS / fps)
        frames = [frames[min(len(frames) - 1, int(round(i * fps / FPS)))] for i in range(n)]
    return frames


def pick_face(faces, width, region):
    if region:
        faces = [f for f in faces if ((f[0][0] + f[0][2]) / 2 < width / 2) == (region == "left")]
    if not faces:
        return None
    return max(faces, key=lambda f: (f[0][2] - f[0][0]) * (f[0][3] - f[0][1]))[0]


def _medfilt(x, k=13):
    import numpy as np

    h = k // 2
    p = np.pad(x, h, mode="edge")
    return np.array([np.median(p[i:i + k]) for i in range(len(x))])


def face_crops(frames, region=None, crop_scale=0.4):
    """224x224 face crops per frame, the share of frames with a detected face, and
    the median detected face height in pixels (0 when none)."""
    import cv2
    import numpy as np

    W = frames[0].shape[1]
    boxes = [pick_face(face_onnx.detect(f), W, region) for f in frames]
    have = np.array([b is not None for b in boxes])
    if not have.any():
        return None, 0.0, 0.0
    t = np.arange(len(frames))
    arr = np.array([b if b is not None else [np.nan] * 4 for b in boxes], float)
    face_h = float(np.median(arr[have, 3] - arr[have, 1]))
    for c in range(4):   # fill gaps by interpolation (edges hold nearest)
        arr[:, c] = np.interp(t, t[have], arr[have, c])
    s = _medfilt(np.maximum(arr[:, 3] - arr[:, 1], arr[:, 2] - arr[:, 0]) / 2)
    y = _medfilt((arr[:, 1] + arr[:, 3]) / 2)
    x = _medfilt((arr[:, 0] + arr[:, 2]) / 2)
    crops = []
    for i, img in enumerate(frames):
        bs = s[i]
        bsi = int(bs * (1 + 2 * crop_scale))
        fr = np.pad(img, ((bsi, bsi), (bsi, bsi), (0, 0)), "constant", constant_values=110)
        my, mx = y[i] + bsi, x[i] + bsi
        face = fr[int(my - bs):int(my + bs * (1 + 2 * crop_scale)),
                  int(mx - bs * (1 + crop_scale)):int(mx + bs * (1 + crop_scale))]
        crops.append(cv2.resize(face, (224, 224)))
    return crops, float(have.mean()), face_h


# ── Scoring (SyncNetInstance.evaluate) ──────────────────────────────────────

def calc_pdist(feat1, feat2, vshift):
    import torch

    win = vshift * 2 + 1
    feat2p = torch.nn.functional.pad(feat2, (0, 0, vshift, vshift))
    return [torch.nn.functional.pairwise_distance(feat1[[i], :].repeat(win, 1), feat2p[i:i + win, :])
            for i in range(len(feat1))]


def score(net, crops, audio, vshift=15, batch=20) -> dict:
    import numpy as np
    import torch

    with torch.no_grad():
        im = torch.from_numpy(np.stack(crops, 3)[None].transpose(0, 3, 4, 1, 2).astype(np.float32))
        cc = torch.from_numpy(mfcc(audio).T[None, None].astype(np.float32))
        min_length = min(len(crops), math.floor(len(audio) / 640))
        last = min_length - 5
        if last < 1:
            raise ValueError("clip too short for a single 5-frame window")
        imf, ccf = [], []
        for i in range(0, last, batch):
            rng = range(i, min(last, i + batch))
            imf.append(net.forward_lip(torch.cat([im[:, :, v:v + 5] for v in rng], 0)))
            ccf.append(net.forward_aud(torch.cat([cc[:, :, :, v * 4:v * 4 + 20] for v in rng], 0)))
        dists = calc_pdist(torch.cat(imf), torch.cat(ccf), vshift)
        mdist = torch.mean(torch.stack(dists, 1), 1)
        minval, minidx = torch.min(mdist, 0)
    offset = int(vshift - minidx)
    return {"windows": last, "offset": offset, "lse_d": float(minval),
            "lse_c": float(torch.median(mdist) - minval), "edge": abs(offset) == vshift}


def measure(video, audio, *, region=None, vshift=15, net=None, frames=None, crops=None) -> dict:
    """Score one video against one audio track.

    Pass ``net``, ``frames`` and ``crops`` (a ``face_crops`` result) to reuse them.
    """
    frames = frames if frames is not None else read_frames(video)
    crops, face_pct, face_h = crops if crops is not None else face_crops(frames, region)
    base = {"frames": len(frames), "faces_pct": face_pct, "face_h_px": round(face_h)}
    if crops is None:
        return {**base, "error": "no face found"}
    return {**base, **score(net or load_model(), crops, load_audio(audio), vshift=vshift)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("video")
    ap.add_argument("audio")
    ap.add_argument("label")
    ap.add_argument("--region", choices=["left", "right"])
    ap.add_argument("--vshift", type=int, default=15)
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    try:
        r = measure(a.video, a.audio, region=a.region, vshift=a.vshift)
    except ValueError as exc:
        print(f"{a.label}: {exc}", file=sys.stderr)
        return 1
    if "error" in r:
        print(f"{a.label:34} frames={r['frames']:4} faces=0%   no face found")
        return 1
    print(f"{a.label:34} frames={r['frames']:4} faces={r['faces_pct']:4.0%} face={r['face_h_px']}px "
          f"windows={r['windows']:4} offset={r['offset']:+3d}  LSE-D={r['lse_d']:6.3f}  LSE-C={r['lse_c']:6.3f}"
          + ("  [FEW FACES: crop mostly interpolated, score unreliable]" if r["faces_pct"] < 0.5 else "")
          + ("  [offset at search edge]" if r["edge"] else ""))
    if a.json:
        Path(a.json).write_text(json.dumps({"label": a.label, "video": a.video, "audio": a.audio, **r},
                                           indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
