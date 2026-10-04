import importlib.util
import hashlib
import json
import re
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "claude_export_queue.py"
SPEC = importlib.util.spec_from_file_location("claude_export_queue", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)

ID_DONE = "11111111-1111-4111-8111-111111111111"
ID_NEXT = "22222222-2222-4222-8222-222222222222"


def conversation(provider_id, *, files=None, attachments=None):
    return {
        "uuid": provider_id,
        "name": "A useful chat",
        "created_at": "2026-07-24T01:02:03Z",
        "updated_at": "2026-07-24T02:03:04Z",
        "chat_messages": [
            {
                "sender": "human",
                "created_at": "2026-07-24T01:02:03Z",
                "text": "Hello",
                "attachments": attachments or [],
                "files": files or [],
            },
            {
                "sender": "assistant",
                "created_at": "2026-07-24T01:03:03Z",
                "text": "Hi there",
                "attachments": [],
                "files": [],
            },
        ],
    }


def test_iter_json_array_streams_across_small_chunks(tmp_path):
    path = tmp_path / "conversations.json"
    expected = [conversation(ID_DONE), conversation(ID_NEXT)]
    path.write_text(json.dumps(expected), encoding="utf-8")
    assert list(MODULE.iter_json_array(path, chunk_size=17)) == expected


def test_processed_ids_only_uses_row_leading_id():
    ledger = (
        f"- {ID_DONE} — complete; next: {ID_NEXT}\n"
        f"Context only mentions {ID_NEXT} again.\n"
    )
    assert MODULE.processed_ids_from_ledger(ledger) == {ID_DONE}


def test_processed_ids_can_be_scoped_to_provider():
    claude_id = ID_DONE
    chatgpt_id = ID_NEXT
    ledger = (
        f"- 2026-07-24 | claude | {claude_id} | complete\n"
        f"- 2026-07-24 · chatgpt-older-gap · {chatgpt_id} · complete\n"
    )
    assert MODULE.processed_ids_from_ledger(ledger, provider="claude") == {
        claude_id
    }
    assert MODULE.processed_ids_from_ledger(ledger, provider="chatgpt") == {
        chatgpt_id
    }


def test_processed_ids_accept_chatgpt_and_gemini_provider_id_shapes():
    chatgpt_id = "6a621e60-fe00-83ec-a102-cc2f6c1dc027"
    gemini_id = "55268deaa7820340"
    ledger = (
        f"- 2026-07-24 | chatgpt | {chatgpt_id} | complete\n"
        f"- 2026-07-27 | gemini-older | {gemini_id} | complete\n"
    )

    assert MODULE.processed_ids_from_ledger(ledger) == {
        chatgpt_id,
        gemini_id,
    }


def test_build_queue_reconciles_and_preserves_export_order(tmp_path):
    path = tmp_path / "conversations.json"
    path.write_text(
        json.dumps([conversation(ID_DONE), conversation(ID_NEXT)]), encoding="utf-8"
    )
    queue = MODULE.build_queue([path], {ID_DONE})
    assert queue["total_unique"] == 2
    assert queue["processed"] == 1
    assert queue["pending"] == 1
    assert queue["next_pending"]["provider_id"] == ID_NEXT


def test_local_vault_read_is_utf8_and_confined_to_root(tmp_path):
    vault = tmp_path / "vault"
    note = vault / "30_Resources" / "conversations" / "ledger.md"
    note.parent.mkdir(parents=True)
    note.write_bytes("证据\r\n".encode("utf-8"))
    assert (
        MODULE.local_vault_read(
            vault, "30_Resources/conversations/ledger.md"
        )
        == "证据\n"
    )


def test_local_vault_read_rejects_path_escape(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="escapes root"):
        MODULE.local_vault_read(tmp_path / "vault", "../outside.md")


def test_local_vault_read_normalizes_legacy_crcrlf(tmp_path):
    vault = tmp_path / "vault"
    note = vault / "legacy.md"
    vault.mkdir()
    note.write_bytes(b"one\r\r\ntwo\r\n")

    assert MODULE.local_vault_read(vault, "legacy.md") == "one\n\ntwo\n"


def test_render_source_preserves_attachment_text_and_hash():
    chat = conversation(
        ID_NEXT,
        attachments=[
            {
                "file_name": "evidence.txt",
                "file_type": "text/plain",
                "file_size": 12,
                "extracted_content": "exact evidence",
            }
        ],
    )
    rendered = MODULE.render_source(chat, "2026-07-25T12:00:00+10:00")
    assert rendered["capture_fidelity"] == "export-native"
    assert "### 001 · User · 2026-07-24T01:02:03Z" in rendered["content"]
    assert "exact evidence" in rendered["content"]
    assert rendered["content_sha256"] in rendered["content"]
    assert "record_kind: conversation-source" in rendered["content"]
    assert "archive_schema: eos-ai-conversation-v1" in rendered["content"]
    assert f'source_conversation_id: "{ID_NEXT}"' in rendered["content"]
    assert "raw_status: complete" in rendered["content"]
    assert (
        '  - "[[30_Resources/conversations/2026-07-24-a-useful-chat--22222222]]"'
        in rendered["content"]
    )
    assert rendered["digest_path"] == (
        "30_Resources/conversations/2026-07-24-a-useful-chat--22222222.md"
    )
    body = rendered["content"].split("---", 2)[2].lstrip("\n")
    assert body.startswith("# A useful chat\n")
    assert hashlib.sha256(body.encode("utf-8")).hexdigest() == rendered["content_sha256"]
    assert len(re.findall(r"^### \d{3} ", body, re.MULTILINE)) == 2


