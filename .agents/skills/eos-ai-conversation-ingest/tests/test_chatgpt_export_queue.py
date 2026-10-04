import hashlib
import importlib.util
import json
import re
import zipfile
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "chatgpt_export_queue.py"
SPEC = importlib.util.spec_from_file_location("chatgpt_export_queue", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)

ID_DONE = "11111111-1111-4111-8111-111111111111"
ID_NEXT = "22222222-2222-4222-8222-222222222222"


def conversation(provider_id, *, attachment=False, alternate=False):
    mapping = {
        "root": {"id": "root", "message": None, "parent": None},
        "u1": {
            "id": "u1",
            "parent": "root",
            "message": {
                "id": "m1",
                "author": {"role": "user", "name": None},
                "create_time": 1784900000,
                "content": {
                    "content_type": "multimodal_text" if attachment else "text",
                    "parts": (
                        [
                            "Hello",
                            {
                                "content_type": "image_asset_pointer",
                                "asset_pointer": "file-service://file-1",
                                "size_bytes": 12,
                            },
                        ]
                        if attachment
                        else ["Hello"]
                    ),
                },
                "metadata": {
                    "attachments": (
                        [
                            {
                                "id": "file-1",
                                "name": "diagram.png",
                                "mime_type": "image/png",
                                "size": 12,
                            }
                        ]
                        if attachment
                        else []
                    )
                },
            },
        },
        "a1": {
            "id": "a1",
            "parent": "u1",
            "message": {
                "id": "m2",
                "author": {"role": "assistant", "name": None},
                "create_time": 1784900010,
                "content": {"content_type": "text", "parts": ["Hi there"]},
                "metadata": {
                    "content_references": [
                        {
                            "title": "Primary source",
                            "url": "https://example.com/source",
                            "snippet": "Evidence",
                        }
                    ]
                },
            },
        },
    }
    if alternate:
        mapping["a2"] = {
            "id": "a2",
            "parent": "u1",
            "message": {
                "id": "m3",
                "author": {"role": "assistant", "name": None},
                "create_time": 1784900020,
                "content": {"content_type": "text", "parts": ["Alternate answer"]},
                "metadata": {},
            },
        }
    return {
        "conversation_id": provider_id,
        "id": provider_id,
        "title": "A useful chat",
        "create_time": 1784900000,
        "update_time": 1784900020,
        "current_node": "a1",
        "mapping": mapping,
        "is_archived": False,
        "is_starred": True,
    }


def write_export(path, shards):
    with zipfile.ZipFile(path, "w") as archive:
        for name, rows in shards.items():
            archive.writestr(name, json.dumps(rows))
        archive.writestr("user.json", "{}")


def test_build_queue_reads_all_zip_shards_and_reconciles_ledger(tmp_path):
    export = tmp_path / "export.zip"
    write_export(
        export,
        {
            "conversations-000.json": [conversation(ID_DONE)],
            "conversations-001.json": [conversation(ID_NEXT)],
        },
    )
    queue = MODULE.build_queue([export], {ID_DONE})
    assert queue["total_unique"] == 2
    assert queue["processed"] == 1
    assert queue["pending"] == 1
    assert queue["next_pending"]["provider_id"] == ID_NEXT
    assert queue["next_pending"]["exact_content_duplicate_of"] == ID_DONE
    assert queue["exact_content_duplicate_count"] == 1
    assert queue["exact_content_duplicate_groups"] == [
        {
            "content_fingerprint_sha256": (
                queue["next_pending"]["content_fingerprint_sha256"]
            ),
            "canonical_provider_id": ID_DONE,
            "provider_ids": [ID_DONE, ID_NEXT],
        }
    ]


def test_queue_deduplicates_ids_across_multiple_exports(tmp_path):
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    write_export(first, {"conversations-000.json": [conversation(ID_NEXT)]})
    write_export(second, {"conversations-000.json": [conversation(ID_NEXT)]})
    queue = MODULE.build_queue([first, second], set())
    assert queue["total_unique"] == 1
    assert queue["duplicate_ids"] == [ID_NEXT]


