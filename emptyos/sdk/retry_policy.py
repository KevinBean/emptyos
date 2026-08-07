"""Retry-policy classifier — why an author→gate→retry loop should stop.

Every EmptyOS retry loop implements the ``bound`` stage (``emptyos/sdk/loops.py``)
as a **flat attempt ceiling**: try N times, give up with "ran out of attempts".
That throws away information the loop already has. A loop whose gate keeps
reporting *the same* defect is not going to fix it on attempt 3 — the brief is
wrong, not the attempt — and burning the remaining attempts costs real GPU
seconds (blockout: ~10s CPU + a think call each) or real money (fix-agent:
a paid claude-cli run each).

This module classifies *why* a loop should stop, so the caller can route on the
reason instead of retrying blindly. It is pure logic — stdlib only, no I/O, no
kernel import — so it unit-tests without a daemon.

REASON VOCABULARY (callers branch on these)
-------------------------------------------
``repeated_defect``  the same finding survived consecutive attempts and the
                     defect set did not shrink → the instruction is wrong;
                     stop retrying the same author with the same brief.
``churn``            the defect set turned over completely twice running →
                     each attempt fixes one axis and breaks another.
``ceiling``          ``len(history) >= max_attempts``. Non-bypassable.
``converging``       keep going (also the answer for an empty history).

Only ``converging`` has ``stop=False``.

TERMINATION GUARANTEE
---------------------
A caller that loops ``while not classify_retry(history, max_attempts=N).stop:``
can never run more than ``N`` iterations — ``ceiling`` fires purely on
``len(history)`` and no earlier branch can return ``stop=False`` once it is
reached. Do not weaken this: it is the property that makes the module safe to
put in front of an expensive loop.

WHY DEFECT SETS AND NOT A FIDELITY SCORE
----------------------------------------
Borrowed 2026-07-29 from img2threejs ``forge/stage4_review/correction_loop.py``
(Apache-2.0) — see the verdict note in
``30_Resources/Web-Clips/2026-07-29 img2threejs …``. The reference implementation
keys every condition off a numeric ``fidelity`` in [0,1]: it has a *scored*
vision gate, so it can detect a plateau (Δ < min_delta) and an oscillation
(direction flip over three scores).

EmptyOS gates are not scored. ``MediaVerdict{ok, hard, soft}`` is binary, and a
fix attempt either compiles-and-verifies or does not. Porting ``decide()``
verbatim would ship ``target_fidelity`` / ``min_delta`` / PLATEAU branches that
no EmptyOS consumer can feed. So the signals here are derived from the *defect
set* each attempt produced, which both real consumers already have in hand.

One deliberate divergence from the reference, which its score hides and an
unscored port would otherwise get wrong: a persisting tag is **not** a repeated
defect when the defect set is strictly shrinking. ``{FLAT, SMOOTH} → {FLAT}``
shares FLAT but is obvious progress; the reference tolerates this because its
rising fidelity score outvotes the shared tag. Without a score the subset check
is what keeps a converging loop alive.

Score-based plateau detection graduates in if a *scored* consumer ever appears
(CLAUDE.md rule 9) — do not add it speculatively.

CONSUMERS
---------
- ``apps/personal/music-studio/blockout.py`` — in-process author→render→depth-gate
  retry; defect tags are the ``hard`` findings from ``check_depth_sequence.py``.
- ``apps/extension/dev/dogfood-agent/drain.py`` — cross-invocation fix-prompt
  attempt budget; defect tags are coarse failure categories the drain derives
  from each attempt's error.

Normalising a raw error string into a tag is the **caller's** job — the SDK must
not know what an "ff-merge conflict" is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

__all__ = ["RetryDecision", "REASONS", "classify_retry", "repeated_tag"]

#: Every value ``RetryDecision.reason`` can take. Callers branch on these.
REASONS: tuple[str, ...] = ("repeated_defect", "churn", "ceiling", "converging")

#: Attempts of history required before ``churn`` can fire. Churn means "the
#: defect set turned over *twice*", which needs three attempts to observe. With
#: ``max_attempts=3`` that makes churn unreachable (``ceiling`` gets there
#: first) — deliberate: at three attempts the useful signal is
#: ``repeated_defect``, and churn earns its keep on longer budgets.
_CHURN_MIN_HISTORY = 3


@dataclass(frozen=True)
class RetryDecision:
    """Whether to retry, and why not.

    ``reason`` is the routing key (one of :data:`REASONS`); ``detail`` is a
    human sentence for the log/message and is never parsed."""

    stop: bool
    reason: str
    detail: str = ""


def _normalise(history: Sequence[Iterable[str]]) -> list[frozenset[str]]:
    """Coerce each attempt's defect tags to a frozenset of non-empty strings.

    Tolerant on purpose: a caller passing ``None`` for "no findings recorded",
    or a bare string instead of a list, should not crash a loop guard. A bare
    string is treated as ONE tag rather than being iterated into characters."""
    out: list[frozenset[str]] = []
    for entry in history or ():
        if entry is None:
            out.append(frozenset())
        elif isinstance(entry, str):
            tag = entry.strip()
            out.append(frozenset({tag}) if tag else frozenset())
        else:
            out.append(frozenset(str(t).strip() for t in entry if str(t).strip()))
    return out


def repeated_tag(
    last: Iterable[str] | None, prev: Iterable[str] | None
) -> str | None:
    """The defect that survived from ``prev`` into ``last``, or None.

    "Did the same failure repeat?" as a standalone question, for callers that
    want the signal without a termination decision. ``classify_retry`` uses it
    for its ``repeated_defect`` branch, so there is exactly one definition of a
    repeat and a consumer cannot drift from it.

    Returns None when either side is empty, when nothing is shared, or when the
    defect set strictly shrank (progress — see the module docstring). When
    several defects survived, returns the lexicographically first so the answer
    is stable across set iteration order.
    """
    a = _normalise([last])[0]
    b = _normalise([prev])[0]
    if not a or not b or a < b:
        return None
    shared = a & b
    return sorted(shared)[0] if shared else None


def _churning(hist: list[frozenset[str]]) -> bool:
    """True when the last three attempts each replaced the whole defect set.

    Requires all three to be non-empty — an attempt with no recorded findings
    is absence of evidence, not evidence of turnover."""
    if len(hist) < _CHURN_MIN_HISTORY:
        return False
    a, b, c = hist[-3], hist[-2], hist[-1]
    if not (a and b and c):
        return False
    return not (c & b) and not (b & a)


def classify_retry(
    history: Sequence[Iterable[str]],
    *,
    max_attempts: int,
) -> RetryDecision:
    """Decide whether an author→gate→retry loop should attempt again.

    Args:
        history: defect tags per **failed** attempt, oldest first — e.g.
            ``[{"FLAT"}, {"FLAT", "SMOOTH"}]``. An attempt that passed ends the
            loop on the caller's own success check, so it does not belong here;
            an empty entry is read as "no findings recorded", not as success.
        max_attempts: hard ceiling on attempts. Clamped to >= 1.

    Returns:
        :class:`RetryDecision`. Only ``reason == "converging"`` has
        ``stop=False``.

    Conditions are evaluated most-informative-first, so a loop that is both at
    its ceiling *and* repeating a defect reports ``repeated_defect`` — the
    reason that tells the caller what to do differently.
    """
    limit = max(1, int(max_attempts))
    hist = _normalise(history)

    if not hist:
        return RetryDecision(False, "converging", "no attempts yet")

    last = hist[-1]
    prev = hist[-2] if len(hist) >= 2 else None

    # repeated_defect — a finding survived, and the defect set did not shrink.
    # The subset guard is what keeps `{FLAT, SMOOTH} -> {FLAT}` alive; see the
    # module docstring on why an unscored port needs it.
    tag = repeated_tag(last, prev)
    if tag is not None:
        return RetryDecision(
            True,
            "repeated_defect",
            f"{tag} survived {len(hist)} consecutive attempts — "
            "the brief is wrong, not the attempt",
        )

    if _churning(hist):
        return RetryDecision(
            True,
            "churn",
            "the defect set turned over completely twice running — "
            "each attempt fixes one axis and breaks another",
        )

    if len(hist) >= limit:
        return RetryDecision(
            True, "ceiling", f"no success in {limit} attempts"
        )

    return RetryDecision(
        False,
        "converging",
        f"attempt {len(hist)} of {limit}"
        + (" — defect set shrinking" if prev and last and last < prev else ""),
    )
