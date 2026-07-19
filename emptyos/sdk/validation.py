"""Shared write-boundary validation primitives.

Extracted 2026-07-19 at the second consumer (CLAUDE.md rule 9): two engineering
apps' spec/parameter validators had structurally identical cores — iterate
named numeric fields, skip the ones the
caller didn't supply, coerce to float, reject non-numeric, reject non-positive.
A wider grep found the same shape in ~20 engineering modules.

The helper returns a **structured reason**, not a message, because the two
consumers legitimately phrase the same condition differently ("must be a
number" / "must be positive" vs "must be numeric" / "must be > 0") and their
tests pin that wording. Sharing the loop while leaving vocabulary to the caller
keeps this a single-purpose function instead of one with message parameters
that exist only because the callers disagree.
"""

from __future__ import annotations

import math
from typing import Iterable, Literal

# Why a field failed. ``not_a_number`` = present but not float-coercible;
# ``not_positive`` = coerced fine but <= 0.
Reason = Literal["not_a_number", "not_positive"]


def find_invalid_positive(
    data: dict,
    fields: Iterable[str],
) -> tuple[str, Reason] | None:
    """Return ``(field, reason)`` for the first present-but-invalid field, else None.

    Lenient by design: a field that is absent, ``None``, or ``""`` is treated as
    "the caller didn't supply it" and skipped, so partial payloads stay legal and
    the consumer's own defaults still apply. Only values the caller actually sent
    are checked.
    """
    for f in fields:
        if f not in data:
            continue
        v = data[f]
        if v is None or v == "":
            continue
        try:
            n = float(v)
        except (TypeError, ValueError):
            return (f, "not_a_number")
        # NaN/Infinity coerce fine, and `nan <= 0` is False, so a non-finite
        # value used to pass this positivity check as if it were valid. It
        # then persisted into a vault note and every later read of the whole
        # collection raised -- recoverable only by hand-editing the vault.
        # Report it as not_a_number: the caller's existing message for a
        # non-numeric field is the honest one, and both consumers already
        # phrase it.
        if not math.isfinite(n):
            return (f, "not_a_number")
        if n <= 0:
            return (f, "not_positive")
    return None
