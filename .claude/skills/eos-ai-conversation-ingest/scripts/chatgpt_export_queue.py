#!/usr/bin/env python3
"""Build a resumable ChatGPT native-export queue and render source notes.

ChatGPT exports conversations as ``conversations-NNN.json`` shards, usually
inside one ZIP. The mapping is a parent-linked graph rather than a flat turn
list, so rendering keeps the selected branch first and then preserves every
remaining exported message as explicitly labelled alternate-branch evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator


SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_SCRIPT = SCRIPT_DIR / "claude_export_queue.py"
SHARD_RE = re.compile(r"(?:^|/)conversations-\d+\.json$", re.IGNORECASE)


def load_common() -> Any:
    spec = importlib.util.spec_from_file_location(
        "conversation_export_common", COMMON_SCRIPT
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load shared queue helper: {COMMON_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


COMMON = load_common()
LEDGER_ROW_RE = COMMON.LEDGER_ROW_RE
processed_ids_from_ledger = COMMON.processed_ids_from_ledger
load_connection = COMMON.load_connection
vault_read = COMMON.vault_read
local_vault_read = COMMON.local_vault_read
normalize_newlines = COMMON.normalize_newlines
slugify = COMMON.slugify
yaml_string = COMMON.yaml_string


def resolved_inputs(values: Iterable[str | Path]) -> list[Path]:
    paths: list[Path] = []
    for value in values:
        path = Path(value)
        if not path.exists():
            raise FileNotFoundError(path)
        paths.append(path.resolve())
    if not paths:
        raise ValueError("No ChatGPT export ZIP, directory, or JSON shard supplied")
    return paths


def export_paths(values: Iterable[str | Path]) -> list[Path]:
    """Compatibility entry point used by generic inventory tooling."""
    return resolved_inputs(values)


def iter_json_array(path: Path) -> Iterator[Any]:
    """Yield a standalone ChatGPT JSON shard."""
    yield from COMMON.iter_json_array(path)


def iter_input_conversations(path: Path) -> Iterator[dict[str, Any]]:
    if path.is_dir():
        shards = sorted(
            child
            for child in path.rglob("conversations-*.json")
            if SHARD_RE.search(child.as_posix())
        )
        if not shards:
            raise ValueError(f"No conversations-NNN.json shards found in {path}")
        for shard in shards:
            yield from COMMON.iter_json_array(shard)
        return
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            names = sorted(name for name in archive.namelist() if SHARD_RE.search(name))
            if not names:
                raise ValueError(f"No conversations-NNN.json shards found in {path}")
            for name in names:
                with archive.open(name) as handle:
                    rows = json.load(handle)
                if not isinstance(rows, list):
                    raise ValueError(f"{path}!{name} is not a top-level JSON array")
                yield from rows
        return
    if SHARD_RE.search(path.as_posix()) or path.suffix.lower() == ".json":
        yield from COMMON.iter_json_array(path)
        return
    raise ValueError(f"Unsupported ChatGPT export input: {path}")


def iter_conversations(values: Iterable[str | Path]) -> Iterator[dict[str, Any]]:
    for path in resolved_inputs(values):
        yield from iter_input_conversations(path)


def conversation_id(conversation: dict[str, Any]) -> str:
    return str(
        conversation.get("conversation_id") or conversation.get("id") or ""
    ).lower()


def epoch_iso(value: Any) -> str:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat().replace("+00:00", "Z")


def material_nodes(conversation: dict[str, Any]) -> list[dict[str, Any]]:
    mapping = conversation.get("mapping")
    if not isinstance(mapping, dict):
        return []
    return [
        node
        for node in mapping.values()
        if isinstance(node, dict) and isinstance(node.get("message"), dict)
    ]


def selected_node_ids(conversation: dict[str, Any]) -> list[str]:
    mapping = conversation.get("mapping")
    if not isinstance(mapping, dict):
        return []
    node_id = str(conversation.get("current_node") or "")
    selected: list[str] = []
    seen: set[str] = set()
    while node_id and node_id in mapping and node_id not in seen:
        seen.add(node_id)
        selected.append(node_id)
        node = mapping.get(node_id)
        node_id = str(node.get("parent") or "") if isinstance(node, dict) else ""
    selected.reverse()
    return selected


def metadata_attachments(message: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = message.get("metadata")
    attachments = metadata.get("attachments") if isinstance(metadata, dict) else []
    return [row for row in attachments or [] if isinstance(row, dict)]


def provider_asset_id(value: Any) -> str:
    """Return the stable provider file ID from an exported asset pointer."""
    if isinstance(value, dict):
        for key in ("asset_pointer", "file_id", "id"):
            identifier = provider_asset_id(value.get(key))
            if identifier:
                return identifier
        return ""
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.split("?", 1)[0].rstrip("/")
    return text.rsplit("/", 1)[-1]


def conversation_asset_records(
    conversation: dict[str, Any],
) -> list[dict[str, Any]]:
    """Consolidate attachment metadata and part pointers by provider asset ID."""
    records: dict[str, dict[str, Any]] = {}
    unresolved = 0
    for node in material_nodes(conversation):
        message = node["message"]
        for attachment in metadata_attachments(message):
            identifier = provider_asset_id(attachment.get("id"))
            if not identifier:
                unresolved += 1
                identifier = f"unresolved-attachment-{unresolved}"
            row = records.setdefault(
                identifier,
                {
                    "provider_asset_id": identifier,
                    "recoverable_id": not identifier.startswith("unresolved-"),
                },
            )
            row.update(
                {
                    key: value
                    for key, value in {
                        "name": attachment.get("name"),
                        "mime_type": (
                            attachment.get("mime_type")
                            or attachment.get("mimeType")
                        ),
                        "size": attachment.get("size"),
                        "url": attachment.get("url"),
                    }.items()
                    if value not in (None, "")
                }
            )
        content = message.get("content")
        parts = content.get("parts") if isinstance(content, dict) else []
        for part in parts or []:
            if not isinstance(part, dict):
                continue
            pointer = (
                part.get("asset_pointer")
                or part.get("audio_asset_pointer")
                or part.get("video_container_asset_pointer")
            )
            if not pointer:
                continue
            identifier = provider_asset_id(pointer)
            if not identifier:
                unresolved += 1
                identifier = f"unresolved-pointer-{unresolved}"
            row = records.setdefault(
                identifier,
                {
                    "provider_asset_id": identifier,
                    "recoverable_id": not identifier.startswith("unresolved-"),
                },
            )
            row["pointer"] = pointer
            if row.get("size") is None and part.get("size_bytes") is not None:
                row["size"] = part["size_bytes"]
            if not row.get("mime_type"):
                content_type = str(part.get("content_type") or "")
                format_name = str(part.get("format") or "")
                if content_type == "image_asset_pointer":
                    row["mime_type"] = "image/unknown"
                elif format_name:
                    row["mime_type"] = f"audio/{format_name}"
    return list(records.values())


def read_export_asset(
    export_values: Iterable[str | Path], provider_id: str
) -> tuple[bytes, str] | None:
    """Read one provider asset byte-for-byte from a native export."""
    wanted = provider_asset_id(provider_id)
    if not wanted:
        return None
    for path in resolved_inputs(export_values):
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as archive:
                member = next(
                    (
                        name
                        for name in archive.namelist()
                        if Path(name).stem == wanted
                    ),
                    None,
                )
                if member:
                    return archive.read(member), f"{path}!{member}"
            continue
        sibling = next(
            (
                candidate
                for candidate in path.parent.glob(f"{wanted}.*")
                if candidate.is_file()
            ),
            None,
        )
        if sibling:
            return sibling.read_bytes(), str(sibling)
    return None


def part_asset_count(message: dict[str, Any]) -> int:
    content = message.get("content")
    parts = content.get("parts") if isinstance(content, dict) else []
    return sum(
        1
        for part in parts or []
        if isinstance(part, dict)
        and (
            part.get("asset_pointer")
            or part.get("audio_asset_pointer")
            or part.get("video_container_asset_pointer")
        )
    )


def conversation_content_fingerprint(conversation: dict[str, Any]) -> str:
    """Hash exact exported message content while ignoring conversation identity.

    ChatGPT exports can contain multiple conversation UUIDs that reuse the same
    message graph. They must each retain an immutable source receipt, but they
    should not trigger repeated semantic review or living-note mutations.
    """
    nodes, selected_set = ordered_nodes(conversation)
    payload: list[dict[str, Any]] = []
    for node in nodes:
        message = node["message"]
        author = message.get("author")
        metadata = message.get("metadata")
        payload.append(
            {
                "branch": (
                    "selected"
                    if str(node.get("id") or "") in selected_set
                    else "alternate"
                ),
                "author": {
                    "role": (
                        author.get("role") if isinstance(author, dict) else None
                    ),
                    "name": (
                        author.get("name") if isinstance(author, dict) else None
                    ),
                },
                "recipient": message.get("recipient"),
                "content": message.get("content"),
                "attachments": metadata_attachments(message),
                "content_references": (
                    metadata.get("content_references")
                    if isinstance(metadata, dict)
                    else None
                ),
            }
        )
    if not payload:
        return ""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def inventory_item(
    conversation: dict[str, Any], batch: int, ordinal: int
) -> dict[str, Any]:
    nodes = material_nodes(conversation)
    selected = set(selected_node_ids(conversation))
    selected_count = sum(
        1 for node in nodes if str(node.get("id") or "") in selected
    )
    attachment_count = sum(
        len(metadata_attachments(node["message"])) for node in nodes
    )
    asset_count = sum(part_asset_count(node["message"]) for node in nodes)
    return {
        "provider_id": conversation_id(conversation),
        "provider": "chatgpt",
        "title": conversation.get("title") or "Untitled",
        "created_at": epoch_iso(conversation.get("create_time")),
        "updated_at": epoch_iso(conversation.get("update_time")),
        "message_count": len(nodes),
        "selected_message_count": selected_count,
        "alternate_message_count": len(nodes) - selected_count,
        "attachment_count": attachment_count,
        "asset_count": asset_count,
        "file_count": attachment_count + asset_count,
        "content_fingerprint_sha256": conversation_content_fingerprint(conversation),
        "capture_fidelity": (
            "partial" if attachment_count or asset_count else "export-native"
        ),
        "is_archived": bool(conversation.get("is_archived")),
        "is_starred": bool(conversation.get("is_starred")),
        "batch": batch,
        "ordinal": ordinal,
    }


def iter_inventory_items(values: Iterable[str | Path]) -> Iterator[dict[str, Any]]:
    for ordinal, conversation in enumerate(iter_conversations(values)):
        yield inventory_item(conversation, 0, ordinal)


def build_queue(
    export_values: Iterable[str | Path], processed_ids: set[str]
) -> dict[str, Any]:
    values = list(export_values)
    inventory: list[dict[str, Any]] = []
    seen: set[str] = set()
    duplicates: list[str] = []
    for item in iter_inventory_items(values):
        provider_id = item["provider_id"]
        if not provider_id:
            raise ValueError(f"Conversation ordinal {item['ordinal']} has no provider ID")
        if provider_id in seen:
            duplicates.append(provider_id)
            continue
        seen.add(provider_id)
        inventory.append(item)
    fingerprint_groups: dict[str, list[dict[str, Any]]] = {}
    for item in inventory:
        fingerprint = str(item.get("content_fingerprint_sha256") or "")
        if fingerprint:
            fingerprint_groups.setdefault(fingerprint, []).append(item)
    content_duplicate_groups: list[dict[str, Any]] = []
    for fingerprint, grouped_items in fingerprint_groups.items():
        if len(grouped_items) < 2:
            continue
        canonical = next(
            (
                item
                for item in grouped_items
                if item["provider_id"] in processed_ids
            ),
            grouped_items[0],
        )
        canonical_id = canonical["provider_id"]
        provider_ids = [item["provider_id"] for item in grouped_items]
        content_duplicate_groups.append(
            {
                "content_fingerprint_sha256": fingerprint,
                "canonical_provider_id": canonical_id,
                "provider_ids": provider_ids,
            }
        )
        for item in grouped_items:
            if item["provider_id"] != canonical_id:
                item["exact_content_duplicate_of"] = canonical_id
    pending = [
        item for item in inventory if item["provider_id"] not in processed_ids
    ]
    paths = resolved_inputs(values)
    return {
        "schema_version": 1,
        "provider": "chatgpt",
        "exports": [str(path) for path in paths],
        "total_unique": len(inventory),
        "processed": len(inventory) - len(pending),
        "pending": len(pending),
        "duplicate_ids": duplicates,
        "exact_content_duplicate_count": sum(
            len(group["provider_ids"]) - 1 for group in content_duplicate_groups
        ),
        "exact_content_duplicate_groups": content_duplicate_groups,
        "next_pending": pending[0] if pending else None,
        "items": pending,
    }


def find_conversation(
    export_values: Iterable[str | Path], provider_id: str
) -> dict[str, Any]:
    wanted = provider_id.lower()
    for conversation in iter_conversations(export_values):
        if conversation_id(conversation) == wanted:
            return conversation
    raise KeyError(f"Conversation not found: {provider_id}")


def display_role(message: dict[str, Any]) -> str:
    author = message.get("author")
    role = (
        str(author.get("role") or "unknown").lower()
        if isinstance(author, dict)
        else "unknown"
    )
    content = message.get("content")
    content_type = (
        str(content.get("content_type") or "text").lower()
        if isinstance(content, dict)
        else "text"
    )
    base = {
        "user": "User",
        "assistant": "ChatGPT",
        "system": "System",
        "tool": "Tool",
    }.get(role, role.title())
    if content_type == "reasoning_recap":
        return f"{base} reasoning recap"
    if content_type == "thoughts":
        return f"{base} exported reasoning"
    return base


def scalar_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def render_part(part: Any) -> list[str]:
    if isinstance(part, str):
        return [part]
    if not isinstance(part, dict):
        return [scalar_text(part)]
    lines: list[str] = []
    if part.get("text") is not None:
        lines.append(scalar_text(part.get("text")))
    asset_pointer = (
        part.get("asset_pointer")
        or part.get("audio_asset_pointer")
        or part.get("video_container_asset_pointer")
    )
    if asset_pointer:
        asset_type = part.get("content_type") or "asset"
        details = [f"type={asset_type}", f"pointer={asset_pointer}"]
        if part.get("size_bytes") is not None:
            details.append(f"size={part['size_bytes']}")
        lines.append("[Exported asset metadata: " + "; ".join(details) + "]")
    if not lines:
        safe = {
            key: value
            for key, value in part.items()
            if key
            in {
                "content_type",
                "direction",
                "format",
                "audio_start_timestamp",
                "expiry_datetime",
            }
        }
        lines.append("[Exported structured content: " + scalar_text(safe) + "]")
    return lines


def render_attachment(
    attachment: dict[str, Any],
    ordinal: int,
    archived_assets: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    name = attachment.get("name") or "unnamed"
    mime = attachment.get("mime_type") or attachment.get("mimeType") or "unknown"
    identifier = attachment.get("id")
    size = attachment.get("size")
    lines = [
        f"#### Attachment {ordinal}: {name}",
        "",
        f"- Type: `{mime}`",
    ]
    if size is not None:
        lines.append(f"- Size: `{size}`")
    if identifier:
        lines.append(f"- Provider asset ID: `{identifier}`")
    if attachment.get("url"):
        lines.append(f"- URL: {attachment['url']}")
    archived = (
        (archived_assets or {}).get(provider_asset_id(identifier))
        if identifier
        else None
    )
    if archived:
        path = str(archived["path"]).removesuffix(".md")
        lines.extend(
            [
                f"- Vault original: [[{path}]]",
                f"- Binary SHA-256: `{archived['content_sha256']}`",
                "",
                "_The provider-export binary was copied to Vault and verified "
                "byte-for-byte._",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "_The source note preserves exported metadata, but no matching "
                "binary payload was recovered from the provider export; capture "
                "remains partial._",
            ]
        )
    return lines


def direct_references(message: dict[str, Any]) -> list[dict[str, str]]:
    metadata = message.get("metadata")
    refs = metadata.get("content_references") if isinstance(metadata, dict) else []
    results: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in refs or []:
        if not isinstance(row, dict):
            continue
        url = scalar_text(row.get("url")).strip()
        title = scalar_text(row.get("title") or row.get("source_name")).strip()
        if not url and not title:
            continue
        key = (url, title)
        if key in seen:
            continue
        seen.add(key)
        results.append(
            {
                "url": url,
                "title": title,
                "snippet": scalar_text(row.get("snippet")).strip(),
            }
        )
    return results


def render_message(
    node: dict[str, Any],
    ordinal: int,
    *,
    selected: bool,
    archived_assets: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    message = node["message"]
    timestamp = epoch_iso(message.get("create_time")) or "timestamp unavailable"
    role = display_role(message)
    branch = "selected" if selected else "alternate"
    node_id = str(node.get("id") or message.get("id") or "")
    parent_id = str(node.get("parent") or "")
    lines = [
        f"### {ordinal:03d} · {role} · {timestamp}",
        "",
        f"> Branch: `{branch}` · node `{node_id}`"
        + (f" · parent `{parent_id}`" if parent_id else ""),
        "",
    ]
    content = message.get("content")
    parts = content.get("parts") if isinstance(content, dict) else []
    rendered_parts: list[str] = []
    for part in parts or []:
        rendered_parts.extend(render_part(part))
    if rendered_parts:
        lines.extend(rendered_parts)
    else:
        lines.append("> No textual part was present in this exported message.")
    for index, attachment in enumerate(metadata_attachments(message), start=1):
        lines.extend(
            [
                "",
                *render_attachment(
                    attachment,
                    index,
                    archived_assets=archived_assets,
                ),
            ]
        )
    references = direct_references(message)
    if references:
        lines.extend(["", "#### Exported references", ""])
        for reference in references:
            label = reference["title"] or reference["url"] or "Reference"
            if reference["url"]:
                lines.append(f"- [{label}]({reference['url']})")
            else:
                lines.append(f"- {label}")
            if reference["snippet"]:
                lines.append(f"  - {reference['snippet']}")
    lines.append("")
    return lines


def ordered_nodes(conversation: dict[str, Any]) -> tuple[list[dict[str, Any]], set[str]]:
    mapping = conversation.get("mapping")
    if not isinstance(mapping, dict):
        return [], set()
    selected_ids = selected_node_ids(conversation)
    selected_set = set(selected_ids)
    selected = [
        mapping[node_id]
        for node_id in selected_ids
        if isinstance(mapping.get(node_id), dict)
        and isinstance(mapping[node_id].get("message"), dict)
    ]
    alternates = [
        node
        for node in material_nodes(conversation)
        if str(node.get("id") or "") not in selected_set
    ]
    alternates.sort(
        key=lambda node: (
            float((node.get("message") or {}).get("create_time") or 0),
            str(node.get("id") or ""),
        )
    )
    return selected + alternates, selected_set


def render_source(
    conversation: dict[str, Any],
    captured_at: str,
    digest_link: str = "",
    force_private: bool = False,
    archived_assets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    provider_id = conversation_id(conversation)
    if not provider_id:
        raise ValueError("ChatGPT conversation has no provider ID")
    title = str(conversation.get("title") or "Untitled")
    item = inventory_item(conversation, 0, 0)
    expected_assets = conversation_asset_records(conversation)
    archived_asset_rows = archived_assets or []
    archived_by_id = {
        str(row.get("provider_asset_id") or ""): row
        for row in archived_asset_rows
        if row.get("provider_asset_id")
    }
    expected_ids = {
        str(row["provider_asset_id"])
        for row in expected_assets
        if row.get("recoverable_id")
    }
    has_unresolved_assets = any(
        not row.get("recoverable_id") for row in expected_assets
    )
    all_assets_archived = (
        item["file_count"] == 0
        or (
            bool(expected_ids)
            and not has_unresolved_assets
            and expected_ids.issubset(archived_by_id)
        )
    )
    capture_fidelity = "export-native" if all_assets_archived else "partial"
    nodes, selected_set = ordered_nodes(conversation)
    date = (item["created_at"] or captured_at)[:10]
    body_lines = [
        f"# {title}",
        "",
        "> Native ChatGPT export capture. The selected branch is rendered first; "
        "all remaining exported message nodes follow as alternate-branch evidence.",
        "",
        "## Messages",
        "",
    ]
    for index, node in enumerate(nodes, start=1):
        body_lines.extend(
            render_message(
                node,
                index,
                selected=str(node.get("id") or "") in selected_set,
                archived_assets=archived_by_id,
            )
        )
    if archived_asset_rows:
        body_lines.extend(["## Archived binary assets", ""])
        for row in archived_asset_rows:
            path = str(row["path"]).removesuffix(".md")
            body_lines.append(
                f"- [[{path}]] · provider asset "
                f"`{row['provider_asset_id']}` · `{row['content_sha256']}` · "
                f"{row['size']} bytes"
            )
        body_lines.append("")
    normalized_body = normalize_newlines("\n".join(body_lines)).rstrip() + "\n"
    content_hash = hashlib.sha256(normalized_body.encode("utf-8")).hexdigest()
    source_root = (
        "40_Archive/AI Conversations/originals/chatgpt"
        if force_private
        else "30_Resources/conversations/originals/chatgpt"
    )
    digest_root = (
        "40_Archive/AI Conversations/chatgpt"
        if force_private
        else "30_Resources/conversations"
    )
    slug = slugify(title)
    path = f"{source_root}/{date}-{slug}--{provider_id[:8]}.md"
    digest_path = f"{digest_root}/{date}-{slug}--{provider_id[:8]}.md"
    if not digest_link:
        digest_link = f"[[{digest_path.removesuffix('.md')}]]"
    tags = ["conversation", "conversation-source", "chatgpt"]
    if force_private:
        tags.append("private")
    raw_status = "complete" if capture_fidelity == "export-native" else "partial"
    frontmatter = [
        "---",
        "record_kind: conversation-source",
        "archive_schema: eos-ai-conversation-v1",
        "author: both",
        "provider: chatgpt",
        f"source_conversation_id: {yaml_string(provider_id)}",
        f"source_url: {yaml_string('https://chatgpt.com/c/' + provider_id)}",
        f"title: {yaml_string(title)}",
        f"conversation_created_at: {yaml_string(item['created_at'])}",
        f"conversation_updated_at: {yaml_string(item['updated_at'])}",
        f"captured_at: {yaml_string(captured_at)}",
        "capture_method: chatgpt-native-export-json",
        f"capture_fidelity: {capture_fidelity}",
        f"raw_status: {raw_status}",
        f"message_count: {item['message_count']}",
        f"selected_message_count: {item['selected_message_count']}",
        f"alternate_message_count: {item['alternate_message_count']}",
        f"attachment_count: {item['attachment_count']}",
        f"asset_count: {item['asset_count']}",
        f"archived_asset_count: {len(archived_asset_rows)}",
        *(
            ["archived_assets:"]
            + [
                f'  - "[[{str(row["path"]).removesuffix(".md")}]]"'
                for row in archived_asset_rows
            ]
            if archived_asset_rows
            else ["archived_assets: []"]
        ),
        f"content_sha256: {content_hash}",
        "tags:",
        *[f"  - {tag}" for tag in tags],
        "related:",
        f"  - {yaml_string(digest_link)}",
        "---",
        "",
    ]
    content = "\n".join(frontmatter) + normalized_body
    return {
        "path": path,
        "digest_path": digest_path,
        "provider_id": provider_id,
        "title": title,
        "message_count": item["message_count"],
        "selected_message_count": item["selected_message_count"],
        "alternate_message_count": item["alternate_message_count"],
        "capture_fidelity": capture_fidelity,
        "archived_assets": archived_asset_rows,
        "content_sha256": content_hash,
        "content": content,
    }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="Reconcile export with the ledger")
    build.add_argument("--export", action="append", required=True)
    build.add_argument("--config", type=Path, default=Path("emptyos.toml"))
    build.add_argument(
        "--vault-root",
        type=Path,
        help="Read the ledger directly when the EmptyOS daemon is offline",
    )
    build.add_argument(
        "--ledger",
        default="30_Resources/conversations/ai-conversation-ingestion-ledger.md",
    )
    build.add_argument("--output", type=Path, required=True)
    render = subparsers.add_parser("render", help="Render one source note")
    render.add_argument("--export", action="append", required=True)
    render.add_argument("--id", required=True)
    render.add_argument("--captured-at", required=True)
    render.add_argument("--digest-link", default="")
    render.add_argument("--private", action="store_true")
    render.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.command == "build":
        if args.vault_root:
            ledger = local_vault_read(args.vault_root, args.ledger)
        else:
            base_url, token = load_connection(args.config)
            ledger = vault_read(base_url, token, args.ledger)
        queue = build_queue(
            args.export,
            processed_ids_from_ledger(ledger, provider="chatgpt"),
        )
        write_json(args.output, queue)
        print(
            json.dumps(
                {
                    key: queue[key]
                    for key in ("total_unique", "processed", "pending", "next_pending")
                },
                ensure_ascii=False,
            )
        )
        return 0
    conversation = find_conversation(args.export, args.id)
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
