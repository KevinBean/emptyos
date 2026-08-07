"""Conversation Ingest telemetry readers.

This module is the read-only boundary over ``data/imports`` artifacts produced
by the canonical ``eos-ai-conversation-ingest`` skill scripts. It deliberately
does not read provider message bodies and never writes source archives, digests,
derived notes, or the ingestion ledger.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any


QUEUE_FILES = (
    "ingestion-queue-current.json",
    "coverage-queue.json",
    "ingestion-queue.json",
)
AUDIT_FILES = ("evidence-audit-current.json", "evidence-audit.json")
MAX_PAGE_SIZE = 200
DEFAULT_LEDGER_PATH = "30_Resources/conversations/ai-conversation-ingestion-ledger.md"

_JSON_CACHE: dict[str, tuple[int, int, dict[str, Any]]] = {}


class ImportPathError(ValueError):
    """Raised when an import key escapes the configured import root."""


@dataclass(frozen=True)
class Artifact:
    path: Path | None
    data: dict[str, Any] | None
    error: str = ""


def _read_json(path: Path | None) -> Artifact:
    if path is None:
        return Artifact(None, None)
    try:
        stat = path.stat()
        key = str(path.resolve())
        cached = _JSON_CACHE.get(key)
        signature = (stat.st_mtime_ns, stat.st_size)
        if cached and cached[:2] == signature:
            return Artifact(path, cached[2])
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError("top-level JSON value must be an object")
        _JSON_CACHE[key] = (signature[0], signature[1], value)
        return Artifact(path, value)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return Artifact(path, None, f"{path.name}: {type(exc).__name__}: {exc}")


def _preferred_file(folder: Path, names: tuple[str, ...]) -> Path | None:
    """Choose the artifact by declared name priority — first match wins.

    This used to rank by ``st_mtime_ns`` with name order only as a tie-break,
    which made the answer depend on filesystem metadata rather than on content.
    Any tool that rewrites mtimes without changing bytes — a vault sync, a
    robocopy, a restore-from-backup — could silently swap which artifact the
    whole UI reads, with no error and no log line. The live claude import
    carries all three queue names and both audit names at once, so the swap is
    reachable, not theoretical: it would exchange a 1047-record sv4 audit for a
    727-record sv3 one.

    Freshness by name is what the ``-current`` suffix already encodes, and it is
    reproducible. (An in-file ``generated_at`` would be better still, but the
    producers in ``.agents/skills/eos-ai-conversation-ingest/scripts/`` do not
    emit one — the artifacts carry ``created_at`` per item only. Read it here
    once they do; do not infer freshness from the filesystem in the meantime.)
    """
    for name in names:
        candidate = folder / name
        if candidate.is_file():
            return candidate
    return None


def _shadowed_files(folder: Path, names: tuple[str, ...]) -> list[str]:
    """Supported artifacts present but out-ranked, so a stale winner is visible.

    Silence was the real cost of the mtime ranking: whichever file won, nothing
    said the others existed.
    """
    present = [name for name in names if (folder / name).is_file()]
    return present[1:]


def resolve_import(root: Path, import_key: str) -> Path:
    """Resolve one direct child of ``root`` and reject traversal."""
    key = (import_key or "").strip()
    if not key or key in {".", ".."} or "/" in key or "\\" in key:
        raise ImportPathError("invalid import key")
    root_resolved = root.resolve()
    candidate = (root_resolved / key).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ImportPathError("import key escapes import root") from exc
    if candidate.parent != root_resolved:
        raise ImportPathError("import key must name a direct child")
    if not candidate.is_dir():
        raise ImportPathError("import directory not found")
    return candidate


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _queue_items(queue: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not queue:
        return []
    rows = queue.get("items")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _audit_records(audit: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not audit:
        return []
    rows = audit.get("records")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _artifact_mtime(*paths: Path | None) -> float:
    values: list[float] = []
    for path in paths:
        if path is not None:
            try:
                values.append(path.stat().st_mtime)
            except OSError:
                pass
    return max(values, default=0.0)


def _summary(folder: Path) -> dict[str, Any]:
    queue_path = _preferred_file(folder, QUEUE_FILES)
    audit_path = _preferred_file(folder, AUDIT_FILES)
    queue_artifact = _read_json(queue_path)
    audit_artifact = _read_json(audit_path)
    queue = queue_artifact.data
    audit = audit_artifact.data
    queue_rows = _queue_items(queue)
    audit_rows = _audit_records(audit)
    errors = [e for e in (queue_artifact.error, audit_artifact.error) if e]

    total = _as_int((queue or {}).get("total_unique"), len(queue_rows))
    processed = _as_int((queue or {}).get("processed"))
    pending = _as_int((queue or {}).get("pending"), len(queue_rows))
    partial = sum(1 for row in queue_rows if row.get("capture_fidelity") == "partial")
    # Both counts fall back to walking the records. `complete` used to have no
    # fallback while its complement did, so an artifact missing only the summary
    # key reported "0 complete, N needs backfill" — two numbers that no longer
    # add up to the record count, rendered side by side on the overview tiles.
    complete = _as_int(
        (audit or {}).get("new_schema_complete"),
        sum(1 for row in audit_rows if row.get("new_schema_complete")),
    )
    backfill = _as_int(
        (audit or {}).get("needs_backfill"),
        sum(1 for row in audit_rows if not row.get("new_schema_complete")),
    )
    missing = (audit or {}).get("missing_from_export") or []
    missing_count = len(missing) if isinstance(missing, list) else 0
    living_note_mutations = _as_int(
        (audit or {}).get("living_note_mutations"),
        sum(1 for row in audit_rows if row.get("derived_notes")),
    )
    routing_review = _as_int(
        (audit or {}).get("routing_review_required"),
        sum(1 for row in audit_rows if row.get("routing_review_required")),
    )
    accounted_no_mutation = _as_int(
        (audit or {}).get("accounted_no_mutation"),
        sum(
            1
            for row in audit_rows
            if not row.get("derived_notes")
            and (row.get("routing_checks") or {}).get("durable_delta_count")
            and not row.get("routing_review_required")
        ),
    )
    no_durable_delta = _as_int(
        (audit or {}).get("no_durable_delta"),
        sum(
            1
            for row in audit_rows
            if not row.get("derived_notes")
            and not (row.get("routing_checks") or {}).get("durable_delta_count")
            and row.get("new_schema_complete")
        ),
    )

    if errors:
        status = "degraded"
    elif queue and audit:
        status = "audited"
    elif queue:
        status = "queued"
    elif audit:
        status = "audit-only"
    else:
        status = "unsupported"

    reason_counts: Counter[str] = Counter()
    for row in audit_rows:
        for reason in row.get("reasons") or []:
            if isinstance(reason, str) and reason:
                reason_counts[reason] += 1

    return {
        "key": folder.name,
        "provider": (queue or audit or {}).get("provider", ""),
        "status": status,
        "errors": errors,
        "queue_file": queue_path.name if queue_path else None,
        "audit_file": audit_path.name if audit_path else None,
        # Name the artifacts we did NOT read. Selection is deterministic now,
        # but "deterministic" and "correct" are different claims: if a producer
        # writes a stale `-current`, the fresher sibling loses silently. Saying
        # which files were shadowed is what makes that inspectable at all.
        "shadowed_files": (
            _shadowed_files(folder, QUEUE_FILES) + _shadowed_files(folder, AUDIT_FILES)
        ),
        "modified": _artifact_mtime(queue_path, audit_path),
        "total": total,
        "processed": processed,
        "pending": pending,
        "partial": partial,
        "new_schema_complete": complete,
        "needs_backfill": backfill,
        "missing_from_export": missing_count,
        "living_note_mutations": living_note_mutations,
        "routing_review_required": routing_review,
        "accounted_no_mutation": accounted_no_mutation,
        "no_durable_delta": no_durable_delta,
        "processed_in_ledger": _as_int((audit or {}).get("processed_in_ledger")),
        "audited_in_export": _as_int((audit or {}).get("audited_in_export")),
        "next_pending": normalize_queue_item((queue or {}).get("next_pending") or {}),
        "top_backfill_reasons": [
            {"reason": reason, "count": count}
            for reason, count in reason_counts.most_common(8)
        ],
    }


def discover_imports(root: Path) -> dict[str, Any]:
    """Discover supported import directories without allowing one bad folder to fail all."""
    root = Path(root)
    if not root.exists():
        return {
            "root": str(root),
            "root_ok": False,
            "error": "import root not found",
            "imports": [],
            "active": None,
        }
    if not root.is_dir():
        return {
            "root": str(root),
            "root_ok": False,
            "error": "import root is not a directory",
            "imports": [],
            "active": None,
        }

    rows: list[dict[str, Any]] = []
    try:
        folders = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name)
    except OSError as exc:
        return {
            "root": str(root),
            "root_ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "imports": [],
            "active": None,
        }
    for folder in folders:
        if _preferred_file(folder, QUEUE_FILES + AUDIT_FILES):
            rows.append(_summary(folder))
    rows.sort(key=lambda row: (row.get("modified", 0), row.get("key", "")), reverse=True)
    return {
        "root": str(root),
        "root_ok": True,
        "error": "",
        "imports": rows,
        "active": rows[0]["key"] if rows else None,
    }


def load_import(root: Path, import_key: str) -> tuple[dict[str, Any], dict[str, Any]]:
    folder = resolve_import(Path(root), import_key)
    queue = _read_json(_preferred_file(folder, QUEUE_FILES))
    audit = _read_json(_preferred_file(folder, AUDIT_FILES))
    errors = [e for e in (queue.error, audit.error) if e]
    if errors:
        raise ValueError("; ".join(errors))
    return queue.data or {}, audit.data or {}


def normalize_queue_item(row: dict[str, Any]) -> dict[str, Any]:
    """Whitelist queue metadata so raw provider bodies can never leak through an API."""
    if not isinstance(row, dict):
        return {}
    keys = (
        "provider_id",
        "title",
        "created_at",
        "updated_at",
        "message_count",
        "attachment_count",
        "file_count",
        "capture_fidelity",
        "batch",
        "ordinal",
    )
    return {key: row.get(key) for key in keys if row.get(key) is not None}


def _normalize_audit_record(
    row: dict[str, Any],
    *,
    schema_version: int = 1,
    ledger_path: str = DEFAULT_LEDGER_PATH,
) -> dict[str, Any]:
    checks = row.get("source_checks") if isinstance(row.get("source_checks"), dict) else {}
    digest_checks = (
        row.get("digest_checks") if isinstance(row.get("digest_checks"), dict) else {}
    )
    links = row.get("reciprocal_links") if isinstance(row.get("reciprocal_links"), dict) else {}
    derived = row.get("derived_notes") if isinstance(row.get("derived_notes"), list) else []
    dispositions = (
        row.get("delta_dispositions")
        if isinstance(row.get("delta_dispositions"), list)
        else []
    )
    routing_checks = (
        row.get("routing_checks")
        if isinstance(row.get("routing_checks"), dict)
        else {}
    )
    domain_statuses = (
        row.get("domain_statuses")
        if isinstance(row.get("domain_statuses"), dict)
        else {}
    )
    disposition_targets = (
        row.get("disposition_targets")
        if isinstance(row.get("disposition_targets"), dict)
        else {}
    )
    reasons = row.get("reasons") if isinstance(row.get("reasons"), list) else []
    ledger = (
        row.get("ledger_receipt")
        if isinstance(row.get("ledger_receipt"), dict)
        else {}
    )
    # A v1 record predates the digest readback checks, so the auditor never ran
    # them. That is not the same as "they passed" — it is "nobody looked". This
    # used to be ORed into all five digest_checks below, which made the UI print
    # "verified by Vault readback" for a readback that never happened, in the
    # one feature whose entire premise is that every claim has a receipt.
    # Keep the checks honest and carry the legacy state as its own fact.
    # `== 1`, not `< 2`: the legacy inference is grounded in what schema v1
    # actually did, so it may only be applied to a record that *declares* v1.
    # An artifact whose schema_version is absent or unparseable normalises to 0
    # and must fall through to the honest path, not inherit v1's benefit of the
    # doubt — that would be failing open on a corrupt file.
    digest_legacy_assumed = (
        schema_version == 1
        and bool(row.get("new_schema_complete"))
        and bool(row.get("digest_path"))
    )
    return {
        "provider_id": row.get("provider_id"),
        "title": row.get("title"),
        "date": row.get("date"),
        "source_path": row.get("source_path"),
        "digest_path": row.get("digest_path"),
        "source_checks": {
            "contract_complete": bool(checks.get("contract_complete")),
            "hash_verified": bool(checks.get("hash_verified")),
            "message_count_verified": bool(checks.get("message_count_verified")),
            "provider_id_verified": bool(checks.get("provider_id_verified")),
        },
        "digest_checks": {
            "required_sections_verified": bool(
                digest_checks.get("required_sections_verified")
            ),
            "domain_coverage_verified": bool(
                digest_checks.get("domain_coverage_verified")
            ),
            "derived_frontmatter_declared": bool(
                digest_checks.get("derived_frontmatter_declared")
            ),
            "provider_id_verified": bool(digest_checks.get("provider_id_verified")),
            "source_link_verified": bool(digest_checks.get("source_link_verified")),
        },
        "digest_legacy_assumed": digest_legacy_assumed,
        "domain_statuses": {
            str(key): str(value) for key, value in domain_statuses.items()
        },
        "derived_notes": [str(value) for value in derived if value],
        "derived_skip_reason": str(row.get("derived_skip_reason") or "").strip(),
        "no_mutation_reason": str(
            row.get("no_mutation_reason")
            or row.get("derived_skip_reason")
            or ""
        ).strip(),
        "delta_dispositions": [
            {
                "domain": str(value.get("domain") or ""),
                "disposition": str(value.get("disposition") or ""),
                "target": str(value.get("target") or ""),
                "reason": str(value.get("reason") or ""),
                "routing_evidence": str(
                    value.get("routing_evidence") or ""
                ),
            }
            for value in dispositions
            if isinstance(value, dict)
        ],
        "disposition_targets": {
            str(key): bool(value) for key, value in disposition_targets.items()
        },
        "routing_checks": {
            "durable_delta_count": _as_int(
                routing_checks.get("durable_delta_count")
            ),
            "needs_review_count": _as_int(
                routing_checks.get("needs_review_count")
            ),
            "delta_dispositions_required": bool(
                routing_checks.get("delta_dispositions_required")
            ),
            "delta_dispositions_verified": bool(
                routing_checks.get("delta_dispositions_verified")
            ),
            "needs_review_resolved": bool(
                routing_checks.get("needs_review_resolved")
            ),
            "routing_search_evidence_required": bool(
                routing_checks.get("routing_search_evidence_required")
            ),
            "routing_search_evidence_verified": bool(
                routing_checks.get("routing_search_evidence_verified")
            ),
            "routing_review_required": bool(
                routing_checks.get("routing_review_required")
                or row.get("routing_review_required")
            ),
        },
        "reciprocal_links": {str(key): bool(value) for key, value in links.items()},
        "ledger_receipt": {
            "path": str(ledger.get("path") or ledger_path),
            # Schema-v1 records were emitted only for IDs found in the ledger, so
            # the record's existence IS the evidence. That reasoning is specific
            # to v1 — `== 1`, not `< 2`, or an artifact with an unreadable
            # schema_version (normalised to 0) would inherit an inference that
            # was never made about it.
            "provider_id_verified": bool(ledger.get("provider_id_verified"))
            or schema_version == 1,
            "verification_basis": (
                "provider-id-readback"
                if schema_version >= 2
                else "audit-record-v1"
                if schema_version == 1
                else "unknown-schema"
            ),
            "empty_or_transient_verified": bool(
                ledger.get("empty_or_transient_verified")
            ),
        },
        "empty_export_verified": bool(row.get("empty_export_verified")),
        "audit_schema_version": schema_version,
        "new_schema_complete": bool(row.get("new_schema_complete")),
        "routing_review_required": bool(
            row.get("routing_review_required")
            or routing_checks.get("routing_review_required")
        ),
        "reasons": [str(value) for value in reasons if value],
    }


def _receipt(receipts: list[dict[str, Any]], receipt_id: str) -> dict[str, Any]:
    """Look a receipt up by its stable id rather than its list position.

    Every entry `_operation_receipts` returns already carries an `id`
    (source/digest/derived/ledger). Reaching for `receipts[2]` instead couples
    each call site to the current ordering, and `display_outcome` exists only on
    `derived` — so an inserted or reordered receipt is a runtime KeyError in a
    UI aggregation path, not a test failure.
    """
    for receipt in receipts:
        if receipt.get("id") == receipt_id:
            return receipt
    raise KeyError(f"no receipt with id {receipt_id!r}")


def _operation_receipts(
    audit_row: dict[str, Any] | None,
    *,
    provider_id: str,
    ledger_path: str = DEFAULT_LEDGER_PATH,
    ledger_only: bool = False,
) -> list[dict[str, Any]]:
    """Build the four visible Vault readback receipts for one conversation."""
    row = audit_row or {}
    empty_export_verified = bool(row.get("empty_export_verified"))
    source_path = row.get("source_path")
    source_checks = row.get("source_checks") or {}
    # `and bool(source_checks)` mirrors the digest branch below. Without it,
    # `all({}.values())` is True, so a row carrying a source_path but no checks
    # at all reads fully verified. Latent today — _normalize_audit_record always
    # materialises the four keys — but the asymmetry is the bug, not the luck.
    source_ok = empty_export_verified or (
        bool(source_path) and bool(source_checks) and all(source_checks.values())
    )

    digest_path = row.get("digest_path")
    digest_checks = row.get("digest_checks") or {}
    digest_readback_ok = empty_export_verified or (
        bool(digest_path)
        and bool(digest_checks)
        and all(digest_checks.values())
    )
    # A v1 record completed under the old schema but was never readback-checked.
    # It is not a failure (nothing went wrong) and not a verification (nothing
    # was looked at) — so `ok` stays True and `verified` does not.
    digest_legacy_assumed = bool(row.get("digest_legacy_assumed")) and not digest_readback_ok
    digest_ok = digest_readback_ok or digest_legacy_assumed

    derived = list(row.get("derived_notes") or [])
    reciprocal = row.get("reciprocal_links") or {}
    no_mutation_reason = str(
        row.get("no_mutation_reason")
        or row.get("derived_skip_reason")
        or ""
    ).strip()
    dispositions = list(row.get("delta_dispositions") or [])
    disposition_types = {
        str(item.get("disposition") or "")
        for item in dispositions
        if isinstance(item, dict) and item.get("disposition")
    }
    disposition_targets = row.get("disposition_targets") or {}
    routing_checks = row.get("routing_checks") or {}
    routing_review_required = bool(
        row.get("routing_review_required")
        or routing_checks.get("routing_review_required")
    )
    schema_version = _as_int(row.get("audit_schema_version"), 1)
    if empty_export_verified:
        derived_ok = True
        derived_outcome = "no-durable-delta"
        derived_display = "no conversation content"
        derived_detail = no_mutation_reason or (
            "The provider export contains no messages, attachments, or files."
        )
    elif derived:
        derived_ok = all(reciprocal.get(note) for note in derived)
        derived_outcome = "updated" if derived_ok else "incomplete"
        derived_display = (
            f"{len(derived)} living note(s) updated"
            if derived_ok
            else "living-note evidence incomplete"
        )
        derived_detail = (
            f"{len(derived)} routed note(s); reciprocal evidence verified."
            if derived_ok
            else "One or more routed notes are missing reciprocal evidence."
        )
    elif routing_review_required:
        derived_ok = False
        derived_outcome = "routing-review"
        derived_display = "semantic routing review required"
        derived_detail = (
            "A durable delta was left without a living-note mutation and its "
            "Vault-search/comparison evidence is incomplete."
        )
    elif schema_version < 3:
        derived_ok = False
        derived_outcome = "legacy-unverified"
        derived_display = "legacy routing unverified"
        derived_detail = (
            "This pre-v3 audit accepted generic skip prose. Re-audit the digest "
            "against the per-domain no-mutation contract."
        )
    else:
        durable_delta_count = _as_int(
            routing_checks.get("durable_delta_count")
        )
        needs_review_count = _as_int(
            routing_checks.get("needs_review_count")
        )
        dispositions_ok = bool(
            routing_checks.get("delta_dispositions_verified")
        )
        needs_review_resolved = bool(
            routing_checks.get("needs_review_resolved")
        )
        if durable_delta_count:
            derived_ok = (
                dispositions_ok
                and needs_review_resolved
                and len(dispositions) >= durable_delta_count
            )
            derived_outcome = (
                "accounted-no-mutation" if derived_ok else "incomplete"
            )
            if not derived_ok:
                derived_display = "routing evidence incomplete"
            elif disposition_types == {"reused-no-change"}:
                derived_display = "existing living note reused"
            elif disposition_types == {"duplicate-of"}:
                derived_display = "duplicate routed to canonical digest"
            elif disposition_types == {"digest-contained"}:
                derived_display = "durable delta retained in digest"
            else:
                derived_display = "accounted without living-note mutation"
            derived_detail = (
                f"{durable_delta_count} durable domain delta(s) were accounted "
                "for without changing a living note."
                if derived_ok
                else "One or more durable domain deltas lack a valid disposition."
            )
        else:
            derived_ok = (
                bool(no_mutation_reason)
                and needs_review_count == 0
                and needs_review_resolved
            )
            derived_outcome = "no-durable-delta" if derived_ok else "incomplete"
            derived_display = (
                "no durable living-note delta"
                if derived_ok
                else "routing evidence incomplete"
            )
            derived_detail = no_mutation_reason or (
                "No routed note and no explicit no-durable-delta reason."
            )

    ledger = row.get("ledger_receipt") or {}
    ledger_verified = bool(ledger.get("provider_id_verified")) or ledger_only
    resolved_ledger_path = str(ledger.get("path") or ledger_path)

    return [
        {
            "id": "source",
            "label": "Source",
            "outcome": (
                "not-required-empty"
                if empty_export_verified
                else "verified"
                if source_ok
                else "incomplete"
                if source_path
                else "missing"
            ),
            "ok": source_ok,
            "verified": source_ok,
            "path": source_path,
            "detail": (
                "Provider export and ledger both verify zero messages, "
                "attachments, and files; no source archive is created."
                if empty_export_verified
                else "Message count, body SHA-256, provider ID, and archive contract "
                "verified by Vault readback."
                if source_ok
                else "Source archive readback is missing or failed one or more checks."
            ),
            "checks": source_checks,
        },
        {
            "id": "digest",
            "label": "Digest",
            "outcome": (
                "not-required-empty"
                if empty_export_verified
                else "legacy-assumed"
                if digest_legacy_assumed
                else "verified"
                if digest_readback_ok
                else "incomplete"
                if digest_path
                else "missing"
            ),
            "ok": digest_ok,
            "verified": digest_readback_ok,
            "path": digest_path,
            "detail": (
                "No digest is created because the verified provider record "
                "contains no conversation content."
                if empty_export_verified
                else "Recorded complete under the pre-readback schema, so no digest "
                "readback was ever performed. Re-audit to turn this into a receipt."
                if digest_legacy_assumed
                else "Provider ID, source link, required sections, domain scan, and "
                "derived declaration verified by Vault readback."
                if digest_readback_ok
                else "Digest readback is missing or failed one or more checks."
            ),
            "checks": digest_checks,
        },
        {
            "id": "derived",
            "label": "Living-note routing",
            "outcome": derived_outcome,
            "display_outcome": derived_display,
            "ok": derived_ok,
            "verified": derived_ok,
            "path": None,
            "detail": derived_detail,
            "notes": [
                {"path": note, "reciprocal": bool(reciprocal.get(note))}
                for note in derived
            ],
            "no_mutation_reason": no_mutation_reason,
            "dispositions": dispositions,
            "disposition_targets": disposition_targets,
        },
        {
            "id": "ledger",
            "label": "Ledger",
            "outcome": "verified" if ledger_verified else "missing",
            "ok": ledger_verified,
            "verified": ledger_verified,
            "path": resolved_ledger_path,
            "detail": (
                f"Provider ID {provider_id} is present in the ingestion ledger."
                if ledger_verified
                else "Provider ID was not verified in the ingestion ledger."
            ),
            "verification_basis": ledger.get("verification_basis", ""),
        },
    ]


def _joined_rows(queue: dict[str, Any], audit: dict[str, Any]) -> list[dict[str, Any]]:
    queue_rows = _queue_items(queue)
    audit_rows = _audit_records(audit)
    queue_by_id = {
        str(row.get("provider_id")): normalize_queue_item(row)
        for row in queue_rows
        if row.get("provider_id")
    }
    # Default 0, not 1. A missing or malformed schema_version used to land on
    # the MOST permissive value — the legacy branch that assumes completeness —
    # so a corrupt artifact failed OPEN into "verified". 0 is below every real
    # schema, so an unreadable version now fails closed.
    schema_version = _as_int(audit.get("schema_version"), 0)
    ledger_path = str(audit.get("ledger_path") or DEFAULT_LEDGER_PATH)
    audit_by_id = {
        str(row.get("provider_id")): _normalize_audit_record(
            row,
            schema_version=schema_version,
            ledger_path=ledger_path,
        )
        for row in audit_rows
        if row.get("provider_id")
    }
    order = list(queue_by_id)
    order.extend(provider_id for provider_id in audit_by_id if provider_id not in queue_by_id)

    rows: list[dict[str, Any]] = []
    for provider_id in order:
        q = queue_by_id.get(provider_id, {})
        a = audit_by_id.get(provider_id, {})
        checks = a.get("source_checks") or {}
        receipts = _operation_receipts(
            a or None,
            provider_id=provider_id,
            ledger_path=ledger_path,
            ledger_only=provider_id in (audit.get("missing_from_export") or []),
        )
        rows.append(
            {
                "provider_id": provider_id,
                "title": q.get("title") or a.get("title") or "Untitled",
                "date": q.get("created_at") or a.get("date") or "",
                "message_count": q.get("message_count"),
                "attachment_count": q.get("attachment_count", 0),
                "file_count": q.get("file_count", 0),
                "capture_fidelity": q.get("capture_fidelity") or "",
                "pending": provider_id in queue_by_id,
                "audited": provider_id in audit_by_id,
                "new_schema_complete": bool(a.get("new_schema_complete")),
                "routing_review_required": bool(
                    a.get("routing_review_required")
                ),
                "source_path": a.get("source_path"),
                "digest_path": a.get("digest_path"),
                "derived_count": len(a.get("derived_notes") or []),
                "hash_verified": bool(checks.get("hash_verified")),
                "message_count_verified": bool(checks.get("message_count_verified")),
                # `verified`, not `ok` — a legacy-assumed digest is not a failure
                # but it is also not a receipt, and counting it would put the
                # same unearned claim back in the list badge ("4/4 verified").
                "receipt_verified": sum(
                    1 for receipt in receipts if receipt.get("verified", receipt["ok"])
                ),
                "receipt_total": len(receipts),
                "derived_outcome": _receipt(receipts, "derived")["outcome"],
                "derived_display": _receipt(receipts, "derived")["display_outcome"],
                "reasons": list(a.get("reasons") or []),
            }
        )
    return rows


def _matches(row: dict[str, Any], query: str) -> bool:
    if not query:
        return True
    haystack = " ".join(
        [
            str(row.get("provider_id") or ""),
            str(row.get("title") or ""),
            str(row.get("date") or ""),
            str(row.get("source_path") or ""),
            str(row.get("digest_path") or ""),
            " ".join(row.get("reasons") or []),
        ]
    ).lower()
    return query.lower() in haystack


def list_items(
    root: Path,
    import_key: str,
    *,
    bucket: str = "pending",
    query: str = "",
    offset: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    queue, audit = load_import(root, import_key)
    rows = _joined_rows(queue, audit)
    bucket = (
        bucket
        if bucket
        in {"pending", "backfill", "routing-review", "complete", "all"}
        else "pending"
    )
    if bucket == "pending":
        rows = [row for row in rows if row["pending"]]
    elif bucket == "backfill":
        rows = [row for row in rows if row["audited"] and not row["new_schema_complete"]]
    elif bucket == "routing-review":
        # `audited and` mirrors the backfill bucket above. Harmless today — the
        # flag can only be set from an audit record — but the asymmetry is the
        # same shape as the bucket-vs-detail split fixed in this pass, and it
        # costs one token not to leave a second definition of "audited" here.
        rows = [row for row in rows if row["audited"] and row["routing_review_required"]]
    elif bucket == "complete":
        rows = [row for row in rows if row["new_schema_complete"]]
    rows = [row for row in rows if _matches(row, query.strip())]
    total = len(rows)
    offset = max(0, _as_int(offset))
    limit = max(1, min(_as_int(limit, 50), MAX_PAGE_SIZE))
    return {
        "import_key": import_key,
        "bucket": bucket,
        "query": query,
        "offset": offset,
        "limit": limit,
        "total": total,
        "rows": rows[offset : offset + limit],
    }


def item_detail(root: Path, import_key: str, provider_id: str) -> dict[str, Any] | None:
    queue, audit = load_import(root, import_key)
    pid = (provider_id or "").strip()
    queue_row = next(
        (normalize_queue_item(row) for row in _queue_items(queue) if row.get("provider_id") == pid),
        None,
    )
    # Default 0, not 1. A missing or malformed schema_version used to land on
    # the MOST permissive value — the legacy branch that assumes completeness —
    # so a corrupt artifact failed OPEN into "verified". 0 is below every real
    # schema, so an unreadable version now fails closed.
    schema_version = _as_int(audit.get("schema_version"), 0)
    ledger_path = str(audit.get("ledger_path") or DEFAULT_LEDGER_PATH)
    audit_row = next(
        (
            _normalize_audit_record(
                row,
                schema_version=schema_version,
                ledger_path=ledger_path,
            )
            for row in _audit_records(audit)
            if row.get("provider_id") == pid
        ),
        None,
    )
    missing = audit.get("missing_from_export") or []
    if not queue_row and not audit_row and pid not in missing:
        return None
    checks = (audit_row or {}).get("source_checks") or {}
    derived = (audit_row or {}).get("derived_notes") or []
    reciprocal = (audit_row or {}).get("reciprocal_links") or {}
    reasons = (audit_row or {}).get("reasons") or []
    receipts = _operation_receipts(
        audit_row,
        provider_id=pid,
        ledger_path=ledger_path,
        ledger_only=pid in missing,
    )
    # `_operation_receipts` treats a verified-empty export as satisfying the
    # source and digest receipts — there is no body to hash and no digest to
    # write. The stage list re-derived the same judgement from raw checks and
    # did NOT, so a legitimately-verified empty export rendered "4/4 verified"
    # on the receipt panel and "incomplete" in the header at the same time,
    # with four red stages. One fact, consulted once, instead of two answers.
    empty_export = bool((audit_row or {}).get("empty_export_verified"))
    not_required = " (not required — verified empty export)"
    stages = [
        {"id": "identify", "label": "Provider identity", "ok": bool(pid)},
        {
            "id": "capture",
            "label": "Source archive contract" + (not_required if empty_export else ""),
            "ok": empty_export or bool(checks.get("contract_complete")),
        },
        {
            "id": "hash",
            "label": "Body hash readback" + (not_required if empty_export else ""),
            "ok": empty_export or bool(checks.get("hash_verified")),
        },
        {
            "id": "messages",
            "label": "Message-count readback" + (not_required if empty_export else ""),
            "ok": empty_export or bool(checks.get("message_count_verified")),
        },
        {
            "id": "digest",
            "label": "Audited digest" + (not_required if empty_export else ""),
            "ok": empty_export or bool((audit_row or {}).get("digest_path")),
        },
        {
            "id": "coverage",
            "label": "Full-domain coverage",
            "ok": bool((audit_row or {}).get("new_schema_complete")),
        },
        {
            "id": "evidence",
            "label": "Derived routing evidence",
            "ok": _receipt(receipts, "derived")["ok"],
        },
        {
            "id": "ledger",
            "label": "Ledger provider-ID readback",
            "ok": _receipt(receipts, "ledger")["ok"],
        },
    ]
    return {
        "import_key": import_key,
        "provider_id": pid,
        "provider": queue.get("provider") or audit.get("provider") or "",
        "queue": queue_row,
        "audit": audit_row,
        "missing_from_export": pid in missing,
        "stages": stages,
        "receipts": receipts,
        "receipt_complete": all(receipt["ok"] for receipt in receipts),
        "complete": bool((audit_row or {}).get("new_schema_complete"))
        and all(stage["ok"] for stage in stages)
        and all(receipt["ok"] for receipt in receipts),
        "reasons": reasons,
    }


def resume_target(root: Path, import_key: str) -> dict[str, Any]:
    pending = list_items(root, import_key, bucket="pending", limit=1)
    if pending["rows"]:
        row = pending["rows"][0]
        action = "ingest"
    else:
        backfill = list_items(root, import_key, bucket="backfill", limit=1)
        row = backfill["rows"][0] if backfill["rows"] else None
        action = "backfill"
    if not row:
        return {"import_key": import_key, "next": None, "prompt": "", "action": "none"}
    prompt = (
        "Continue eos-ai-conversation-ingest atomically. "
        f"Import: {import_key}. Provider conversation ID: {row['provider_id']}. "
        f"Action: {action}. Verify source archive, audited digest, full-domain scan, "
        "derived-note reciprocity, and ledger before advancing."
    )
    return {"import_key": import_key, "next": row, "prompt": prompt, "action": action}


def parse_mechanism(skill_text: str, routing_text: str) -> dict[str, Any]:
    """Extract the UI contract from canonical Markdown rather than restating it."""
    required_sections: list[str] = []
    in_required = False
    for line in skill_text.splitlines():
        if line.strip() == "Every digest must contain:":
            in_required = True
            continue
        if in_required:
            match = re.match(r"^-\s+`(## [^`]+)`\s*$", line.strip())
            if match:
                required_sections.append(match.group(1))
                continue
            if required_sections and line.strip():
                break

    domains: list[str] = []
    in_domain_table = False
    for line in routing_text.splitlines():
        if line.startswith("| Domain |"):
            in_domain_table = True
            continue
        if in_domain_table:
            if not line.startswith("|"):
                if domains:
                    break
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if not cells or cells[0].startswith("---"):
                continue
            if cells[0]:
                domains.append(cells[0])

    output_paths: list[dict[str, str]] = []
    in_paths = False
    for line in routing_text.splitlines():
        if line.startswith("| Layer | Standard | Sensitive |"):
            in_paths = True
            continue
        if in_paths:
            if not line.startswith("|"):
                if output_paths:
                    break
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if not cells or cells[0].startswith("---") or len(cells) < 3:
                continue
            output_paths.append(
                {"layer": cells[0], "standard": cells[1], "sensitive": cells[2]}
            )

    return {
        "required_sections": required_sections,
        "domains": domains,
        "output_paths": output_paths,
        "completion_gate_present": "## Completion gate" in skill_text,
        "evidence_contract_present": "## Evidence graph contract" in routing_text,
    }
