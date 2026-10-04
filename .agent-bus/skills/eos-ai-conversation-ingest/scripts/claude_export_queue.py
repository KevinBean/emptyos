#!/usr/bin/env python3
"""Build a resumable Claude native-export queue and render exact source notes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Iterator


LEDGER_ROW_RE = re.compile(
    r"(?im)^(?:\s*-\s+(?:\[[ x]\]\s+)?|\s*\|\s*[^|\r\n]*\|\s*[^|\r\n]*\|\s*)"
    # Legacy rows begin with the provider UUID. Canonical rows are date,
    # separator, provider, separator, UUID. Do not hard-code the separator:
    # vault/API serialisation can present it differently. Anchoring that
    # four-token prefix prevents UUIDs in a free-text `next:` pointer from
    # being counted as completed work.
    r"(?:\d{4}-\d{2}-\d{2}\s+\S+\s+\S+\s+\S+\s+)?"
    # The helper originated with Claude exports, but the evidence auditor also
    # reuses this ledger parser for ChatGPT and Gemini. ChatGPT conversation
    # IDs are UUID-shaped without RFC version/variant guarantees; Gemini uses
    # 16 lowercase hex characters.
    r"(?P<id>(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12})|(?:[0-9a-f]{16}))\b"
)


def iter_json_array(path: str | Path, chunk_size: int = 1024 * 1024) -> Iterator[Any]:
    """Yield values from a UTF-8 JSON top-level array incrementally."""
    path = Path(path)
    decoder = json.JSONDecoder()
    with path.open("r", encoding="utf-8-sig") as handle:
        buffer = ""
        position = 0
        eof = False
        started = False
        while True:
            if not eof and len(buffer) - position < chunk_size // 2:
                if position:
                    buffer = buffer[position:]
                    position = 0
                chunk = handle.read(chunk_size)
                if chunk:
                    buffer += chunk
                else:
                    eof = True
            while position < len(buffer) and buffer[position].isspace():
                position += 1
            if not started:
                if position >= len(buffer):
                    if eof:
                        raise ValueError(f"{path} is empty")
                    continue
                if buffer[position] != "[":
                    raise ValueError(f"{path} does not contain a top-level JSON array")
                position += 1
                started = True
                continue
            while position < len(buffer) and (
                buffer[position].isspace() or buffer[position] == ","
            ):
                position += 1
            if position < len(buffer) and buffer[position] == "]":
                return
            if position >= len(buffer):
                if eof:
                    raise ValueError(f"{path} ended before the JSON array closed")
                continue
            try:
                value, end = decoder.raw_decode(buffer, position)
            except json.JSONDecodeError:
                if eof:
                    raise
                chunk = handle.read(chunk_size)
                if chunk:
                    buffer += chunk
                else:
                    eof = True
                continue
            yield value
            position = end


def ledger_row_provider(match: re.Match[str]) -> str | None:
    """Infer a provider from the row prefix captured before its primary UUID."""
    prefix = match.group(0).lower()
    for provider in ("claude", "chatgpt", "gemini", "codex"):
        if re.search(rf"\b{provider}(?:-[a-z0-9-]+)?\b", prefix):
            return provider
    return None


def processed_ids_from_ledger(
    markdown: str, provider: str | None = None
) -> set[str]:
    """Return row-leading IDs, optionally restricted to one provider.

    Provider-less legacy rows remain eligible for compatibility. Rows that
    explicitly name another provider are excluded so one provider's ledger
    history cannot distort another provider's queue or audit.
    """
    normalized_provider = provider.lower() if provider else None
    matches = list(LEDGER_ROW_RE.finditer(markdown))
    explicit_by_id: dict[str, set[str]] = {}
    for match in matches:
        provider_id = match.group("id").lower()
        row_provider = ledger_row_provider(match)
        if row_provider is not None:
            explicit_by_id.setdefault(provider_id, set()).add(row_provider)

    processed: set[str] = set()
    for match in matches:
        provider_id = match.group("id").lower()
        row_provider = ledger_row_provider(match)
        explicit = explicit_by_id.get(provider_id, set())
        if normalized_provider and explicit and normalized_provider not in explicit:
            continue
        if (
            normalized_provider
            and row_provider is not None
            and row_provider != normalized_provider
        ):
            continue
        processed.add(provider_id)
    return processed


def load_connection(config_path: Path) -> tuple[str, str]:
    with config_path.open("rb") as handle:
        config = tomllib.load(handle)
    network = config.get("network", {})
    host = str(network.get("host", "127.0.0.1"))
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    port = int(network.get("port", 9000))
    return f"http://{host}:{port}", str(network.get("auth_token", ""))


def vault_read(base_url: str, token: str, vault_path: str) -> str:
    query = urllib.parse.urlencode({"path": vault_path})
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = urllib.request.Request(
        f"{base_url}/api/vault/read?{query}", headers=headers
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return str(json.load(response)["content"])


def local_vault_read(vault_root: Path, vault_path: str) -> str:
    """Read one UTF-8 vault note without allowing paths outside the vault."""
    relative = Path(vault_path)
    if relative.is_absolute():
        raise ValueError(f"Vault path must be relative: {vault_path}")
    root = vault_root.resolve()
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise ValueError(f"Vault path escapes root: {vault_path}") from error
    # Match `/api/vault/read`, whose Path.read_text() uses universal-newline
    # translation. Preserving CRLF here makes Markdown/frontmatter parsing
    # diverge from the daemon-backed audit on legacy Windows-authored notes.
    return target.read_text(encoding="utf-8")


def conversation_id(conversation: dict[str, Any]) -> str:
    return str(conversation.get("uuid") or conversation.get("id") or "").lower()


def messages(conversation: dict[str, Any]) -> list[dict[str, Any]]:
    raw = conversation.get("chat_messages")
    if raw is None:
        raw = conversation.get("messages")
    return raw if isinstance(raw, list) else []


def normalize_newlines(text: str) -> str:
    """Return the archive-contract newline form used for body hashing."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def inventory_item(
    conversation: dict[str, Any], batch: int, ordinal: int
) -> dict[str, Any]:
    chat_messages = messages(conversation)
    attachment_count = sum(
        len(message.get("attachments") or []) for message in chat_messages
    )
    file_count = sum(len(message.get("files") or []) for message in chat_messages)
    missing_attachment_content = any(
        not attachment.get("extracted_content")
        for message in chat_messages
        for attachment in (message.get("attachments") or [])
    )
    return {
        "provider_id": conversation_id(conversation),
        "title": conversation.get("name") or conversation.get("title") or "Untitled",
        "created_at": conversation.get("created_at"),
        "updated_at": conversation.get("updated_at"),
        "message_count": len(chat_messages),
        "attachment_count": attachment_count,
        "file_count": file_count,
        "capture_fidelity": (
            "partial" if file_count or missing_attachment_content else "export-native"
        ),
        "batch": batch,
        "ordinal": ordinal,
    }


