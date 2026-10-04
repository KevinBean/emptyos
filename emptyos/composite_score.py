"""Composite scoring — normalize signals, weight them, roll up to one index.

Pure, stdlib-only, daemon-free: every consumer is either a pure helper or an app
method that must unit-test without booting the kernel. Same rationale as
`emptyos/frontmatter.py` and `emptyos/fieldspec.py`, one layer up.

**The unit is deliberately split in two**, because the consumers disagree about
normalization but agree about everything after it:

    normalize_values(...)   optional — four standard normalizers, for callers
                            whose inputs are raw comparable numbers
    composite_score(...)    the shared part — weight already-0..1 components,
                            resolve missing ones, roll up to a 0-100 index

Forcing every caller through a normalizer was the design mistake to avoid:
`briefing`/`hub-life` normalize by bespoke per-signal bucket logic (a boolean
presence check, a ratio capped at a ceiling) that no generic normalizer
expresses, while a corridor/suitability scorer genuinely wants min-max over a
numeric field. Splitting lets both share the half they actually share.

Lineage: the normalizer set and three of the sharp edges below are transcribed
from `opengeos/GeoLibre`'s composite score builder
(`packages/processing/src/statistics-tools.ts`, MIT) — read 2026-08-28, not
copied; theirs is TypeScript over inlined GeoJSON. The edges worth carrying
over, each of which is a silent wrong answer rather than an error:

  * a **constant field normalizes to 0.5, not 0.0** — min-max over zero spread
    is 0/0, and calling it zero silently penalises every subject equally on a
    criterion that discriminates nothing;
  * a **geometric mean needs a floor** (`GEOMETRIC_FLOOR`), or one component
    landing exactly on the min-max minimum drives the product to zero and every
    such subject ties at the bottom. The *technique* (floor before log) is
    standard practice for composite indicators; the specific magnitude `0.01`
    is transcribed from GeoLibre's implementation, which is the only hop
    verified here — the OECD handbook it credits is not vendored in this repo
    and was not read. Treat the value as a working default, not a sourced
    constant, and cite a primary source before defending it as one;
  * **missing is a policy** — `drop` and `renormalize` produce materially
    different rankings. `renormalize` is the parameter default because it is
    the forgiving one (a partially-attributed subject still scores), but that
    makes it the choice a caller makes *by omission*, so a caller that cares
    about unscorable subjects must pass `drop` deliberately.

One edge is ours, not theirs: a component whose weight is zero cannot make a
subject "missing" — the caller weighted it out, so its absence is not a gap.

See `docs/OPEN-SOURCE-BORROWING-PLAN.md` § opengeos/GeoLibre (2026-08-28).
"""

from __future__ import annotations

import math

__all__ = [
    "GEOMETRIC_FLOOR",
    "NEUTRAL",
    "CompositeError",
    "numeric_value",
    "normalize_values",
    "composite_score",
    "dimension_rollup",
]

# Floor applied to each component before its log in a geometric mean.
GEOMETRIC_FLOOR = 0.01
# What a criterion that cannot discriminate scores: neither reward nor penalty.
NEUTRAL = 0.5

_METHODS = ("minmax", "rank", "quantile", "zscore")
_DIRECTIONS = ("higher", "lower")
_NULL_POLICIES = ("drop", "renormalize")
_MEANS = ("arithmetic", "geometric")


class CompositeError(ValueError):
    """Caller error — an unknown method/policy, or weights that sum to zero."""


def numeric_value(value: object) -> float | None:
    """Coerce to float, or None when the value is not a usable number.

    Booleans are excluded deliberately even though `float(True)` succeeds: a
    True/False column is a category, and scoring it as 1.0/0.0 makes a flag look
    like a measurement. Numeric *strings* are accepted, because CSV and GeoJSON
    round-trips routinely quote numbers.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        as_float = float(value)
        return as_float if math.isfinite(as_float) else None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = float(text)
        except ValueError:
            return None
        return parsed if math.isfinite(parsed) else None
    return None


def _normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _mid_ranks(values: list[float]) -> list[float]:
    """1-based ranks; tied values share the average of the ranks they span."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def normalize_values(
    values: list[object],
    *,
    method: str = "minmax",
    direction: str = "higher",
) -> list[float | None]:
    """Map raw comparable values onto 0..1, higher = better.

    `direction="lower"` flips the result (`1 - v`) *after* normalization, so a
    lower-is-better criterion composes correctly with any of the four methods.

    Non-numeric and missing entries stay `None` and take no part in the
    statistics — they are the caller's to resolve via `composite_score`'s null
    policy, not something to silently impute here.
    """
    if method not in _METHODS:
        raise CompositeError(f"unknown method {method!r}; expected one of {_METHODS}")
    if direction not in _DIRECTIONS:
        raise CompositeError(
            f"unknown direction {direction!r}; expected one of {_DIRECTIONS}"
        )

    coerced = [numeric_value(v) for v in values]
    present = [(i, v) for i, v in enumerate(coerced) if v is not None]
    out: list[float | None] = [None] * len(coerced)
    if not present:
        return out

    nums = [v for _, v in present]
    n = len(nums)

    if n == 1 or max(nums) == min(nums):
        # No spread: the criterion discriminates nothing. Neutral, never zero.
        for i, _ in present:
            out[i] = NEUTRAL
    elif method == "minmax":
        lo, hi = min(nums), max(nums)
        span = hi - lo
        for i, v in present:
            out[i] = (v - lo) / span
    elif method in ("rank", "quantile"):
        ranks = _mid_ranks(nums)
        for (i, _), r in zip(present, ranks):
            out[i] = (r - 1.0) / (n - 1.0) if method == "rank" else (r - 0.5) / n
    else:  # zscore
        # No sd == 0 branch: zero spread means max == min, which the guard above
        # already returned on. A second check here would be unreachable code
        # pretending to be a safeguard.
        mean = sum(nums) / n
        sd = math.sqrt(sum((v - mean) ** 2 for v in nums) / n)
        for i, v in present:
            out[i] = _normal_cdf((v - mean) / sd)

    if direction == "lower":
        out = [None if v is None else 1.0 - v for v in out]
    return out


