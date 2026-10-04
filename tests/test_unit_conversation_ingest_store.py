"""Pure tests for the Conversation Ingest telemetry reader."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from helpers import load_app_module


@pytest.fixture(scope="module")
def store():
    return load_app_module("conversation-ingest", "store")


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _fixture_import(root: Path, *, malformed_audit: bool = False) -> Path:
    folder = root / "sample-export"
    _write_json(
        folder / "coverage-queue.json",
        {
            "schema_version": 1,
            "provider": "provider-a",
            "total_unique": 3,
            "processed": 1,
            "pending": 2,
            "next_pending": {
                "provider_id": "id-1",
                "title": "First",
                "message_count": 3,
                "capture_fidelity": "partial",
                "messages": [{"role": "user", "text": "must not leak"}],
            },
            "items": [
                {
                    "provider_id": "id-1",
                    "title": "First",
                    "created_at": "2024-01-01T00:00:00Z",
                    "message_count": 3,
                    "capture_fidelity": "partial",
                    "messages": [{"role": "user", "text": "must not leak"}],
                },
                {
                    "provider_id": "id-2",
                    "title": "Second",
                    "created_at": "2024-01-02T00:00:00Z",
                    "message_count": 5,
                    "capture_fidelity": "export-native",
                },
            ],
        },
    )
    if malformed_audit:
        (folder / "evidence-audit.json").write_text("{broken", encoding="utf-8")
    else:
        _write_json(
            folder / "evidence-audit.json",
            {
                "schema_version": 3,
                "provider": "provider-a",
                "ledger_path": (
                    "30_Resources/conversations/"
                    "ai-conversation-ingestion-ledger.md"
                ),
                "processed_in_ledger": 2,
                "audited_in_export": 2,
                "new_schema_complete": 1,
                "needs_backfill": 1,
                "living_note_mutations": 1,
                "routing_review_required": 0,
                "accounted_no_mutation": 0,
                "no_durable_delta": 0,
                "missing_from_export": ["id-missing"],
                "records": [
                    {
                        "provider_id": "id-1",
                        "title": "First",
                        "date": "2024-01-01",
                        "source_path": "30_Resources/conversations/originals/provider-a/first.md",
                        "digest_path": "30_Resources/conversations/first.md",
                        "source_checks": {
                            "contract_complete": True,
                            "hash_verified": True,
                            "message_count_verified": True,
                            "provider_id_verified": True,
                        },
                        "digest_checks": {
                            "required_sections_verified": True,
                            "domain_coverage_verified": True,
                            "derived_frontmatter_declared": True,
                            "provider_id_verified": True,
                            "source_link_verified": True,
                        },
                        "derived_notes": ["30_Resources/KB/first"],
                        "derived_skip_reason": "",
                        "reciprocal_links": {"30_Resources/KB/first": True},
                        "ledger_receipt": {
                            "path": (
                                "30_Resources/conversations/"
                                "ai-conversation-ingestion-ledger.md"
                            ),
                            "provider_id_verified": True,
                        },
                        "new_schema_complete": True,
                        "reasons": [],
                    },
                    {
                        "provider_id": "id-legacy",
                        "title": "Legacy",
                        "date": "2023-12-31",
                        "source_path": None,
                        "digest_path": None,
                        "source_checks": {
                            "contract_complete": False,
                            "hash_verified": False,
                            "message_count_verified": False,
                            "provider_id_verified": False,
                        },
                        "digest_checks": {
                            "required_sections_verified": False,
                            "domain_coverage_verified": False,
                            "derived_frontmatter_declared": False,
                            "provider_id_verified": False,
                            "source_link_verified": False,
                        },
                        "derived_notes": [],
                        "derived_skip_reason": "",
                        "reciprocal_links": {},
                        "ledger_receipt": {
                            "path": (
                                "30_Resources/conversations/"
                                "ai-conversation-ingestion-ledger.md"
                            ),
                            "provider_id_verified": True,
                        },
                        "new_schema_complete": False,
                        "reasons": [
                            "source-missing-at-expected-path",
                            "digest-missing-at-expected-path",
                        ],
                    },
                ],
            },
        )
    return folder


def test_discover_missing_root_is_fail_soft(store, tmp_path):
    result = store.discover_imports(tmp_path / "missing")
    assert result["root_ok"] is False
    assert result["imports"] == []


def test_discover_summary_uses_preferred_coverage_queue(store, tmp_path):
    _fixture_import(tmp_path)
    result = store.discover_imports(tmp_path)
    row = result["imports"][0]
    assert row["queue_file"] == "coverage-queue.json"
    assert row["total"] == 3
    assert row["processed"] == 1
    assert row["pending"] == 2


def test_artifact_choice_ignores_mtime(store, tmp_path):
    """Which artifact the UI reads must not depend on filesystem metadata.

    This test previously asserted the opposite — it stamped a newer mtime on
    the lower-priority file and required it to win. That pinned the bug: a
    vault sync, a robocopy, or a restore-from-backup rewrites mtimes without
    changing a byte, and would silently swap the whole UI onto a different
    audit (in the live claude import, a 727-record sv3 one in place of a
    1047-record sv4 one) with no error and no log line.

    Name priority is what the `-current` suffix already encodes, and unlike
    mtime it is reproducible. `generated_at` would be better still, but the
    producer scripts do not emit one.
    """
    folder = _fixture_import(tmp_path)
    ingestion = folder / "ingestion-queue.json"
    _write_json(
        ingestion,
        {
            "schema_version": 1,
            "provider": "provider-a",
            "total_unique": 3,
            "processed": 2,
            "pending": 1,
            "items": [],
        },
    )
    coverage = folder / "coverage-queue.json"
    # coverage-queue ranks ahead of ingestion-queue; make it look much older.
    os.utime(coverage, ns=(1_000_000_000, 1_000_000_000))
    os.utime(ingestion, ns=(2_000_000_000, 2_000_000_000))

    row = store.discover_imports(tmp_path)["imports"][0]
    assert row["queue_file"] == "coverage-queue.json"
    assert row["processed"] == 1
    assert row["pending"] == 2

    # Flipping the mtimes back must not change the answer.
    os.utime(coverage, ns=(3_000_000_000, 3_000_000_000))
    store._JSON_CACHE.clear()
    assert store.discover_imports(tmp_path)["imports"][0]["queue_file"] == (
        "coverage-queue.json"
    )


def test_discover_supports_current_named_artifacts(store, tmp_path):
    folder = _fixture_import(tmp_path)
    current_queue = folder / "ingestion-queue-current.json"
    current_audit = folder / "evidence-audit-current.json"
    _write_json(
        current_queue,
        {
            "schema_version": 1,
            "provider": "provider-a",
            "total_unique": 4,
            "processed": 3,
            "pending": 1,
            "items": [],
        },
    )
    _write_json(
        current_audit,
        {
            "schema_version": 4,
            "provider": "provider-a",
            "new_schema_complete": 2,
            "needs_backfill": 1,
            "living_note_mutations": 1,
            "routing_review_required": 1,
            "records": [],
        },
    )

    row = store.discover_imports(tmp_path)["imports"][0]

    assert row["queue_file"] == "ingestion-queue-current.json"
    assert row["audit_file"] == "evidence-audit-current.json"
    assert row["routing_review_required"] == 1


def test_discover_counts_partial_and_backfill(store, tmp_path):
    _fixture_import(tmp_path)
    row = store.discover_imports(tmp_path)["imports"][0]
    assert row["partial"] == 1
    assert row["new_schema_complete"] == 1
    assert row["needs_backfill"] == 1
    assert row["missing_from_export"] == 1
    assert row["living_note_mutations"] == 1
    assert row["routing_review_required"] == 0


def test_malformed_artifact_degrades_only_its_import(store, tmp_path):
    _fixture_import(tmp_path, malformed_audit=True)
    other = tmp_path / "other-export"
    _write_json(
        other / "coverage-queue.json",
        {"provider": "provider-b", "total_unique": 0, "items": []},
    )
    rows = {row["key"]: row for row in store.discover_imports(tmp_path)["imports"]}
    assert rows["sample-export"]["status"] == "degraded"
    assert rows["other-export"]["status"] == "queued"


def test_pending_list_is_paginated(store, tmp_path):
    _fixture_import(tmp_path)
    result = store.list_items(
        tmp_path, "sample-export", bucket="pending", offset=1, limit=1
    )
    assert result["total"] == 2
    assert len(result["rows"]) == 1
    assert result["rows"][0]["provider_id"] == "id-2"


def test_pending_api_shape_cannot_leak_message_bodies(store, tmp_path):
    _fixture_import(tmp_path)
    row = store.list_items(tmp_path, "sample-export", bucket="pending")["rows"][0]
    assert "messages" not in row
    assert "content" not in row
    assert "conversation" not in row
    next_item = store.discover_imports(tmp_path)["imports"][0]["next_pending"]
    assert "messages" not in next_item


def test_search_matches_provider_id_title_path_and_reason(store, tmp_path):
    _fixture_import(tmp_path)
    by_title = store.list_items(
        tmp_path, "sample-export", bucket="all", query="Second"
    )
    by_id = store.list_items(
        tmp_path, "sample-export", bucket="all", query="id-legacy"
    )
    by_reason = store.list_items(
        tmp_path, "sample-export", bucket="all", query="digest-missing"
    )
    assert [row["provider_id"] for row in by_title["rows"]] == ["id-2"]
    assert [row["provider_id"] for row in by_id["rows"]] == ["id-legacy"]
    assert [row["provider_id"] for row in by_reason["rows"]] == ["id-legacy"]


def test_backfill_excludes_complete_records(store, tmp_path):
    _fixture_import(tmp_path)
    result = store.list_items(tmp_path, "sample-export", bucket="backfill")
    assert [row["provider_id"] for row in result["rows"]] == ["id-legacy"]


def test_detail_joins_queue_and_audit_by_provider_id(store, tmp_path):
    _fixture_import(tmp_path)
    detail = store.item_detail(tmp_path, "sample-export", "id-1")
    assert detail["queue"]["title"] == "First"
    assert detail["audit"]["digest_path"].endswith("first.md")
    assert detail["audit"]["reciprocal_links"]["30_Resources/KB/first"] is True
    assert detail["complete"] is True


def test_detail_surfaces_missing_export_identity(store, tmp_path):
    _fixture_import(tmp_path)
    detail = store.item_detail(tmp_path, "sample-export", "id-missing")
    assert detail["missing_from_export"] is True
    assert detail["complete"] is False


def test_four_layer_receipt_surfaces_no_durable_delta(store):
    receipts = store._operation_receipts(
        {
            "audit_schema_version": 3,
            "source_path": "30_Resources/conversations/originals/provider-a/item.md",
            "source_checks": {
                "contract_complete": True,
                "hash_verified": True,
                "message_count_verified": True,
                "provider_id_verified": True,
            },
            "digest_path": "30_Resources/conversations/item.md",
            "digest_checks": {
                "required_sections_verified": True,
                "domain_coverage_verified": True,
                "derived_frontmatter_declared": True,
                "provider_id_verified": True,
                "source_link_verified": True,
            },
            "derived_notes": [],
            "no_mutation_reason": "The domain scan found no durable delta.",
            "delta_dispositions": [],
            "routing_checks": {
                "durable_delta_count": 0,
                "needs_review_count": 0,
                "delta_dispositions_required": False,
                "delta_dispositions_verified": True,
                "needs_review_resolved": True,
            },
            "reciprocal_links": {},
            "ledger_receipt": {
                "path": store.DEFAULT_LEDGER_PATH,
                "provider_id_verified": True,
            },
        },
        provider_id="id-skipped",
    )
    assert [receipt["id"] for receipt in receipts] == [
        "source",
        "digest",
        "derived",
        "ledger",
    ]
    assert all(receipt["ok"] for receipt in receipts)
    assert receipts[2]["outcome"] == "no-durable-delta"
    assert receipts[2]["display_outcome"] == "no durable living-note delta"
    assert "no durable delta" in receipts[2]["detail"]


def test_four_layer_receipt_accepts_verified_empty_export(store):
    receipts = store._operation_receipts(
        {
            "audit_schema_version": 3,
            "empty_export_verified": True,
            "source_path": None,
            "digest_path": None,
            "source_checks": {},
            "digest_checks": {},
            "derived_notes": [],
            "no_mutation_reason": (
                "Provider export contains zero messages, attachments, and files."
            ),
            "routing_checks": {
                "durable_delta_count": 0,
                "needs_review_count": 0,
                "delta_dispositions_required": False,
                "delta_dispositions_verified": True,
                "needs_review_resolved": True,
            },
            "ledger_receipt": {
                "path": store.DEFAULT_LEDGER_PATH,
                "provider_id_verified": True,
                "empty_or_transient_verified": True,
            },
        },
        provider_id="id-empty",
    )

    assert all(receipt["ok"] for receipt in receipts)
    assert receipts[0]["outcome"] == "not-required-empty"
    assert receipts[1]["outcome"] == "not-required-empty"
    assert receipts[2]["outcome"] == "no-durable-delta"
    assert receipts[3]["outcome"] == "verified"


def test_four_layer_receipt_accounts_for_delta_without_false_mutation(store):
    receipts = store._operation_receipts(
        {
            "audit_schema_version": 3,
            "source_path": "30_Resources/conversations/originals/provider-a/item.md",
            "source_checks": {
                "contract_complete": True,
                "hash_verified": True,
                "message_count_verified": True,
                "provider_id_verified": True,
            },
            "digest_path": "30_Resources/conversations/item.md",
            "digest_checks": {
                "required_sections_verified": True,
                "domain_coverage_verified": True,
                "derived_frontmatter_declared": True,
                "provider_id_verified": True,
                "source_link_verified": True,
            },
            "derived_notes": [],
            "no_mutation_reason": "Contained in the audited digest.",
            "delta_dispositions": [
                {
                    "domain": "Knowledge fragments",
                    "disposition": "digest-contained",
                    "target": "This digest",
                    "reason": "Small verified distinction.",
                }
            ],
            "routing_checks": {
                "durable_delta_count": 1,
                "needs_review_count": 0,
                "delta_dispositions_required": True,
                "delta_dispositions_verified": True,
                "needs_review_resolved": True,
            },
            "reciprocal_links": {},
            "ledger_receipt": {
                "path": store.DEFAULT_LEDGER_PATH,
                "provider_id_verified": True,
            },
        },
        provider_id="id-contained",
    )
    assert all(receipt["ok"] for receipt in receipts)
    assert receipts[2]["outcome"] == "accounted-no-mutation"
    assert receipts[2]["display_outcome"] == "durable delta retained in digest"
    assert receipts[2]["dispositions"][0]["domain"] == "Knowledge fragments"


def test_four_layer_receipt_rejects_unreviewed_digest_contained(store):
    receipts = store._operation_receipts(
        {
            "audit_schema_version": 4,
            "derived_notes": [],
            "routing_review_required": True,
            "routing_checks": {
                "durable_delta_count": 1,
                "routing_review_required": True,
            },
        },
        provider_id="id-routing-review",
    )

    assert receipts[2]["ok"] is False
    assert receipts[2]["outcome"] == "routing-review"
    assert receipts[2]["display_outcome"] == (
        "semantic routing review required"
    )


def test_list_items_has_dedicated_routing_review_bucket(store, tmp_path):
    folder = _fixture_import(tmp_path)
    audit_path = folder / "evidence-audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["records"][1]["routing_review_required"] = True
    audit["records"][1]["routing_checks"] = {
        "durable_delta_count": 1,
        "routing_review_required": True,
    }
    _write_json(audit_path, audit)

    result = store.list_items(
        tmp_path, "sample-export", bucket="routing-review"
    )

    assert result["total"] == 1
    assert result["rows"][0]["provider_id"] == "id-legacy"


def test_pre_v3_generic_skip_is_not_accepted_as_complete(store):
    receipts = store._operation_receipts(
        {
            "audit_schema_version": 2,
            "derived_notes": [],
            "derived_skip_reason": "No living note updated.",
        },
        provider_id="id-legacy-skip",
    )
    assert receipts[2]["ok"] is False
    assert receipts[2]["outcome"] == "legacy-unverified"


def test_import_key_rejects_traversal(store, tmp_path):
    _fixture_import(tmp_path)
    with pytest.raises(store.ImportPathError):
        store.resolve_import(tmp_path, "../sample-export")
    with pytest.raises(store.ImportPathError):
        store.resolve_import(tmp_path, "nested/path")


def test_page_size_is_capped(store, tmp_path):
    _fixture_import(tmp_path)
    result = store.list_items(tmp_path, "sample-export", bucket="all", limit=999)
    assert result["limit"] == store.MAX_PAGE_SIZE


def test_resume_prefers_pending_then_backfill(store, tmp_path):
    _fixture_import(tmp_path)
    result = store.resume_target(tmp_path, "sample-export")
    assert result["action"] == "ingest"
    assert result["next"]["provider_id"] == "id-1"
    assert "Provider conversation ID: id-1" in result["prompt"]


def test_mechanism_is_derived_from_markdown_contract(store):
    skill = """