def build_queue(
    export_paths: Iterable[Path], processed_ids: set[str]
) -> dict[str, Any]:
    inventory: list[dict[str, Any]] = []
    seen: set[str] = set()
    duplicate_ids: list[str] = []
    resolved_paths = list(export_paths)
    for batch, export_path in enumerate(resolved_paths):
        for ordinal, conversation in enumerate(iter_json_array(export_path)):
            provider_id = conversation_id(conversation)
            if not provider_id:
                raise ValueError(
                    f"Conversation {ordinal} in {export_path} has no provider ID"
                )
            if provider_id in seen:
                duplicate_ids.append(provider_id)
                continue
            seen.add(provider_id)
            inventory.append(inventory_item(conversation, batch, ordinal))
    pending = [
        item for item in inventory if item["provider_id"] not in processed_ids
    ]
    return {
        "schema_version": 1,
        "provider": "claude",
        "exports": [str(path.resolve()) for path in resolved_paths],
        "total_unique": len(inventory),
        "processed": len(inventory) - len(pending),
        "pending": len(pending),
        "duplicate_ids": duplicate_ids,
        "next_pending": pending[0] if pending else None,
        "items": pending,
    }


def iter_inventory_items(values: Iterable[str | Path]) -> Iterator[dict[str, Any]]:
    """Yield provider inventory rows through the generic evidence auditor."""
    paths = export_paths([str(value) for value in values])
    for batch, export_path in enumerate(paths):
        for ordinal, conversation in enumerate(iter_json_array(export_path)):
            yield inventory_item(conversation, batch, ordinal)


