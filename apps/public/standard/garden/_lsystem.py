"""garden — geometry primitives shared across theme species grammars.

This module is intentionally small. Species grammars (i.e. "how to draw a
bamboo stalk under the sumi-e theme") live in `themes.py` because the
*shape* of a plant is part of the theme's identity, not a shared concept.
This file holds only the math + recursion primitives every grammar reuses.

Pure functions. No I/O, no theme awareness, no Plant awareness.
"""

from __future__ import annotations

import math
import random
from typing import Iterable


def rng_for(seed) -> random.Random:
    """Deterministic RNG keyed by anything stringifiable."""
    return random.Random(str(seed))


def stage_vigour(stage: str) -> float:
    """Map plant lifecycle stage → vigour multiplier in [0.2, 1.0].

    Used by grammars to scale plant size, leaf count, opacity, etc.
    Stages come from `_plot_sources.py` Plant.stage.
    """
    return {
        "seedling": 0.35,
        "budding": 0.95,
        "evergreen": 0.85,
        "wilting": 0.55,
        "dormant": 0.25,
    }.get(stage, 0.6)


def stage_droop(stage: str) -> float:
    """Map stage → how much the plant droops (radians of forward tilt)."""
    return {
        "seedling": 0.0,
        "budding": -0.05,   # slight upward eagerness
        "evergreen": 0.0,
        "wilting": 0.35,    # leaning forward
        "dormant": 0.55,
    }.get(stage, 0.0)


def q_curve(x1: float, y1: float, x2: float, y2: float,
            ctl_dx: float = 0.0, ctl_dy: float = 0.0) -> str:
    """Quadratic-bezier path string `M..Q..`. ctl_d* is offset from midpoint."""
    mx, my = (x1 + x2) / 2 + ctl_dx, (y1 + y2) / 2 + ctl_dy
    return f"M{x1:.1f},{y1:.1f} Q{mx:.1f},{my:.1f} {x2:.1f},{y2:.1f}"


def filled_leaf(x: float, y: float, side: int, length: float,
                width: float = 0.4) -> str:
    """Closed Q-curve leaf rooted at (x,y), pointing `side` (±1), length px.

    width is the leaf's lateral fatness as a fraction of length.
    Returns just the `d=` path string body.
    """
    tip_x = x + side * length
    tip_y = y - length * 0.15
    ctl_x = x + side * (length * 0.55)
    ctl_y = y - length * width
    back_ctl_y = y + length * width * 0.3
    return (
        f"M{x:.1f},{y:.1f} "
        f"Q{ctl_x:.1f},{ctl_y:.1f} {tip_x:.1f},{tip_y:.1f} "
        f"Q{ctl_x:.1f},{back_ctl_y:.1f} {x:.1f},{y + 1:.1f} Z"
    )


def branch_tree(rng: random.Random, x: float, y: float, angle: float,
                length: float, depth: int) -> list[tuple[float, float, float, float, float]]:
    """Recursive Y-branching. Returns list of (x1, y1, x2, y2, stroke_w)."""
    out: list[tuple[float, float, float, float, float]] = []
    if depth <= 0 or length < 4:
        return out
    x2 = x + math.cos(angle) * length
    y2 = y + math.sin(angle) * length
    w = max(0.6, depth * 0.7)
    out.append((x, y, x2, y2, w))
    if depth > 1:
        spread = rng.uniform(0.35, 0.7)
        out.extend(branch_tree(rng, x2, y2, angle - spread, length * 0.7, depth - 1))
        out.extend(branch_tree(rng, x2, y2, angle + spread, length * 0.7, depth - 1))
    return out


def jittered_anchors(rng: random.Random, width: float, count: int,
                     margin: float = 0.1) -> list[float]:
    """Return `count` x-coordinates spread across width with jitter.

    margin keeps anchors away from the edges (fraction of width).
    """
    if count <= 0:
        return []
    if count == 1:
        return [width * 0.5 + rng.uniform(-width * 0.05, width * 0.05)]
    span = width * (1 - 2 * margin)
    step = span / max(1, count - 1)
    start = width * margin
    return [start + step * i + rng.uniform(-step * 0.15, step * 0.15)
            for i in range(count)]


def simplex_1d(rng: random.Random, n: int, amplitude: float = 1.0) -> list[float]:
    """Cheap pseudo-simplex: sum of two sines + a low-amplitude noise.

    Returns `n` floats in roughly [-amplitude, amplitude]. Deterministic
    given the rng. Used for grass sway, vine undulation.
    """
    phase_a = rng.uniform(0, math.pi * 2)
    phase_b = rng.uniform(0, math.pi * 2)
    freq_a = rng.uniform(0.4, 0.9)
    freq_b = rng.uniform(1.5, 2.2)
    out: list[float] = []
    for i in range(n):
        v = (math.sin(phase_a + freq_a * i) * 0.7
             + math.sin(phase_b + freq_b * i) * 0.3)
        out.append(v * amplitude + rng.uniform(-0.05, 0.05) * amplitude)
    return out


def soil_tuft(x: float, y: float, kind: str = "arc") -> str:
    """A small "nothing's growing here" mark. `kind`: arc | dash | dot."""
    if kind == "dash":
        return (
            f'<path class="eos-g-soil" d="M{x - 10:.1f},{y:.1f} '
            f'L{x + 10:.1f},{y - 0.4:.1f}" stroke-width="1.2" opacity="0.4"/>'
        )
    if kind == "dot":
        return (
            f'<circle class="eos-g-soil" cx="{x:.1f}" cy="{y:.1f}" r="0.8" '
            f'opacity="0.5"/>'
        )
    # arc default
    return (
        f'<path class="eos-g-soil" d="M{x - 8:.1f},{y:.1f} '
        f'Q{x:.1f},{y - 2:.1f} {x + 8:.1f},{y:.1f}" fill="none"/>'
    )


def density_for(plants: list, max_count: int = 12) -> int:
    """How many plants to actually draw given the input list — caps the canvas."""
    return min(len(plants), max_count)


def map_anchors(width: float, plants: list, rng: random.Random,
                composition: str = "cluster") -> list[float]:
    """Pick x-coords for plants based on composition.

    cluster — even spread across width
    asymmetric_corner — bunched on one side (sumi-e 一角)
    centered — single subject in middle
    """
    n = density_for(plants)
    if n == 0:
        return []
    if composition == "asymmetric_corner":
        side = rng.choice([0.18, 0.78])  # left or right
        return [side * width + rng.uniform(-15, 15) for _ in range(n)]
    if composition == "centered":
        return [width * 0.5 + rng.uniform(-25, 25) for _ in range(n)]
    return jittered_anchors(rng, width, n)
