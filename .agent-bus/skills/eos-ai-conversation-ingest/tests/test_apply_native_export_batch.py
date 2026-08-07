import hashlib
import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "apply_native_export_batch.py"
SPEC = importlib.util.spec_from_file_location("apply_native_export_batch", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)

PROVIDER_ID = "22222222-2222-4222-8222-222222222222"
SOURCE_PATH = (
    "40_Archive/AI Conversations/originals/chatgpt/"
    "2026-07-28-example--22222222.md"
)
DIGEST_PATH = (
    "40_Archive/AI Conversations/chatgpt/"
    "2026-07-28-example--22222222.md"
)


def reviewed_spec():
    return {
        "provider_id": PROVIDER_ID,
        "title": "Example",
        "conversation_date": "2026-07-28",
        "ingestion_date": "2026-07-28",
        "digested_at": "2026-07-28T12:00:00+10:00",
        "private": True,
        "tags": ["example"],
        "digest": "A reviewed digest.",
        "domain_coverage": {
            "*": {"status": "not-present", "notes": "Not present."},
            "Knowledge fragments": {
                "status": "delta",
                "notes": "One reusable lesson.",
            },
        },
        "decisions": ["Keep the verified lesson."],
        "fact_checks": [
            {
                "claim": "A claim",
                "class": "verified",
                "check": "Checked.",
                "durable_treatment": "Promoted.",
                "source": "Primary",
                "source_url": "https://example.com/source",
            }
        ],
        "derived_mutations": [
            {
                "path": "30_Resources/KB/example.md",
                "mode": "create",
                "change": "Created lesson",
                "claim_class": "verified",
                "content": "example",
            }
        ],
    }


def test_digest_renders_complete_evidence_contract():
    text = MODULE.digest_markdown(
        reviewed_spec(),
        provider="chatgpt",
        provider_id=PROVIDER_ID,
        source_path=SOURCE_PATH,
        digest_path=DIGEST_PATH,
        source_url=f"https://chatgpt.com/c/{PROVIDER_ID}",
        message_count=2,
        capture_fidelity="export-native",
    )
    assert all(heading in text for heading in MODULE.AUDIT.REQUIRED_DIGEST_HEADINGS)
    assert f'source_conversation_id: "{PROVIDER_ID}"' in text
    assert '  - "[[30_Resources/KB/example]]"' in text
    assert "[Primary](https://example.com/source)" in text
    assert "| verified by atomic batch readback |" in text
    assert "| planned |" not in text
    checks = MODULE.AUDIT.digest_checks(text, PROVIDER_ID, SOURCE_PATH)
    assert all(checks.values())


def test_ledger_after_appends_completion_receipt_without_rewriting_legacy_row():
    legacy = f"- 2025-01-01 · chatgpt-old · {PROVIDER_ID} · legacy\n"
    row = f"- 2026-07-28 · chatgpt-native · {PROVIDER_ID} · complete"
    updated, status = MODULE.ledger_after(legacy, PROVIDER_ID, row)
    assert legacy.rstrip() in updated
    assert row in updated
    assert status == "appended-completion-receipt"
    reused, reused_status = MODULE.ledger_after(updated, PROVIDER_ID, row)
    assert reused == updated
    assert reused_status == "reused"


def test_backup_path_is_confined_and_stable():
    first = MODULE.backup_path(DIGEST_PATH, PROVIDER_ID, "2026-07-28")
    second = MODULE.backup_path(DIGEST_PATH, PROVIDER_ID, "2026-07-28")
    assert first == second
    assert first.startswith("99_Attachments/temp-backup/")
    assert first.endswith(".md")