def test_render_source_normalizes_provider_newlines_before_hashing():
    chat = conversation(
        ID_NEXT,
        attachments=[
            {
                "file_name": "mixed.txt",
                "file_type": "text/plain",
                "extracted_content": "alpha\r\nbeta\rgamma",
            }
        ],
    )
    chat["chat_messages"][0]["text"] = "one\r\ntwo\rthree"
    rendered = MODULE.render_source(chat, "2026-07-25T12:00:00+10:00")
    body = rendered["content"].split("---", 2)[2].lstrip("\n")
    assert "\r" not in body
    assert "one\ntwo\nthree" in body
    assert "alpha\nbeta\ngamma" in body
    assert hashlib.sha256(body.encode("utf-8")).hexdigest() == (
        rendered["content_sha256"]
    )


def test_render_source_preserves_generated_artifact_tool_input():
    chat = conversation(ID_NEXT)
    artifact = "const label = `goal`;\nconsole.log(label);"
    chat["chat_messages"][1]["text"] = (
        "Viewing artifacts created via the Analysis Tool web feature preview "
        "isn’t yet supported on mobile."
    )
    chat["chat_messages"][1]["content"] = [
        {"type": "text", "text": "I'll create it."},
        {
            "type": "tool_use",
            "name": "artifacts",
            "input": {
                "command": "create",
                "id": "goal-tracker",
                "title": "Goal Tracker",
                "type": "text/html",
                "version_uuid": "version-123",
                "content": artifact,
            },
        },
    ]

    rendered = MODULE.render_source(chat, "2026-07-25T12:00:00+10:00")
    body = rendered["content"].split("---", 2)[2].lstrip("\n")

    assert "#### Generated artifact 1: Goal Tracker" in body
    assert "- Command: `create`" in body
    assert "- Artifact ID: `goal-tracker`" in body
    assert "- Type: `text/html`" in body
    assert "- Version UUID: `version-123`" in body
    assert hashlib.sha256(artifact.encode("utf-8")).hexdigest() in body
    assert artifact in body
    assert hashlib.sha256(body.encode("utf-8")).hexdigest() == (
        rendered["content_sha256"]
    )


def test_render_source_preserves_artifact_update_repl_and_search_traces():
    chat = conversation(ID_NEXT)
    chat["chat_messages"][1]["text"] = "Unsupported tool previews."
    chat["chat_messages"][1]["content"] = [
        {
            "type": "tool_use",
            "name": "artifacts",
            "input": {
                "command": "update",
                "id": "info-log",
                "old_str": "old probability",
                "new_str": "corrected probability",
                "version_uuid": "version-456",
            },
        },
        {
            "type": "tool_result",
            "name": "artifacts",
            "content": [{"type": "text", "text": "OK"}],
            "is_error": False,
        },
        {
            "type": "tool_use",
            "name": "repl",
            "input": {"code": "const risk = 0.03;\nconsole.log(risk);"},
        },
        {
            "type": "tool_result",
            "name": "repl",
            "content": [{"type": "text", "text": "0.03"}],
            "is_error": False,
        },
        {
            "type": "tool_use",
            "name": "web_search",
            "input": {"query": "official visa bulletin"},
        },
        {
            "type": "tool_result",
            "name": "web_search",
            "content": [
                {"type": "knowledge", "title": "Visa Bulletin", "url": "https://example.test"}
            ],
            "is_error": False,
        },
    ]

    rendered = MODULE.render_source(chat, "2026-07-25T12:00:00+10:00")
    body = rendered["content"].split("---", 2)[2].lstrip("\n")

    assert "#### Artifact operation 1: artifacts" in body
    assert '"old_str": "old probability"' in body
    assert '"new_str": "corrected probability"' in body
    assert "#### Tool call 3: repl" in body
    assert "const risk = 0.03;" in body
    assert "#### Tool call 5: web_search" in body
    assert '"query": "official visa bulletin"' in body
    assert "https://example.test" in body
    assert hashlib.sha256(body.encode("utf-8")).hexdigest() == (
        rendered["content_sha256"]
    )


def test_render_source_marks_missing_file_payload_partial_and_private():
    chat = conversation(
        ID_NEXT, files=[{"file_name": "diagram.png", "file_uuid": "file-123"}]
    )
    rendered = MODULE.render_source(
        chat, "2026-07-25T12:00:00+10:00", force_private=True
    )
    assert rendered["capture_fidelity"] == "partial"
    assert rendered["path"].startswith("40_Archive/AI Conversations/originals/claude/")
    assert "binary payload" in rendered["content"]
    assert "  - private" in rendered["content"]
    assert "raw_status: partial" in rendered["content"]
    assert (
        '  - "[[40_Archive/AI Conversations/claude/'
        '2026-07-24-a-useful-chat--22222222]]"'
        in rendered["content"]
    )
    assert rendered["digest_path"] == (
        "40_Archive/AI Conversations/claude/2026-07-24-a-useful-chat--22222222.md"
    )


def test_render_source_allows_explicit_digest_link_override():
    rendered = MODULE.render_source(
        conversation(ID_NEXT),
        "2026-07-25T12:00:00+10:00",
        digest_link="[[custom/digest]]",
    )
    assert '  - "[[custom/digest]]"' in rendered["content"]