def find_conversation(
    export_paths: Iterable[str | Path], provider_id: str
) -> dict[str, Any]:
    wanted = provider_id.lower()
    for export_path in export_paths:
        for conversation in iter_json_array(export_path):
            if conversation_id(conversation) == wanted:
                return conversation
    raise KeyError(f"Conversation not found: {provider_id}")


def yaml_string(value: Any) -> str:
    return json.dumps("" if value is None else str(value), ensure_ascii=False)


def slugify(value: str) -> str:
    value = re.sub(r"[^\w\s-]", "", value.lower(), flags=re.UNICODE)
    return re.sub(r"[-\s_]+", "-", value).strip("-")[:80] or "untitled"


def display_role(message: dict[str, Any]) -> str:
    sender = str(message.get("sender") or message.get("role") or "unknown").lower()
    if sender in {"human", "user"}:
        return "User"
    if sender in {"assistant", "claude"}:
        return "Claude"
    return sender.title()


def render_attachment(attachment: dict[str, Any], ordinal: int) -> list[str]:
    filename = attachment.get("file_name") or attachment.get("filename") or "unnamed"
    media_type = attachment.get("file_type") or attachment.get("type") or "unknown"
    size = attachment.get("file_size") or attachment.get("size")
    lines = [f"#### Attachment {ordinal}: {filename}", "", f"- Type: `{media_type}`"]
    if size is not None:
        lines.append(f"- Size: `{size}`")
    content = attachment.get("extracted_content")
    if content:
        lines.extend(["", "```text", str(content), "```"])
    else:
        lines.extend(
            ["", "_No extracted attachment content was present in the export._"]
        )
    return lines


def render_file(file_item: dict[str, Any], ordinal: int) -> list[str]:
    filename = file_item.get("file_name") or file_item.get("filename") or "unnamed"
    file_id = file_item.get("file_uuid") or file_item.get("uuid") or file_item.get("id")
    lines = [
        f"#### File {ordinal}: {filename}",
        "",
        "_The native export contained file metadata but not the binary payload._",
    ]
    if file_id:
        lines.append(f"- Provider file ID: `{file_id}`")
    return lines


def render_artifact(block: dict[str, Any], ordinal: int) -> list[str]:
    """Render recoverable Claude artifact source embedded in a tool call.

    Claude's native export keeps generated artifact code in
    ``content[].input.content`` while the message text contains only a mobile
    preview placeholder. Omitting that tool input silently drops the actual
    deliverable from the immutable source archive.
    """
    artifact_input = block.get("input")
    if not isinstance(artifact_input, dict):
        return []
    content = artifact_input.get("content")
    if content in (None, ""):
        return []
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, indent=2)

    command = str(artifact_input.get("command") or "unknown")
    artifact_id = str(artifact_input.get("id") or f"artifact-{ordinal}")
    title = str(artifact_input.get("title") or artifact_id)
    media_type = str(artifact_input.get("type") or "unknown")
    version_uuid = artifact_input.get("version_uuid")
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    language = (
        "html"
        if media_type == "text/html"
        else "jsx"
        if media_type == "application/vnd.ant.react"
        else "text"
    )
    longest_ticks = max((len(run) for run in re.findall(r"`+", content)), default=0)
    fence = "`" * max(3, longest_ticks + 1)

    lines = [
        f"#### Generated artifact {ordinal}: {title}",
        "",
        f"- Command: `{command}`",
        f"- Artifact ID: `{artifact_id}`",
        f"- Type: `{media_type}`",
    ]
    if version_uuid:
        lines.append(f"- Version UUID: `{version_uuid}`")
    lines.extend(
        [
            f"- Content SHA-256: `{content_hash}`",
            "",
            f"{fence}{language}",
            content,
            fence,
        ]
    )
    return lines


def fenced_payload(value: Any, language: str = "json") -> tuple[str, str, str]:
    """Return stable text, hash, and a safe Markdown fence for a tool payload."""
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    longest_ticks = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest_ticks + 1)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return text, digest, f"{fence}{language}"