def test_planned_chatgpt_assets_have_deterministic_paths_and_hashes():
    content = b"\x89PNG\r\n\x1a\noriginal"

    class Helper:
        @staticmethod
        def conversation_asset_records(_conversation):
            return [
                {
                    "provider_asset_id": "file-asset",
                    "recoverable_id": True,
                    "name": "formula image.png",
                    "mime_type": "image/png",
                }
            ]

        @staticmethod
        def read_export_asset(_exports, _identifier):
            return content, "export.zip!file-asset.dat"

    plans = MODULE.planned_chatgpt_assets(
        Helper(),
        {"conversation_id": PROVIDER_ID},
        ["export.zip"],
        SOURCE_PATH,
    )
    assert len(plans) == 1
    assert plans[0]["path"].endswith(
        "/assets/22222222/001-formula-image.png"
    )
    assert plans[0]["content"] == content
    assert plans[0]["content_sha256"] == hashlib.sha256(content).hexdigest()


def test_planned_chatgpt_assets_sniffs_wav_payload_hidden_in_dat():
    content = b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt "

    class Helper:
        @staticmethod
        def conversation_asset_records(_conversation):
            return [
                {
                    "provider_asset_id": "file-voice",
                    "recoverable_id": True,
                }
            ]

        @staticmethod
        def read_export_asset(_exports, _identifier):
            return content, "export.zip!file-voice.dat"

    plans = MODULE.planned_chatgpt_assets(
        Helper(),
        {"conversation_id": PROVIDER_ID},
        ["export.zip"],
        SOURCE_PATH,
    )

    assert len(plans) == 1
    assert plans[0]["path"].endswith(
        "/assets/22222222/001-file-voice.wav"
    )
    assert plans[0]["name"] == "file-voice.wav"
    assert plans[0]["mime_type"] == "audio/wav"
    assert plans[0]["content"] == content


def test_source_gap_override_marks_partial_and_rehashes_body():
    body = (
        "# Example\n\n"
        "## Messages\n\n"
        "### 001 · User\n\n"
        "Create an image.\n"
    )
    original_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    rendered = {
        "capture_fidelity": "export-native",
        "content_sha256": original_hash,
        "content": (
            "---\n"
            "capture_fidelity: export-native\n"
            "raw_status: complete\n"
            f"content_sha256: {original_hash}\n"
            "---\n"
            f"{body}"
        ),
    }
    updated = MODULE.apply_source_gap_overrides(
        rendered,
        {
            "force_capture_fidelity": "partial",
            "source_gap_notes": [
                "Two generated images are absent from the native export."
            ],
        },
    )
    assert updated["capture_fidelity"] == "partial"
    assert "capture_fidelity: partial" in updated["content"]
    assert "raw_status: partial" in updated["content"]
    assert "## Unavailable payloads" in updated["content"]
    assert "Two generated images are absent" in updated["content"]
    assert MODULE.AUDIT.body(updated["content"]).startswith("# Example\n")
    assert (
        hashlib.sha256(
            MODULE.AUDIT.body(updated["content"]).encode("utf-8")
        ).hexdigest()
        == updated["content_sha256"]
    )
    assert f"content_sha256: {updated['content_sha256']}" in updated["content"]


def test_source_gap_override_requires_explanation_for_forced_partial():
    try:
        MODULE.apply_source_gap_overrides(
            {"content": "---\n---\n## Messages\n", "capture_fidelity": "export-native"},
            {"force_capture_fidelity": "partial"},
        )
    except ValueError as error:
        assert "requires source_gap_notes" in str(error)
    else:
        raise AssertionError("Expected a reviewed source-gap explanation")


def _verified_complete_asset_source():
    body = (
        "# Example\n\n"
        "## Messages\n\n"
        "### 001 · User\n\n"
        "Hello\n\n"
        "## Archived binary assets\n\n"
        "- [[asset.wav]] · provider asset `file-voice` · `abc` · 44 bytes\n"
    )
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return f"""---
record_kind: conversation-source
archive_schema: eos-ai-conversation-v1
author: both
source_conversation_id: "{PROVIDER_ID}"
source_url: "https://chatgpt.com/c/{PROVIDER_ID}"
capture_fidelity: export-native
raw_status: complete
message_count: 1
asset_count: 1
archived_asset_count: 1
content_sha256: {digest}
---
{body}"""


