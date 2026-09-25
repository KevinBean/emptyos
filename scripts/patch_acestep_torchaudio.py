"""Make the ComfyUI_ACE-Step pack's audio I/O work on torchaudio >= 2.9.

WHY THIS EXISTS
---------------
EmptyOS's section-repaint workflow (plugins/comfyui/workflows/acestep_repaint.json)
drives the ComfyUI_ACE-Step (MW) custom-node pack, which is the only thing on
this install that owns the ACE-Step studio verbs. The pack does its audio I/O
through ``torchaudio.save`` / ``torchaudio.load``.

torchaudio 2.9 routes both through TorchCodec, and TorchCodec cannot load
without FFmpeg's *shared* DLLs on PATH. Measured on this machine 2026-08-15
(torch 2.9.1+cu130, torchaudio 2.9.1+cu130): torchcodec is installed but
``import torchcodec`` raises, and both ``torchaudio.save`` and
``torchaudio.load`` fail with "Could not load libtorchcodec". The repaint run
dies inside ACEStepRepainting *after* loading 7.6 GB of weights.

The alternative fix -- installing FFmpeg full-shared and putting its DLLs on
PATH -- repairs torchaudio globally but changes the environment every other
ComfyUI workflow on this machine depends on. This script takes the contained
route instead: rewrite the two live call sites to use ``soundfile`` (already
present, 0.12.1), which reads and writes wav/flac without FFmpeg.

WHAT IT TOUCHES (two call sites, both on the repaint path)
  * ace_step_nodes.py::cache_audio_tensor      -- writes src_audio to a temp file
  * ace_step/music_dcae/music_dcae_pipeline.py::MusicDCAE.load_audio
                                               -- reads that file back for encoding
Two further torchaudio calls in the pack are deliberately left alone: the ones
in music_dcae_pipeline.py's ``__main__`` demo block and in text2music_dataset.py
(training only). Neither runs during inference.

This edits a VENDORED THIRD-PARTY FILE, so a pack update reverts it. That is
the reason this is a tracked script and not a manual edit -- re-run it after
updating the pack. It is idempotent (safe to re-run) and reversible.

    python scripts/patch_acestep_torchaudio.py           # apply
    python scripts/patch_acestep_torchaudio.py --check   # report only, exit 1 if unpatched
    python scripts/patch_acestep_torchaudio.py --revert  # restore from .eosbak
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

PACK = Path(
    "D:/ComfyUI_windows_portable/ComfyUI/custom_nodes/ComfyUI_ACE-Step"
)

MARKER = "# eos-patched: torchaudio>=2.9 needs torchcodec+FFmpeg; soundfile instead"

SAVE_OLD = "        torchaudio.save(temp_filepath, audio_tensor, sample_rate)"
SAVE_NEW = f"""        {MARKER}
        import soundfile as _sf
        _t = audio_tensor.detach().to("cpu").float()
        # soundfile wants frames-major (samples, channels); torchaudio is
        # channels-major. A 1-D mono tensor is already frames-major.
        _sf.write(
            temp_filepath,
            _t.T.numpy() if _t.ndim > 1 else _t.numpy(),
            sample_rate,
        )"""

LOAD_OLD = "        audio, sr = torchaudio.load(audio_path)"
LOAD_NEW = f"""        {MARKER}
        import soundfile as _sf
        import torch as _torch
        _data, sr = _sf.read(audio_path, dtype="float32", always_2d=True)
        # back to channels-major for the rest of the pipeline
        audio = _torch.from_numpy(_data).T.contiguous()"""

TARGETS = [
    (PACK / "ace_step_nodes.py", SAVE_OLD, SAVE_NEW),
    (PACK / "ace_step" / "music_dcae" / "music_dcae_pipeline.py", LOAD_OLD, LOAD_NEW),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--revert", action="store_true")
    args = ap.parse_args()

    if not PACK.exists():
        print(f"ACE-Step pack not found: {PACK}")
        # Not an error: a machine without the pack simply has no repaint verb.
        return 0

    unpatched = 0
    for path, old, new in TARGETS:
        if not path.exists():
            print(f"MISSING  {path}")
            unpatched += 1
            continue
        backup = path.with_suffix(path.suffix + ".eosbak")

        if args.revert:
            if backup.exists():
                shutil.copy2(backup, path)
                backup.unlink()
                print(f"REVERTED {path.name}")
            else:
                print(f"no backup {path.name}")
            continue

        text = path.read_text(encoding="utf-8")
        if MARKER in text:
            print(f"OK       {path.name} (already patched)")
            continue
        if old not in text:
            # Refuse to guess. A changed call site means the pack moved and the
            # patch needs re-deriving against the new source, not fuzzy-matching.
            print(f"STALE    {path.name}: expected call site not found — re-derive the patch")
            unpatched += 1
            continue

        unpatched += 1
        if args.check:
            print(f"UNPATCHED {path.name}")
            continue

        if not backup.exists():
            shutil.copy2(path, backup)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        print(f"PATCHED  {path.name} (backup: {backup.name})")

    if args.check:
        return 1 if unpatched else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
