"""MV grade: per-shot exposure match, then a film look — as ffmpeg filter fragments.

Lifted from the 〈說得太急〉 rescue (rescue-v18, look "B + letterbox", chosen by
the user from a side-by-side comparison on 2026-09-26). Why each piece exists:

- Exposure match first: generated shots arrive at wildly different brightness,
  so each is scaled toward one mean luma before any look is applied. Shots
  whose light change IS the story (a lobby timing out) keep their exposure.
- Two colour zones: the present is one cold night, memory shots are warmer and
  paler with a faint 4% haze, so a cut home reads as the past without a word.
  12% haze was rejected as too blurred.
- Halation (a red-orange bloom from highlights only), then a 0.7 px soften and
  coarse grain to take the plastic edge off generated footage.
- A 2.39:1 letterbox, whose bottom bar carries the subtitles.

Everything returned here is a string; nothing runs ffmpeg except `shot_gain`,
which reads one frame. numpy is imported lazily.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

NIGHT = ("colorbalance=rs=-0.06:gs=0.01:bs=0.07:rm=-0.01:bm=0.02:rh=0.05:gh=0.01:bh=-0.05,"
         "eq=saturation=0.80:contrast=1.02,curves=all='0/0.02 0.25/0.21 0.5/0.5 0.75/0.79 1/0.95'")
MEMORY = ("colorbalance=rs=0.03:bs=-0.03:rm=0.04:bm=-0.04:rh=0.03:bh=-0.03,"
          "eq=saturation=0.72:contrast=0.99,curves=all='0/0.03 0.25/0.23 0.5/0.51 0.75/0.78 1/0.95'")
GRAIN = "gblur=sigma=0.7,noise=alls=9:allf=t,vignette=PI/4.5"
NIGHT_PULL = "eq=gamma=0.80:saturation=0.88:brightness=-0.03,"   # dusk-looking shot pulled to night


@dataclass
class Grade:
    """Per-film grade settings; the defaults are the rescue-v18 values."""
    exposure_target: float = 0.165
    gain_min: float = 0.55
    gain_max: float = 1.35
    memory_haze: float = 0.04
    halation_night: float = 0.45
    halation_memory: float = 0.55
    memory: set[str] = field(default_factory=set)         # clip ids graded as memory
    keep_exposure: set[str] = field(default_factory=set)  # clip ids left at their own exposure
    night_pull: set[str] = field(default_factory=set)     # clip ids that read as dusk


def letterbox(width: int = 1920, height: int = 1080, ratio: float = 2.39) -> str:
    """Crop to ``ratio`` and pad back to the frame, centred, with black bars."""
    inner = int(round(width / ratio / 2)) * 2
    top = (height - inner) // 2
    return f"crop={width}:{inner}:0:{top},pad={width}:{height}:0:{top}:black"


def shot_gain(path: str, frame: int, grade: Grade) -> float:
    """Exposure gain that brings this frame's mean luma to the grade's target."""
    import numpy as np

    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf",
                          f"select='eq(n,{frame})',scale=320:180", "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    if len(raw) != 320 * 180 * 3:
        raise ValueError(f"could not read frame {frame} of {path}")
    a = np.frombuffer(raw, np.uint8).reshape(180, 320, 3) / 255.0
    y = (0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]).mean()
    return float(np.clip(grade.exposure_target / max(y, 1e-3), grade.gain_min, grade.gain_max))


def look(i: int, clip: str, gain: float, grade: Grade, *,
         width: int = 1920, height: int = 1080, fps: int = 24) -> str:
    """Per-shot look as a filtergraph fragment from ``[pre{i}]`` to ``[v{i}]``."""
    mem = clip in grade.memory
    ex = f"colorchannelmixer=rr={gain:.3f}:gg={gain:.3f}:bb={gain:.3f}"
    op = grade.halation_memory if mem else grade.halation_night
    body = (f"[pre{i}]scale={width}:{height}:flags=lanczos,format=gbrp,{ex},"
            f"{MEMORY if mem else NIGHT}")
    if mem:   # faint haze
        body += (f",split[ha{i}][hb{i}];[hb{i}]gblur=sigma=3[hc{i}];"
                 f"[ha{i}][hc{i}]blend=all_mode=normal:all_opacity={grade.memory_haze:g}")
    # halation: red-orange bloom from the highlights only
    body += (f",split[m{i}][h{i}];[h{i}]curves=all='0/0 0.55/0 1/1',gblur=sigma=32,"
             f"colorchannelmixer=rr=1:gg=0.45:bb=0.25[g{i}];"
             f"[m{i}][g{i}]blend=all_mode=screen:all_opacity={op:g},{GRAIN},"
             f"format=yuv420p,setsar=1,fps={fps}[v{i}]")
    return body