def composite_score(
    components: dict[str, float | None],
    weights: dict[str, float],
    *,
    mean: str = "arithmetic",
    null_policy: str = "renormalize",
    scale: float = 100.0,
    decimals: int = 2,
) -> float | None:
    """Combine already-0..1 components into one index, or None if unscorable.

    `weights` are relative — renormalized to sum to 1 across the components
    actually named, so callers never pre-balance them. Negative weights are
    clamped to zero rather than rejected: a negative weight is almost always a
    direction mistake, and the correct way to say "lower is better" is
    `normalize_values(direction="lower")`.

    `null_policy`: `"drop"` — any missing component with a non-zero weight makes
    the subject unscorable (returns None); `"renormalize"` — the present
    components' weights are re-divided among themselves. These give genuinely
    different rankings, so there is no safe default and the caller picks. A
    component with zero weight never counts as missing.

    **Scale.** Components must share a common scale, but only the *geometric*
    mean requires that scale to be 0..1 — `GEOMETRIC_FLOOR` is meaningless
    otherwise, so an out-of-range component raises rather than silently
    returning a number nobody can interpret. `arithmetic` is scale-free: a
    caller already holding 0..100 values passes them directly with `scale=1.0`,
    which matters because rescaling through 0..1 and back is *not* value-
    preserving — it moves results that land on a rounding boundary (measured:
    59 of 2000 cases shifted by 0.1 when work-fit was first migrated this way).
    """
    if mean not in _MEANS:
        raise CompositeError(f"unknown mean {mean!r}; expected one of {_MEANS}")
    if null_policy not in _NULL_POLICIES:
        raise CompositeError(
            f"unknown null_policy {null_policy!r}; expected one of {_NULL_POLICIES}"
        )

    effective = {k: max(0.0, float(w)) for k, w in weights.items()}
    if sum(effective.values()) <= 0.0:
        raise CompositeError("weights must include at least one positive value")

    if null_policy == "drop":
        for key, weight in effective.items():
            if weight > 0.0 and components.get(key) is None:
                return None

    usable = [
        (weight, float(components[key]))  # type: ignore[arg-type]
        for key, weight in effective.items()
        if weight > 0.0 and components.get(key) is not None
    ]
    if not usable:
        return None

    present_weight = sum(w for w, _ in usable)
    if mean == "arithmetic":
        combined = sum(w * v for w, v in usable) / present_weight
    else:
        out_of_range = [v for _, v in usable if v < 0.0 or v > 1.0]
        if out_of_range:
            raise CompositeError(
                "geometric mean requires components on 0..1 (GEOMETRIC_FLOOR "
                f"assumes it); got {out_of_range[0]!r}"
            )
        logs = sum(w * math.log(max(v, GEOMETRIC_FLOOR)) for w, v in usable)
        combined = math.exp(logs / present_weight)

    return round(combined * scale, decimals)


def dimension_rollup(dims: dict[str, dict]) -> dict:
    """Roll up `{name: {"score": s, "max": m, ...}}` into a total + percent.

    The shape the life-dashboard scorers already use, where each dimension
    carries its own points ceiling and that ceiling *is* the weight. Returns the
    dimensions untouched alongside `total`/`max`/`pct`, so a caller can render
    the breakdown that produced the number — a bare percentage with no visible
    components is not reviewable.

    Dimensions with a non-positive `max` are skipped rather than dividing by
    zero, and an all-empty table yields `pct: None` rather than a misleading 0.
    """
    total = 0.0
    ceiling = 0.0
    for entry in dims.values():
        if not isinstance(entry, dict):
            continue
        cap = numeric_value(entry.get("max"))
        if cap is None or cap <= 0:
            continue
        got = numeric_value(entry.get("score")) or 0.0
        total += max(0.0, min(got, cap))
        ceiling += cap
    if ceiling <= 0:
        return {"total": 0, "max": 0, "pct": None, "dimensions": dims}
    return {
        "total": round(total, 2),
        "max": round(ceiling, 2),
        "pct": round(total / ceiling * 100.0),
        "dimensions": dims,
    }
