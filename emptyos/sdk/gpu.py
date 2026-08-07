"""GPU VRAM admission math — will this job fit, and what should we free first?

Five independent consumers share one card on a typical EmptyOS box: ollama (a
quantised LLM at 32k ctx), ComfyUI (a diffusion checkpoint, then a video model
plus a 12B text encoder), XTTS, the wav2vec2 pronounce service, and marker-pdf
OCR. Nothing coordinated them — a ComfyUI job was queued blind and, when ollama
happened to be holding 6.6 GB, died on a ten-minute timeout instead of a fast,
legible "free something first".

This module is the arithmetic half of the fix. It is **pure and synchronous**
on purpose:

- Pure, so it unit-tests without a daemon, a GPU, or nvidia-smi.
- Synchronous, so a decision is atomic. A sync function runs to completion on
  the event loop with no interleaving; the moment you add an ``await`` between
  "check headroom" and "act on it" another task can land in between (see
  `.claude/rules/dev-gotchas.md` § Async atomicity). The async orchestration
  that calls this — probing, freeing, re-probing — lives in the health plugin
  and is deliberately **advisory**: a lost race costs a redundant unload, never
  correctness.

Honesty about scope: an advisory check is all that is achievable here. Two of
the five consumers are separate OS processes with their own CUDA contexts and
no channel back to the daemon, so a lock only the daemon's own plugins respect
would report a safety it does not provide.
"""

from __future__ import annotations

from dataclasses import dataclass

#: ComfyUI holds a CUDA context worth roughly this much even with no model
#: loaded, so it is not counted as reclaimable headroom.
COMFYUI_BASELINE_GB = 2.0

#: Leave this much unallocated rather than filling the card to the brim —
#: allocator fragmentation makes a nominally-exact fit fail in practice.
DEFAULT_RESERVE_GB = 1.0


def vram_fit(need_gb: float, total_gb) -> str:
    """Classify how a model's approx VRAM need sits against the GPU total.

    Returns ``fits`` / ``tight`` / ``wont_fit`` / ``unknown``. ``unknown`` when
    the GPU total can't be read (no nvidia-smi and no ComfyUI to report it) —
    never guess.

    Originally defined in ``apps/public/labs/model-serving`` for its catalog
    badges; moved here when the admission check became the second consumer
    (CLAUDE.md rule 9).
    """
    try:
        total = float(total_gb)
    except (TypeError, ValueError):
        return "unknown"
    if total <= 0:
        return "unknown"
    if need_gb <= total * 0.8:
        return "fits"
    if need_gb <= total:
        return "tight"
    return "wont_fit"


def headroom_gb(total_gb: float, active_gb: float, *, baseline_gb: float = 0.0) -> float:
    """VRAM plausibly available for a new job, in GB (never negative).

    ``active_gb`` should be a ground-truth reading of everything resident on the
    card (nvidia-smi ``memory.used``), not a sum over the consumers the daemon
    happens to know about — the services it can't see are exactly the ones that
    make an optimistic estimate dangerous.

    ``baseline_gb`` defaults to **0** because a true reading already includes
    every context on the card, ComfyUI's idle ~2 GB among them; subtracting
    :data:`COMFYUI_BASELINE_GB` on top of it would double-count. Pass the
    baseline only when ``active_gb`` is a *daemon-side estimate* that omits it.
    Getting this backwards under-reports headroom by 2 GB — caught by
    ``tests/test_unit_comfyui_preflight.py``, not by review.
    """
    try:
        total, active = float(total_gb), float(active_gb)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, total - active - max(0.0, baseline_gb))


@dataclass(frozen=True)
class Admission:
    """The verdict on one prospective GPU job."""

    ok: bool
    free_first: bool
    headroom_gb: float
    reason: str

    @property
    def should_act(self) -> bool:
        """True when the caller has something useful to do (free something)."""
        return self.free_first


def plan_admission(
    need_gb: float,
    total_gb: float,
    active_gb: float,
    *,
    baseline_gb: float = 0.0,
    reserve_gb: float = DEFAULT_RESERVE_GB,
    reclaimable_gb: float = 0.0,
) -> Admission:
    """Decide whether a job needing ``need_gb`` should proceed as-is.

    ``reclaimable_gb`` is what could be recovered by evicting a lower-priority
    resident consumer (in practice: the loaded ollama model). It is what
    separates "won't fit" from "won't fit *yet*".

    Fail-soft by construction: an unreadable GPU (``total_gb <= 0``) returns
    ``ok=True, reason="unknown"``. A probe that can't answer must never block a
    render — the pre-existing behaviour was to queue blind, and the check only
    earns its place if it is strictly less likely to stop useful work.
    """
    try:
        need = float(need_gb)
    except (TypeError, ValueError):
        need = 0.0

    try:
        total = float(total_gb)
    except (TypeError, ValueError):
        total = 0.0
    if total <= 0:
        return Admission(True, False, 0.0, "unknown")

    free = headroom_gb(total, active_gb, baseline_gb=baseline_gb)
    want = need + max(0.0, reserve_gb)

    if free >= want:
        return Admission(True, False, round(free, 1), "fits")

    if free + max(0.0, reclaimable_gb) >= want:
        return Admission(True, True, round(free, 1), "fits_after_free")

    # Still short even after evicting what we can. Proceed anyway — the job may
    # well succeed with a model the caller can offload internally, and refusing
    # would be a regression against today's queue-blind behaviour. The verdict
    # is the signal; it is the log line that explains a later OOM.
    return Admission(True, True, round(free, 1), "over_capacity")