def test_exact_content_duplicate_prefers_processed_canonical(tmp_path):
    export = tmp_path / "export.zip"
    write_export(
        export,
        {
            "conversations-000.json": [
                conversation(ID_NEXT),
                conversation(ID_DONE),
            ]
        },
    )
    queue = MODULE.build_queue([export], {ID_DONE})
    assert queue["items"][0]["provider_id"] == ID_NEXT
    assert queue["items"][0]["exact_content_duplicate_of"] == ID_DONE
    assert queue["exact_content_duplicate_groups"][0]["canonical_provider_id"] == (
        ID_DONE
    )


def test_render_preserves_selected_and_alternate_branches_and_hash():
    rendered = MODULE.render_source(
        conversation(ID_NEXT, alternate=True),
        "2026-07-28T12:00:00+10:00",
    )
    assert rendered["message_count"] == 3
    assert rendered["selected_message_count"] == 2
    assert rendered["alternate_message_count"] == 1
    assert "Branch: `selected`" in rendered["content"]
    assert "Branch: `alternate`" in rendered["content"]
    assert "Alternate answer" in rendered["content"]
    assert "provider: chatgpt" in rendered["content"]
    assert f'source_conversation_id: "{ID_NEXT}"' in rendered["content"]
    body = rendered["content"].split("---", 2)[2].lstrip("\n")
    assert hashlib.sha256(body.encode("utf-8")).hexdigest() == (
        rendered["content_sha256"]
    )
    assert len(re.findall(r"^### \d{3} ", body, re.MULTILINE)) == 3


def test_render_marks_unstored_binary_assets_partial_and_private():
    rendered = MODULE.render_source(
        conversation(ID_NEXT, attachment=True),
        "2026-07-28T12:00:00+10:00",
        force_private=True,
    )
    assert rendered["capture_fidelity"] == "partial"
    assert rendered["path"].startswith(
        "40_Archive/AI Conversations/originals/chatgpt/"
    )
    assert rendered["digest_path"].endswith(
        "2026-07-24-a-useful-chat--22222222.md"
    )
    assert "no matching binary payload was recovered" in rendered["content"]
    assert "capture remains partial" in rendered["content"]
    assert "  - private" in rendered["content"]
    assert "raw_status: partial" in rendered["content"]


def test_render_marks_recovered_binary_asset_complete_and_links_original():
    asset = {
        "provider_asset_id": "file-1",
        "path": (
            "40_Archive/AI Conversations/originals/chatgpt/"
            "assets/22222222/001-diagram.png"
        ),
        "content_sha256": "a" * 64,
        "size": 12,
    }
    rendered = MODULE.render_source(
        conversation(ID_NEXT, attachment=True),
        "2026-07-28T12:00:00+10:00",
        force_private=True,
        archived_assets=[asset],
    )
    assert rendered["capture_fidelity"] == "export-native"
    assert "raw_status: complete" in rendered["content"]
    assert "archived_asset_count: 1" in rendered["content"]
    assert "## Archived binary assets" in rendered["content"]
    assert "provider-export binary was copied to Vault" in rendered["content"]
    assert asset["path"] in rendered["content"]


def test_native_export_asset_is_recovered_byte_for_byte(tmp_path):
    export = tmp_path / "export.zip"
    content = b"\x89PNG\r\n\x1a\nasset"
    write_export(
        export,
        {"conversations-000.json": [conversation(ID_NEXT, attachment=True)]},
    )
    with zipfile.ZipFile(export, "a") as archive:
        archive.writestr("file-1.dat", content)
    record = MODULE.conversation_asset_records(
        conversation(ID_NEXT, attachment=True)
    )
    assert [row["provider_asset_id"] for row in record] == ["file-1"]
    recovered = MODULE.read_export_asset([export], "file-1")
    assert recovered is not None
    assert recovered[0] == content
    assert recovered[1].endswith("!file-1.dat")


def test_render_keeps_direct_exported_references():
    rendered = MODULE.render_source(
        conversation(ID_NEXT),
        "2026-07-28T12:00:00+10:00",
    )
    assert "[Primary source](https://example.com/source)" in rendered["content"]
    assert "Evidence" in rendered["content"]
