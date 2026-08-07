#!/usr/bin/env python3
"""Build a resumable semantic-routing review queue from an evidence audit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[4]
if str(_REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO / "scripts"))
from scanner_lib import emit_json  # noqa: E402


ROUTING_REASON_PREFIXES = (
    "delta-",
    "needs-review-unresolved:",
    "derived-skip-reason-missing",
    "derived-notes-frontmatter-missing",
    "domain-coverage-incomplete",
    "missing-domain-coverage",
)


def load_audit(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("audit must be a JSON object")
    if not isinstance(value.get("records"), list):
        raise ValueError("audit records must be an array")
    return value


def routing_reasons(record: dict[str, Any]) -> list[str]:
    return [
        str(reason)
        for reason in record.get("reasons") or []
        if str(reason).startswith(ROUTING_REASON_PREFIXES)
    ]


def review_kind(record: dict[str, Any]) -> str | None:
    reasons = routing_reasons(record)
    if any(
        reason.startswith(
            "delta-digest-contained-routing-evidence-missing:"
        )
        for reason in reasons
    ):
        return "digest-contained-search-review"
    if bool(record.get("routing_review_required")) or reasons:
        return "semantic-routing-review"
    if not bool(record.get("new_schema_complete")):
        return "legacy-evidence-backfill"
    return None


def priority(kind: str) -> int:
    return {
        "digest-contained-search-review": 0,
        "semantic-routing-review": 1,
        "legacy-evidence-backfill": 2,
    }[kind]


def build_queue(audit: dict[str, Any], *, audit_path: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for record in audit["records"]:
        if not isinstance(record, dict):
            continue
        kind = review_kind(record)
        if kind is None:
            continue
        checks = record.get("routing_checks") or {}
        rows.append(
            {
                "provider_id": str(record.get("provider_id") or ""),
                "provider": str(audit.get("provider") or ""),
                "title": str(record.get("title") or "Untitled"),
                "date": str(record.get("date") or ""),
                "review_kind": kind,
                "priority": priority(kind),
                "source_path": record.get("source_path"),
                "digest_path": record.get("digest_path"),
                "derived_count": len(record.get("derived_notes") or []),
                "durable_delta_count": int(
                    checks.get("durable_delta_count") or 0
                ),
                "domain_statuses": record.get("domain_statuses") or {},
                "delta_dispositions": record.get("delta_dispositions") or [],
                "reasons": list(record.get("reasons") or []),
            }
        )
    rows.sort(
        key=lambda row: (
            row["priority"],
            row["date"] or "9999-99-99",
            row["provider_id"],
        )
    )
    kinds: dict[str, int] = {}
    for row in rows:
        kinds[row["review_kind"]] = kinds.get(row["review_kind"], 0) + 1
    return {
        "schema_version": 1,
        "provider": str(audit.get("provider") or ""),
        "source_audit": audit_path,
        "total": len(rows),
        "counts": kinds,
        "next_review": rows[0] if rows else None,
        "items": rows,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    # One envelope on stdout whether this succeeds or not. A bare raise here
    # exits 1 with an empty stdout and a traceback on stderr, so the agent that
    # invoked it cannot read why (.claude/rules/agent-cli.md).
    try:
        return _build(args)
    except (ValueError, KeyError, FileNotFoundError, OSError) as exc:
        return emit_json(False, "invalid_input", f"{type(exc).__name__}: {exc}")


def _build(args: argparse.Namespace) -> int:
    audit = load_audit(args.audit)
    report = build_queue(audit, audit_path=str(args.audit))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary = {key: value for key, value in report.items() if key != "items"}
    return emit_json(True, "ok", f"routing-review queue written to {args.output}", summary)


if __name__ == "__main__":
    raise SystemExit(main())
