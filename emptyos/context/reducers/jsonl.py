"""JSONL reducer — count rows, group by a stable field, keep anomalies.

Deterministic + query-independent. Preserves the first + last row verbatim and
every row whose status/level field reads like an error/anomaly, so failures are
never silently dropped. Unparseable lines are counted, not discarded silently.
"""

from __future__ import annotations

import json
from collections import Counter

from ..budget import est_tokens
from ..blocks import ReducerResult

# Fields commonly carrying a stable category to group by (first hit wins).
_GROUP_FIELDS = ("status", "level", "kind", "type", "event", "outcome")
_ANOMALY_VALUES = {
    "error",
    "fail",
    "failed",
    "failure",
    "timeout",
    "crash",
    "denied",
    "exception",
    "critical",
    "fatal",
}


def _anomalous(row: dict) -> bool:
    for f in _GROUP_FIELDS:
        v = row.get(f)
        if isinstance(v, str) and v.strip().lower() in _ANOMALY_VALUES:
            return True
    return False


def reduce(text: str) -> ReducerResult:
    original_tokens = est_tokens(text)
    raw_lines = [ln for ln in text.splitlines() if ln.strip()]
    rows: list[dict] = []
    unparsed = 0
    for ln in raw_lines:
        try:
            obj = json.loads(ln)
        except ValueError:
            unparsed += 1
            continue
        rows.append(obj if isinstance(obj, dict) else {"_value": obj})

    if not rows:
        # Nothing structured to summarize — return verbatim, flag it.
        return ReducerResult(
            text=text,
            original_tokens=original_tokens,
            packed_tokens=original_tokens,
            warnings=["jsonl: no parseable rows"],
        )

    group_field = next((f for f in _GROUP_FIELDS if any(f in r for r in rows)), None)
    lines_out: list[str] = [
        f"[jsonl summary: {len(rows)} rows, {unparsed} unparseable]"
    ]
    if group_field:
        counts = Counter(str(r.get(group_field, "?")) for r in rows)
        grouped = ", ".join(f"{k}={v}" for k, v in counts.most_common())
        lines_out.append(f"by {group_field}: {grouped}")

    # Keep first, last, and every anomalous row verbatim (deduped by identity).
    keep: list[dict] = []
    seen_ids: set[int] = set()

    def _add(r: dict) -> None:
        if id(r) not in seen_ids:
            seen_ids.add(id(r))
            keep.append(r)

    _add(rows[0])
    for r in rows:
        if _anomalous(r):
            _add(r)
    _add(rows[-1])

    lines_out.append("examples + anomalies:")
    for r in keep:
        lines_out.append(json.dumps(r, ensure_ascii=False))

    summary = "\n".join(lines_out)
    return ReducerResult(
        text=summary,
        original_tokens=original_tokens,
        packed_tokens=est_tokens(summary),
        warnings=[],
    )
