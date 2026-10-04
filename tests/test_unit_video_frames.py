"""Unit tests for emptyos/sdk/video_frames.py — the ComfyUI I2V constraints.

CI-safe: imports only the SDK, so these run everywhere. The app-side companion
(tests/test_unit_music_studio_frames.py) skips without the gitignored
apps/personal/music-studio, which is exactly why the grid maths lives here.

Pins the 2026-07-25 defect: num_frames was computed as ``dur * fps_internal``
with no clamp, so a 15s scene asked for 360 frames — roughly 3x what a 16GB card
holds, and off the grid both latent nodes require. It failed silently.
"""

from __future__ import annotations

import pytest

from emptyos.sdk.video_frames import (
    DEFAULT_MAX_FRAMES,
    DIM_STEP,
    FRAME_GRID,
    LTX_FRAME_GRID,
    WAN_FRAME_GRID,
    dims_on_grid,
    frames_on_grid,
    snap_dim,
    snap_frames,
)


class TestGridConstants:
    def test_shared_grid_is_the_stricter_one(self):
        """Snapping to the stricter grid must satisfy the looser one."""
        assert FRAME_GRID == LTX_FRAME_GRID
        assert FRAME_GRID % WAN_FRAME_GRID == 0, (
            "the shared grid must be a multiple of Wan's, or 8n+1 counts would "
            "be invalid for Wan"
        )

    def test_default_max_is_itself_on_grid(self):
        assert frames_on_grid(DEFAULT_MAX_FRAMES)


class TestSnapFrames:
    @pytest.mark.parametrize("requested", [
        -5, 0, 1, 2, 5, 9, 33, 97, 100, 120, 121, 129, 180, 360, 10_000,
    ])
    def test_always_on_grid_and_within_cap(self, requested):
        n = snap_frames(requested)
        assert 1 <= n <= DEFAULT_MAX_FRAMES
        assert frames_on_grid(n), f"{n} is not accepted by both latent nodes"

    def test_the_actual_bug(self):
        """A 15s scene at 24fps asked for 360 frames. That was the defect."""
        assert snap_frames(15 * 24) == 121

    def test_snaps_down_never_up(self):
        for r in range(1, 200):
            assert snap_frames(r) <= max(1, min(r, DEFAULT_MAX_FRAMES))

    def test_monotonic(self):
        prev = 0
        for r in range(0, 400):
            n = snap_frames(r)
            assert n >= prev
            prev = n

    def test_exact_grid_values_pass_through(self):
        for k in range(0, 15):
            n = k * FRAME_GRID + 1
            assert snap_frames(n) == n

    @pytest.mark.parametrize("cap,expected", [
        (49, 49),      # on grid, honoured exactly
        (50, 49),      # off-grid cap must still snap down
        (249, 249),    # a 32GB card raising the budget
        (360, 353),    # off-grid, snaps to 8n+1
        (1, 1),
        (0, 1),        # nonsense cap must not produce 0 frames
    ])
    def test_maximum_is_snapped_too(self, cap, expected):
        """An off-grid config value must not leak an invalid count through."""
        assert snap_frames(10_000, maximum=cap) == expected
        assert frames_on_grid(snap_frames(10_000, maximum=cap))


class TestFramesOnGrid:
    @pytest.mark.parametrize("n", [1, 9, 17, 121, 249, 353])
    def test_accepts_valid(self, n):
        assert frames_on_grid(n)

    @pytest.mark.parametrize("n", [2, 8, 120, 122, 360, 5])
    def test_rejects_invalid(self, n):
        assert not frames_on_grid(n)


class TestDimensions:
    @pytest.mark.parametrize("w,h", [
        (512, 288), (832, 480), (1024, 576), (1536, 864), (576, 1024),
    ])
    def test_known_good_dims(self, w, h):
        assert dims_on_grid(w, h)

    @pytest.mark.parametrize("w,h", [
        (768, 432),    # the old "standard" preset
        (1920, 1080),  # the old "release" preset
        (1080, 1920),  # the old "shorts" preset
    ])
    def test_the_three_offending_presets(self, w, h):
        """These shipped as generation sizes the latent node cannot accept."""
        assert not dims_on_grid(w, h)

    def test_snap_dim_rounds_down_to_step(self):
        assert snap_dim(432) == 416
        assert snap_dim(1080) == 1056
        assert snap_dim(576) == 576

    def test_snap_dim_never_returns_zero(self):
        for v in (0, 1, 31, -100):
            assert snap_dim(v) == DIM_STEP

    def test_snap_dim_output_is_always_on_grid(self):
        for v in range(0, 2000, 7):
            assert dims_on_grid(snap_dim(v), snap_dim(v))


class TestExactSixteenNineIsRare:
    """Why the preset ladder is 512/1024/1536 and not 768/1920.

    Exact 16:9 with both dimensions on a 32-grid requires the width to be a
    multiple of 512 (width*9/16 divisible by 32, and gcd(9,512)==1). This test
    documents that, so nobody 'fixes' the ladder back to 768 or 1920.
    """

    def test_only_multiples_of_512_give_exact_16_9_on_grid(self):
        valid = [
            w for w in range(DIM_STEP, 4096 + DIM_STEP, DIM_STEP)
            if (w * 9) % 16 == 0 and dims_on_grid(w, w * 9 // 16)
        ]
        # The invariant, not a hand-listed enumeration — every valid width is a
        # multiple of 512, and every multiple of 512 in range is valid.
        assert valid, "no exact-16:9 on-grid widths found — check DIM_STEP"
        assert all(w % 512 == 0 for w in valid)
        assert valid == list(range(512, 4096 + 1, 512))
        # The shipped ladder is drawn from this set; 768 and 1920 are not in it.
        assert {512, 1024, 1536}.issubset(valid)
        assert 768 not in valid and 1920 not in valid