Every digest must contain:

- `## Digest`
- `## Domain coverage`
- `## Source`

More prose.

## Completion gate
"""
    routing = """
| Domain | Typical durable content | Default destination |
|---|---|---|
| Personal chronology | Event | Journal |
| Work and career | Role | Area |

| Layer | Standard | Sensitive |
|---|---|---|
| Immutable source | standard-source | private-source |
| Audited digest | standard-digest | private-digest |

## Evidence graph contract
"""
    parsed = store.parse_mechanism(skill, routing)
    assert parsed["required_sections"] == [
        "## Digest",
        "## Domain coverage",
        "## Source",
    ]
    assert parsed["domains"] == ["Personal chronology", "Work and career"]
    assert len(parsed["output_paths"]) == 2
    assert parsed["completion_gate_present"] is True
    assert parsed["evidence_contract_present"] is True


def test_v1_digest_is_legacy_assumed_not_verified(store):
    """A pre-readback record must not be handed a receipt nobody earned.

    Schema v1 predates the digest readback checks, so the auditor never ran
    them. The old code ORed `schema_version < 2 and new_schema_complete` into
    all five digest_checks, which made `digest_ok` trivially true and printed
    "...verified by Vault readback" for a readback that never happened — in the
    one feature whose whole premise is that every claim carries a receipt.

    No existing test caught this: every `_operation_receipts` case used
    audit_schema_version 2/3/4, so the legacy branch had zero coverage.
    """
    normalized = store._normalize_audit_record(
        {
            "provider_id": "id-legacy",
            "new_schema_complete": True,
            "digest_path": "30_Resources/conversations/legacy.md",
            "source_path": "30_Resources/conversations/originals/legacy.md",
            "source_checks": {
                "contract_complete": True,
                "hash_verified": True,
                "message_count_verified": True,
                "provider_id_verified": True,
            },
            "ledger_receipt": {"path": store.DEFAULT_LEDGER_PATH},
        },
        schema_version=1,
    )

    # The checks stay honest — nothing was verified, so nothing reads verified.
    assert normalized["digest_legacy_assumed"] is True
    assert not any(normalized["digest_checks"].values()), (
        "digest_checks were synthesized for a record that was never checked"
    )

    receipts = store._operation_receipts(normalized, provider_id="id-legacy")
    digest = store._receipt(receipts, "digest")

    assert digest["outcome"] == "legacy-assumed"
    assert digest["ok"] is True, "a v1 record is not a failure — nothing went wrong"
    assert digest["verified"] is False, "...but nothing was verified either"
    assert "verified by Vault readback" not in digest["detail"]
    assert "re-audit" in digest["detail"].lower()


def test_v1_digest_does_not_count_toward_the_verified_tally(store, tmp_path):
    """The list badge reads N/4 verified — a legacy-assumed digest must not be N.

    Counting `ok` instead of `verified` puts the same unearned claim back one
    level up, where it is even harder to notice than on the receipt card.
    """
    receipts = store._operation_receipts(
        store._normalize_audit_record(
            {
                "provider_id": "id-legacy",
                "new_schema_complete": True,
                "digest_path": "30_Resources/conversations/legacy.md",
                "ledger_receipt": {"path": store.DEFAULT_LEDGER_PATH},
            },
            schema_version=1,
        ),
        provider_id="id-legacy",
    )
    tally = sum(1 for r in receipts if r.get("verified", r["ok"]))
    assert store._receipt(receipts, "digest")["ok"] is True
    assert tally < sum(1 for r in receipts if r["ok"]), (
        "the digest counted as verified despite never being read back"
    )


def test_receipts_are_addressable_by_id_not_position(store):
    """`display_outcome` exists only on `derived`, so a wrong index is a
    KeyError in a UI aggregation path rather than a test failure."""
    receipts = store._operation_receipts(None, provider_id="id-x")
    assert [r["id"] for r in receipts] == ["source", "digest", "derived", "ledger"]
    assert store._receipt(receipts, "derived") is receipts[2]
    with pytest.raises(KeyError):
        store._receipt(receipts, "nonexistent")


def test_verified_empty_export_is_not_simultaneously_complete_and_incomplete(store, tmp_path):
    """A verified-empty export must not read "4/4 verified" AND "incomplete".

    `_operation_receipts` special-cases empty_export_verified — there is no body
    to hash and no digest to write — but the stage list re-derived the same
    judgement from raw checks and did not. The detail page therefore rendered a
    complete receipt panel above an incomplete header with four red stages.
    Three records in the live claude import hit this.

    The bug is one fact answered twice; the fix is to answer it once, so this
    test asserts the two surfaces agree rather than asserting either value.
    """
    folder = tmp_path / "empty-export"
    ledger = "30_Resources/conversations/ai-conversation-ingestion-ledger.md"
    _write_json(
        folder / "coverage-queue.json",
        {"schema_version": 1, "provider": "provider-a", "items": [], "total_unique": 1},
    )
    _write_json(
        folder / "evidence-audit.json",
        {
            "schema_version": 4,
            "provider": "provider-a",
            "ledger_path": ledger,
            "new_schema_complete": 1,
            "records": [
                {
                    "provider_id": "id-empty",
                    "title": "Nothing was said",
                    "new_schema_complete": True,
                    "empty_export_verified": True,
                    "source_path": None,
                    "digest_path": None,
                    "source_checks": {},
                    "digest_checks": {},
                    "derived_notes": [],
                    "routing_checks": {
                        "durable_delta_count": 0,
                        "needs_review_count": 0,
                        "delta_dispositions_required": False,
                        "delta_dispositions_verified": True,
                        "needs_review_resolved": True,
                    },
                    "ledger_receipt": {"path": ledger, "provider_id_verified": True},
                }
            ],
        },
    )

    detail = store.item_detail(tmp_path, "empty-export", "id-empty")
    assert detail is not None
    assert detail["receipt_complete"] is True
    assert detail["complete"] is True, (
        "receipts say every layer is accounted for but the stage list says "
        "incomplete — the two surfaces disagree about the same record"
    )
    assert all(stage["ok"] for stage in detail["stages"])
    assert any("not required" in stage["label"] for stage in detail["stages"]), (
        "the stages pass silently, so the reader cannot tell why"
    )


def test_discover_names_the_artifacts_it_did_not_read(store, tmp_path):
    """Deterministic selection is not the same as correct selection.

    If a producer writes a stale `-current`, the fresher sibling loses without
    a word. Listing the shadowed files is the only thing that makes which-file-
    won inspectable — silence was the real cost of the old mtime ranking.
    """
    folder = _fixture_import(tmp_path)
    _write_json(
        folder / "ingestion-queue-current.json",
        {"schema_version": 1, "provider": "provider-a", "items": [], "total_unique": 1},
    )
    row = store.discover_imports(tmp_path)["imports"][0]
    assert row["queue_file"] == "ingestion-queue-current.json"
    assert "coverage-queue.json" in row["shadowed_files"]
