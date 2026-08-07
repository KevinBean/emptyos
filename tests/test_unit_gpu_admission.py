"""Unit tests for GPU admission math (emptyos.sdk.gpu).

Pure — no GPU, no nvidia-smi, no daemon.

The rows that matter most are the 16 GB vs 32 GB workload cases: they are the
executable statement of what the hardware upgrade actually buys. On 16 GB a
FLUX still cannot coexist with a loaded ollama model, so something must be
evicted before every render; on 32 GB the same pair fits and the eviction
disappears.
"""

from __future__ import annotations

import pytest

from emptyos.sdk.gpu import (
    COMFYUI_BASELINE_GB,
    Admission,
    headroom_gb,
    plan_admission,
    vram_fit,
)

# Real workload numbers from the MV pipeline / model catalog.
FLUX_STILL_GB = 12.0
LTX_VIDEO_GB = 14.0
OLLAMA_32K_GB = 6.6
CARD_16GB = 16.0
CARD_32GB = 32.0


# --- vram_fit: unchanged behaviour after the move from model-serving -------

@pytest.mark.parametrize(
    "need,total,expected",
    [
        (1.5, 16.0, "fits"),
        (12.0, 16.0, "fits"),       # 75% — still under the 80% line
        (14.0, 16.0, "tight"),      # 87.5% — over 80%, but it does fit
        (16.0, 16.0, "tight"),      # exactly the card
        (20.0, 16.0, "wont_fit"),
        (20.0, 32.0, "fits"),
        (5.0, 0, "unknown"),        # GPU total unreadable
        (5.0, None, "unknown"),
        (5.0, "not-a-number", "unknown"),
    ],
)
def test_vram_fit(need, total, expected):
    assert vram_fit(need, total) == expected


# --- headroom ------------------------------------------------------------

def test_headroom_takes_a_true_reading_at_face_value():
    """No baseline by default — a whole-card reading already counts every
    context on it. Subtracting COMFYUI_BASELINE_GB here would double-count."""
    assert headroom_gb(16.0, 6.6) == pytest.approx(16.0 - 6.6)


def test_headroom_applies_a_baseline_only_when_asked():
    """For a daemon-side estimate that omits ComfyUI's idle context."""
    assert headroom_gb(16.0, 6.6, baseline_gb=COMFYUI_BASELINE_GB) == pytest.approx(
        16.0 - 6.6 - COMFYUI_BASELINE_GB
    )


def test_headroom_never_negative():
    assert headroom_gb(16.0, 20.0) == 0.0


def test_headroom_tolerates_garbage():
    assert headroom_gb(None, 1.0) == 0.0
    assert headroom_gb(16.0, "nope") == 0.0


# --- the upgrade, stated as a test --------------------------------------

def test_16gb_must_evict_ollama_before_a_flux_still():
    got = plan_admission(
        FLUX_STILL_GB, CARD_16GB, active_gb=OLLAMA_32K_GB, reclaimable_gb=OLLAMA_32K_GB
    )
    assert got.free_first is True
    assert got.reason == "fits_after_free"


def test_32gb_fits_flux_and_ollama_together():
    got = plan_admission(
        FLUX_STILL_GB, CARD_32GB, active_gb=OLLAMA_32K_GB, reclaimable_gb=OLLAMA_32K_GB
    )
    assert got.free_first is False
    assert got.reason == "fits"


def test_16gb_cannot_hold_ltx_alongside_a_resident_still_model():
    """The current pipeline's actual squeeze: 14 GB video model, 12 GB resident."""
    got = plan_admission(
        LTX_VIDEO_GB, CARD_16GB, active_gb=FLUX_STILL_GB, reclaimable_gb=0.0
    )
    assert got.reason == "over_capacity"
    assert got.free_first is True


def test_32gb_holds_ltx_alongside_a_resident_still_model():
    got = plan_admission(
        LTX_VIDEO_GB, CARD_32GB, active_gb=FLUX_STILL_GB, reclaimable_gb=0.0
    )
    assert got.reason == "fits"
    assert got.free_first is False


# --- fail-soft: the check must never be the reason a render didn't run ----

@pytest.mark.parametrize("total", [0, 0.0, -1, None, "unreadable"])
def test_unreadable_gpu_admits_without_acting(total):
    got = plan_admission(12.0, total, active_gb=0.0)
    assert got == Admission(True, False, 0.0, "unknown")


def test_every_verdict_is_ok_true():
    """Advisory only — the planner reports, it never refuses."""
    cases = [
        (1.0, 32.0, 0.0, 0.0),
        (12.0, 16.0, 6.6, 6.6),
        (99.0, 16.0, 15.0, 0.0),
        (12.0, 0, 0.0, 0.0),
    ]
    for need, total, active, reclaim in cases:
        assert plan_admission(need, total, active, reclaimable_gb=reclaim).ok is True


def test_garbage_need_does_not_raise():
    assert plan_admission("nonsense", 16.0, 2.0).ok is True


# --- reserve headroom ----------------------------------------------------

def test_reserve_prevents_a_brim_full_fit():
    """32 GB card, 17 GB resident -> 15 GB free.

    A 15 GB job fits arithmetically but leaves nothing for allocator
    fragmentation, so the reserve rejects it; 14 GB clears.
    """
    tight = plan_admission(15.0, 32.0, active_gb=17.0, reserve_gb=1.0)
    assert tight.reason != "fits"
    loose = plan_admission(14.0, 32.0, active_gb=17.0, reserve_gb=1.0)
    assert loose.reason == "fits"


def test_should_act_mirrors_free_first():
    got = plan_admission(FLUX_STILL_GB, CARD_16GB, OLLAMA_32K_GB, reclaimable_gb=OLLAMA_32K_GB)
    assert got.should_act is got.free_first is True