def render_tool_trace(block: dict[str, Any], ordinal: int) -> list[str]:
    """Preserve recoverable non-create tool calls and tool results.

    Claude exports can keep the only copy of artifact update patches, REPL
    programs, and web-search evidence in ``content[]`` while the flattened
    message text contains only an unsupported-preview marker. These payloads
    are part of the available source evidence and must not be silently lost.
    """
    block_type = block.get("type")
    name = str(block.get("name") or "unknown")
    if block_type == "tool_use":
        payload = block.get("input")
        label = "Artifact operation" if name == "artifacts" else "Tool call"
        language = "javascript" if name == "repl" else "json"
        if name == "repl" and isinstance(payload, dict) and set(payload) == {"code"}:
            payload = payload["code"]
        text, digest, opening = fenced_payload(payload, language)
        fence = opening.split(language, 1)[0] if language else opening
        return [
            f"#### {label} {ordinal}: {name}",
            "",
            f"- Payload SHA-256: `{digest}`",
            "",
            opening,
            text,
            fence,
        ]
    if block_type == "tool_result":
        payload: dict[str, Any] = {
            "content": block.get("content"),
            "is_error": bool(block.get("is_error")),
        }
        if block.get("structured_content") is not None:
            payload["structured_content"] = block.get("structured_content")
        text, digest, opening = fenced_payload(payload)
        fence = opening[:-4]
        return [
            f"#### Tool result {ordinal}: {name}",
            "",
            f"- Payload SHA-256: `{digest}`",
            "",
            opening,
            text,
            fence,
        ]
    return []


def render_source(
    conversation: dict[str, Any],
    captured_at: str,
    digest_link: str = "",
    force_private: bool = False,
) -> dict[str, Any]:
    provider_id = conversation_id(conversation)
    title = str(conversation.get("name") or conversation.get("title") or "Untitled")
    chat_messages = messages(conversation)
    item = inventory_item(conversation, 0, 0)
    date = str(conversation.get("created_at") or captured_at)[:10]
    body_lines = [f"# {title}", "", "## Conversation", ""]
    for index, message in enumerate(chat_messages, start=1):
        timestamp = (
            message.get("created_at")
            or message.get("updated_at")
            or "timestamp unavailable"
        )
        body_lines.extend(
            [
                f"### {index:03d} · {display_role(message)} · {timestamp}",
                "",
                str(message.get("text") or ""),
                "",
            ]
        )
        artifact_index = 0
        tool_trace_index = 0
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type not in {"tool_use", "tool_result"}:
                continue
            if block_type == "tool_use" and block.get("name") == "artifacts":
                artifact_index += 1
                rendered_artifact = render_artifact(block, artifact_index)
                if rendered_artifact:
                    body_lines.extend(rendered_artifact)
                    body_lines.append("")
                    continue
            tool_trace_index += 1
            rendered_trace = render_tool_trace(block, tool_trace_index)
            if rendered_trace:
                body_lines.extend(rendered_trace)
                body_lines.append("")
        for attachment_index, attachment in enumerate(
            message.get("attachments") or [], start=1
        ):
            body_lines.extend(render_attachment(attachment, attachment_index))
            body_lines.append("")
        for file_index, file_item in enumerate(message.get("files") or [], start=1):
            body_lines.extend(render_file(file_item, file_index))
            body_lines.append("")
    # Provider message/attachment strings can already contain CRLF. Normalize
    # the complete body before hashing so Windows text-output translation can
    # never turn embedded CRLF into CRCRLF and invalidate the contract.
    normalized_body = normalize_newlines("\n".join(body_lines)).rstrip() + "\n"
    content_hash = hashlib.sha256(normalized_body.encode("utf-8")).hexdigest()
    folder = (
        "40_Archive/AI Conversations/originals/claude"
        if force_private
        else "30_Resources/conversations/originals/claude"
    )
    path = f"{folder}/{date}-{slugify(title)}--{provider_id[:8]}.md"
    digest_folder = (
        "40_Archive/AI Conversations/claude"
        if force_private
        else "30_Resources/conversations"
    )
    # Provider titles are not identities. Separate Claude conversations often
    # share both a date and a generated title (especially "Untitled"), so a
    # title-only digest path lets the later write replace the earlier digest.
    # Match the source archive and ChatGPT renderer: provider short ID is the
    # stable disambiguator, while legacy title-only digests remain readable.
    digest_path = f"{digest_folder}/{date}-{slugify(title)}--{provider_id[:8]}"
    if not digest_link:
        digest_link = f"[[{digest_path}]]"
    tags = ["conversation", "conversation-source", "claude"]
    if force_private:
        tags.append("private")
    raw_status = "complete" if item["capture_fidelity"] == "export-native" else "partial"
    frontmatter = [
        "---",
        "record_kind: conversation-source",
        "archive_schema: eos-ai-conversation-v1",
        "author: both",
        "provider: claude",
        f"source_conversation_id: {yaml_string(provider_id)}",
        f"source_url: {yaml_string('https://claude.ai/chat/' + provider_id)}",
        f"title: {yaml_string(title)}",
        f"conversation_created_at: {yaml_string(conversation.get('created_at'))}",
        f"conversation_updated_at: {yaml_string(conversation.get('updated_at'))}",
        f"captured_at: {yaml_string(captured_at)}",
        "capture_method: claude-native-export-json",
        f"capture_fidelity: {item['capture_fidelity']}",
        f"raw_status: {raw_status}",
        f"message_count: {len(chat_messages)}",
        f"attachment_count: {item['attachment_count']}",
        f"file_count: {item['file_count']}",
        f"content_sha256: {content_hash}",
        "tags:",
        *[f"  - {tag}" for tag in tags],
    ]
    frontmatter.extend(["related:", f"  - {yaml_string(digest_link)}"])
    frontmatter.extend(["---", ""])
    content = "\n".join(frontmatter) + normalized_body
    return {
        "path": path,
        "digest_path": f"{digest_path}.md",
        "provider_id": provider_id,
        "title": title,
        "message_count": len(chat_messages),
        "capture_fidelity": item["capture_fidelity"],
        "content_sha256": content_hash,
        "content": content,
    }


