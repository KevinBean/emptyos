"""Frame-count and dimension constraints for the ComfyUI image-to-video workflows.

These are properties of the *workflows* under ``plugins/comfyui/workflows/``, not
of any one app — which is why they live here rather than beside a single caller.
Both shipped I2V workflows constrain their latent node:

    EmptyLTXVLatentVideo    (LTX-2.3)   length on an 8n+1 grid, w/h step 32
    Wan22ImageToVideoLatent (Wan 2.2)   length on a 4n+1 grid, w/h step 32

Every 8n+1 is also a 4n+1 (8n+1 == 4(2n)+1), so snapping to the stricter grid is
safe whichever workflow ``[plugins.comfyui] video_workflow`` points at — a caller
does not have to know which is active.

Placed in the SDK ahead of a second caller (CLAUDE.md rule 9) for one specific
reason: the app that consumes it lives under ``apps/personal/`` which is
gitignored, so an invariant pinned only there can never run in CI. The knowledge
is about tracked workflow files; the tests belong with the tracked code.

Pure — no kernel, no I/O. Tested in tests/test_unit_video_frames.py.
"""

from __future__ import annotations

# Latent-node grids, for reference and for callers that want to be explicit.
LTX_FRAME_GRID = 8
WAN_FRAME_GRID = 4

# The grid we actually snap to: strictest of the above, so it satisfies both.
FRAME_GRID = LTX_FRAME_GRID

# Both latent nodes declare ``step: 32`` on width/height.
DIM_STEP = 32

# Default per-generation frame ceiling. This is a VRAM budget, not a model
# limit: a 16GB card holds roughly 5s of 720p latent once the UNet is resident.
# Callers should let the operator raise it from config rather than editing here.
DEFAULT_MAX_FRAMES = 121


def snap_frames(requested: int, *, maximum: int = DEFAULT_MAX_FRAMES) -> int:
    """Clamp a frame request onto the shared frame grid, capped at ``maximum``.

    Snaps *down*, so the result never exceeds the requested duration or the cap.
    An off-grid ``maximum`` is itself snapped, so a sloppy config value cannot
    leak an invalid count through to the latent node.

    >>> snap_frames(15 * 24)        # a 15s scene at 24fps
    121
    >>> snap_frames(100)
    97
    >>> snap_frames(400, maximum=249)
    249
    """
    n = max(1, min(int(requested), int(maximum)))
    if n <= 1:
        return 1
    return ((n - 1) // FRAME_GRID) * FRAME_GRID + 1


def frames_on_grid(n: int) -> bool:
    """True when ``n`` is a frame count both latent nodes accept."""
    n = int(n)
    return n == 1 or ((n - 1) % LTX_FRAME_GRID == 0 and (n - 1) % WAN_FRAME_GRID == 0)


def snap_dim(value: int) -> int:
    """Round a width/height down to the nearest multiple of ``DIM_STEP``."""
    return max(DIM_STEP, (int(value) // DIM_STEP) * DIM_STEP)


def dims_on_grid(width: int, height: int) -> bool:
    """True when both dimensions satisfy the latent nodes' ``step: 32``."""
    return int(width) % DIM_STEP == 0 and int(height) % DIM_STEP == 0