def test_capture_metadata_repair_only_reverses_false_gap_override():
    corrected = _verified_complete_asset_source()
    old_hash = MODULE.AUDIT.field_value(corrected, "content_sha256")
    partial = MODULE.apply_source_gap_overrides(
        {
            "capture_fidelity": "export-native",
            "content_sha256": old_hash,
            "content": corrected,
        },
        {
            "force_capture_fidelity": "partial",
            "source_gap_notes": ["The referenced WAV was incorrectly reported absent."],
        },
    )["content"]

    repaired = MODULE.repair_source_capture_metadata(
        partial,
        corrected,
        provider_id=PROVIDER_ID,
    )

    assert repaired == corrected
    assert all(MODULE.AUDIT.source_checks(repaired, PROVIDER_ID).values())


def test_capture_metadata_repair_rejects_message_change():
    corrected = _verified_complete_asset_source()
    partial = MODULE.apply_source_gap_overrides(
        {
            "capture_fidelity": "export-native",
            "content_sha256": MODULE.AUDIT.field_value(corrected, "content_sha256"),
            "content": corrected,
        },
        {
            "force_capture_fidelity": "partial",
            "source_gap_notes": ["Incorrect gap."],
        },
    )["content"]
    changed = corrected.replace("Hello", "Different provider evidence")
    try:
        MODULE.repair_source_capture_metadata(
            partial,
            changed,
            provider_id=PROVIDER_ID,
        )
    except RuntimeError as error:
        assert "contract failed" in str(error) or "immutable message" in str(error)
    else:
        raise AssertionError("Expected immutable message-evidence refusal")


def _verified_partial_source(message_count=1):
    body = "# Example\n\n## Messages\n\n### 001 Â· User\n\nHello\n"
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return f"""---
record_kind: conversation-source
archive_schema: eos-ai-conversation-v1
author: both
source_conversation_id: "{PROVIDER_ID}"
source_url: "https://chatgpt.com/c/{PROVIDER_ID}"
capture_fidelity: partial
raw_status: partial
message_count: {message_count}
content_sha256: {digest}
---
{body}"""


def test_reusable_existing_source_requires_verified_contract_and_export_count():
    existing = _verified_partial_source()

    selected = MODULE.reusable_existing_source(
        existing,
        provider_id=PROVIDER_ID,
        expected_message_count=1,
    )

    assert selected["content"] == existing
    assert selected["capture_fidelity"] == "partial"
    assert selected["message_count"] == 1


def test_reusable_existing_source_rejects_export_count_mismatch():
    try:
        MODULE.reusable_existing_source(
            _verified_partial_source(),
            provider_id=PROVIDER_ID,
            expected_message_count=2,
        )
    except RuntimeError as error:
        assert "message-count mismatch" in str(error)
    else:
        raise AssertionError("Expected immutable source count mismatch")


def test_repair_source_digest_link_changes_only_frontmatter():
    existing = _verified_partial_source().replace(
        "raw_status: partial\n",
        "raw_status: partial\nrelated:\n"
        '  - "[[30_Resources/conversations/2026-07-28-example]]"\n',
    )
    before_body = MODULE.AUDIT.body(existing)
    repaired = MODULE.repair_source_digest_link(
        existing,
        provider_id=PROVIDER_ID,
        digest_path=(
            "30_Resources/conversations/"
            "2026-07-28-example--22222222.md"
        ),
    )
    assert "2026-07-28-example--22222222]]" in repaired
    assert MODULE.AUDIT.body(repaired) == before_body
    assert all(MODULE.AUDIT.source_checks(repaired, PROVIDER_ID).values())


def test_repair_source_digest_link_refuses_missing_related_link():
    try:
        MODULE.repair_source_digest_link(
            _verified_partial_source(),
            provider_id=PROVIDER_ID,
            digest_path=(
                "30_Resources/conversations/"
                "2026-07-28-example--22222222.md"
            ),
        )
    except RuntimeError as error:
        assert "no repairable related digest link" in str(error)
    else:
        raise AssertionError("Expected missing related-link refusal")