def export_paths(values: list[str]) -> list[Path]:
    paths: list[Path] = []
    for value in values:
        path = Path(value)
        if path.is_dir():
            paths.extend(sorted(path.glob("batch-*/conversations.json")))
        else:
            paths.append(path)
    if not paths:
        raise ValueError("No conversations.json export files found")
    return paths


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="Reconcile exports with the ledger")
    build.add_argument("--export", action="append", required=True)
    build.add_argument("--config", type=Path, default=Path("emptyos.toml"))
    build.add_argument(
        "--vault-root",
        type=Path,
        help="Read the ledger directly from this vault when the daemon is offline",
    )
    build.add_argument(
        "--ledger",
        default="30_Resources/conversations/ai-conversation-ingestion-ledger.md",
    )
    build.add_argument("--output", type=Path, required=True)
    render = subparsers.add_parser("render", help="Render one exact source note")
    render.add_argument("--export", action="append", required=True)
    render.add_argument("--id", required=True)
    render.add_argument("--captured-at", required=True)
    render.add_argument("--digest-link", default="")
    render.add_argument("--private", action="store_true")
    render.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    paths = export_paths(args.export)
    if args.command == "build":
        if args.vault_root:
            ledger = local_vault_read(args.vault_root, args.ledger)
        else:
            base_url, token = load_connection(args.config)
            ledger = vault_read(base_url, token, args.ledger)
        queue = build_queue(paths, processed_ids_from_ledger(ledger, provider="claude"))
        write_json(args.output, queue)
        keys = ("total_unique", "processed", "pending", "next_pending")
        print(json.dumps({key: queue[key] for key in keys}, ensure_ascii=False))
        return 0
    conversation = find_conversation(paths, args.id)
    rendered = render_source(
        conversation,
        captured_at=args.captured_at,
        digest_link=args.digest_link,
        force_private=args.private,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(rendered["content"].encode("utf-8"))
    print(
        json.dumps(
            {key: value for key, value in rendered.items() if key != "content"},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
