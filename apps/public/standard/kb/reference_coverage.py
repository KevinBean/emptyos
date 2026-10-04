"""Reference-source and clause coverage for the KB corpus.

This is a read model over the existing ``kind: reference`` and
``kind: clause`` primitives. The atomizer in :mod:`compose` remains the only
way to turn an archived standard into clause notes.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Callable

from emptyos.sdk import web_route

from .shared import (
    DEFAULT_REVIEW_INTERVAL_DAYS,
    _norm_edition,
    _norm_standard,
    _slug_of,
    next_review_due,
    reference_freshness,
)


def _reference_identity(note: dict) -> tuple[str, str, str]:
    props = note.get("properties", {}) or {}
    standard_id = str(props.get("standard_id") or "").strip()
    standard = _norm_standard(
        props.get("standard") or standard_id or props.get("title") or note.get("name") or ""
    )
    return standard_id.lower(), standard, _norm_edition(props.get("edition"))


def _clause_identity(note: dict) -> tuple[str, str, str]:
    props = note.get("properties", {}) or {}
    standard_id = str(props.get("standard_id") or "").strip()
    standard = _norm_standard(props.get("standard") or standard_id)
    return standard_id.lower(), standard, _norm_edition(props.get("edition"))


def reference_clause_match_score(
    reference: dict,
    clause: dict,
) -> tuple[int, int] | None:
    """Rank a clause-to-reference ownership match; ``None`` means no match.

    Reference coverage and KB health share this primitive so a clause surfaced
    through a composed reference landing page is not simultaneously reported
    as an unused clause by the health sweep.
    """
    ref_id, ref_standard, ref_edition = _reference_identity(reference)
    clause_id, clause_standard, clause_edition = _clause_identity(clause)
    if ref_edition and clause_edition and ref_edition != clause_edition:
        return None
    if ref_id and clause_id and ref_id == clause_id:
        return (3, len(ref_id))
    if ref_standard and clause_standard:
        if ref_standard == clause_standard:
            return (2, len(ref_standard))
        if clause_standard.startswith(ref_standard):
            return (1, len(ref_standard))
    return None


def _coverage_status(source_status: str, clause_count: int) -> str:
    if source_status == "missing":
        return "source-missing"
    if source_status == "available":
        return "digested" if clause_count else "undigested"
    return "clauses-only" if clause_count else "metadata-only"


def build_reference_coverage(
    notes: list[dict],
    source_exists: Callable[[str], bool] | None = None,
    today: _dt.date | None = None,
) -> dict:
    """Build a deterministic coverage report from already-indexed notes.

    Each clause is assigned to at most one reference (the strongest match), so
    totals remain honest when both series-level and part-specific notes exist.

    Also reports each reference's review freshness. ``unscheduled`` (no
    ``review_due``) is reported *here* rather than as a health finding — it is
    the normal state of a reference nobody has booked a re-check for, and
    flagging all of them would drown the health sweep.
    """
    today = today or _dt.date.today()
    references = [
        note for note in notes
        if (note.get("properties", {}) or {}).get("kind") == "reference"
    ]
    clauses = [
        note for note in notes
        if (note.get("properties", {}) or {}).get("kind") == "clause"
    ]
    assigned: dict[int, list[dict]] = {index: [] for index in range(len(references))}
    unmatched: list[dict] = []

    for clause in clauses:
        candidates = []
        for index, reference in enumerate(references):
            score = reference_clause_match_score(reference, clause)
            if score is not None:
                candidates.append((score, index))
        if not candidates:
            unmatched.append(clause)
            continue
        _, best_index = max(candidates, key=lambda item: (item[0], -item[1]))
        assigned[best_index].append(clause)

    rows = []
    for index, reference in enumerate(references):
        props = reference.get("properties", {}) or {}
        pointer = str(props.get("local_text") or props.get("source_file") or "").strip()
        if not pointer:
            source_status = "none"
        elif source_exists is not None and source_exists(pointer):
            source_status = "available"
        else:
            source_status = "missing"
        clause_count = len(assigned[index])
        fresh = reference_freshness(props, today)
        rows.append({
            "slug": _slug_of(reference.get("path", "")),
            "path": reference.get("path", ""),
            "title": props.get("title") or reference.get("name") or "",
            "standard_id": props.get("standard_id") or "",
            "standard": props.get("standard") or "",
            "edition": str(props.get("edition") or ""),
            "domain": props.get("domain") or "unknown",
            "source_file": pointer,
            "source_status": source_status,
            "clause_count": clause_count,
            "status": _coverage_status(source_status, clause_count),
            "freshness": fresh["state"],
            "review_due": fresh["review_due"],
            "source_checked_at": fresh["source_checked_at"],
            "effective_date": fresh["effective_date"],
            "days_overdue": fresh["days_overdue"],
        })

    status_order = {
        "source-missing": 0,
        "undigested": 1,
        "clauses-only": 2,
        "metadata-only": 3,
        "digested": 4,
    }
    rows.sort(key=lambda row: (
        status_order.get(row["status"], 9),
        (row["standard_id"] or row["title"]).lower(),
        row["edition"].lower(),
    ))

    missing_groups: dict[tuple[str, str], dict] = {}
    for clause in unmatched:
        props = clause.get("properties", {}) or {}
        standard = str(props.get("standard") or props.get("standard_id") or "Unknown standard")
        edition = str(props.get("edition") or "")
        key = (_norm_standard(standard), _norm_edition(edition))
        group = missing_groups.setdefault(key, {
            "standard": standard,
            "edition": edition,
            "clause_count": 0,
            "status": "missing-reference",
        })
        group["clause_count"] += 1
    missing = sorted(
        missing_groups.values(),
        key=lambda group: (group["standard"].lower(), group["edition"].lower()),
    )

    summary = {
        "references": len(rows),
        "clause_notes": len(clauses),
        "digested": sum(row["status"] == "digested" for row in rows),
        "undigested": sum(row["status"] == "undigested" for row in rows),
        "metadata_only": sum(row["status"] == "metadata-only" for row in rows),
        "clauses_only": sum(row["status"] == "clauses-only" for row in rows),
        "source_missing": sum(row["status"] == "source-missing" for row in rows),
        "missing_reference_groups": len(missing),
        "review_overdue": sum(row["freshness"] == "overdue" for row in rows),
        "review_scheduled": sum(row["freshness"] == "scheduled" for row in rows),
        "review_unscheduled": sum(row["freshness"] == "unscheduled" for row in rows),
        "review_malformed": sum(row["freshness"] == "malformed" for row in rows),
    }
    return {"summary": summary, "references": rows, "missing_references": missing}


def reference_coverage(self) -> dict:
    def source_exists(relative_path: str) -> bool:
        try:
            return (self.vault_root / relative_path).is_file()
        except (OSError, TypeError, ValueError):
            return False

    return build_reference_coverage(self._all_notes(), source_exists=source_exists)


@web_route("GET", "/api/reference-coverage")
async def api_reference_coverage(self, request):
    return reference_coverage(self)


@web_route("POST", "/api/references/{slug}/checked")
async def api_mark_reference_checked(self, request):
    """Record that a human has confirmed this reference is still the current
    edition: stamp ``source_checked_at`` = today and book the next
    ``review_due``.

    This is the *only* writer of those fields, and it exists so the
    ``stale_reference`` health bucket reads something a user can actually set —
    a bucket over a field nothing writes is dead code that renders an empty
    list forever.

    Body (all optional): ``interval_days`` (default ``[apps.kb]
    reference_review_interval_days``, else 365), ``effective_date``
    (``YYYY-MM-DD``, recorded verbatim when this edition came into force).
    """
    slug = (request.path_params.get("slug") or "").strip()
    # The slug is resolved by exact match against indexed notes, so it never
    # becomes a path fragment — no traversal surface to validate against.
    path = await self.note_path(slug) if slug else ""
    if not path:
        return {"ok": False, "error": "not_found", "slug": slug}

    props = self.vault_get_properties(path) or {}
    if props.get("kind") != "reference":
        return {
            "ok": False,
            "error": "not_a_reference",
            "kind": props.get("kind") or "",
            "hint": "review scheduling applies to `kind: reference` notes only",
        }

    body = {}
    try:
        raw = await request.json()
        if isinstance(raw, dict):
            body = raw
    except Exception:
        pass

    default_interval = self.app_config(
        "reference_review_interval_days", DEFAULT_REVIEW_INTERVAL_DAYS
    )
    # `or default` would be wrong here: 0 is falsy, so an explicit
    # `interval_days: 0` would silently become the default instead of being
    # rejected. Only absence falls through to the default.
    raw_interval = body.get("interval_days")
    if raw_interval is None or (isinstance(raw_interval, str) and not raw_interval.strip()):
        raw_interval = default_interval
    try:
        interval = int(raw_interval)
    except (TypeError, ValueError):
        return {"ok": False, "error": "bad_interval_days", "value": raw_interval}
    if interval < 1:
        return {"ok": False, "error": "bad_interval_days", "value": interval}

    today = _dt.date.today()
    update = {
        "source_checked_at": today.isoformat(),
        "review_due": next_review_due(today, interval).isoformat(),
    }

    effective = str(body.get("effective_date") or "").strip()
    if effective:
        try:
            update["effective_date"] = _dt.date.fromisoformat(effective[:10]).isoformat()
        except ValueError:
            return {"ok": False, "error": "bad_effective_date", "value": effective}

    async with self.note_lock(path):
        self.vault_update(path, update)

    await self.emit("kb:reference_checked", {
        "slug": slug, "path": path, "interval_days": interval, **update,
    })
    return {"ok": True, "slug": slug, "path": path, "interval_days": interval, **update}
