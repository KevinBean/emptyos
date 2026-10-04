#!/usr/bin/env python3
"""Apply reviewed native-export conversation specs through the EmptyOS Vault API.

Each item is prospective-audited before any write and read-back audited after
source, digest, derived-note, and ledger operations. The spec contains the
semantic judgment; this helper owns deterministic rendering, evidence links,
backups, idempotence, and operation receipts.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import mimetypes
import re
import sys
from pathlib import Path
from typing import Any
import urllib.error
import urllib.parse
import urllib.request


SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG = Path("emptyos.toml")
LEDGER_PATH = "30_Resources/conversations/ai-conversation-ingestion-ledger.md"


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load helper: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


APPLY = load_module(
    "conversation_apply_shared", SCRIPT_DIR / "apply_no_mutation_browser_batch.py"
)
AUDIT = load_module("conversation_evidence_audit", SCRIPT_DIR / "audit_evidence_graph.py")


def helper_for(provider: str) -> Any:
    return AUDIT.load_queue_helper(provider)


def yaml_quote(value: Any) -> str:
    return json.dumps("" if value is None else str(value), ensure_ascii=False)


def markdown_cell(value: Any) -> str:
    return str(value or "-").replace("|", "\\|").replace("\n", " ")


def apply_source_gap_overrides(
    rendered: dict[str, Any],
    spec: dict[str, Any],
) -> dict[str, Any]:
    """Record reviewed native-export payload gaps without falsifying fidelity."""
    raw_gaps = spec.get("source_gap_notes") or []
    if not isinstance(raw_gaps, list):
        raise ValueError("source_gap_notes must be a list of reviewed explanations")
    gaps = [
        " ".join(str(value).replace("\r", "\n").split())
        for value in raw_gaps
        if str(value).strip()
    ]
    forced_fidelity = spec.get("force_capture_fidelity")
    if forced_fidelity not in {None, "partial"}:
        raise ValueError("force_capture_fidelity may only be 'partial'")
    if forced_fidelity == "partial" and not gaps:
        raise ValueError(
            "force_capture_fidelity='partial' requires source_gap_notes"
        )
    if not gaps:
        return rendered

    content = str(rendered["content"]).replace("\r\n", "\n").replace("\r", "\n")
    parts = content.split("---", 2)
    if len(parts) != 3:
        raise ValueError("Rendered source is missing YAML frontmatter")
    frontmatter = parts[1]
    body = parts[2].lstrip("\n")
    gap_section = (
        "## Unavailable payloads\n\n"
        + "\n".join(f"- {gap}" for gap in gaps)
        + "\n\n"
    )
    if "## Unavailable payloads\n" not in body:
        marker = next(
            (heading for heading in ("## Messages\n", "## Conversation\n") if heading in body),
            None,
        )
        if marker is None:
            raise ValueError("Rendered source is missing the conversation section")
        body = body.replace(marker, gap_section + marker, 1)
    normalized_body = body.rstrip() + "\n"
    content_hash = hashlib.sha256(normalized_body.encode("utf-8")).hexdigest()
    frontmatter = re.sub(
        r"(?m)^capture_fidelity:\s*.*$",
        "capture_fidelity: partial",
        frontmatter,
        count=1,
    )
    frontmatter = re.sub(
        r"(?m)^raw_status:\s*.*$",
        "raw_status: partial",
        frontmatter,
        count=1,
    )
    frontmatter = re.sub(
        r"(?m)^content_sha256:\s*.*$",
        f"content_sha256: {content_hash}",
        frontmatter,
        count=1,
    )
    updated = dict(rendered)
    updated.update(
        {
            "capture_fidelity": "partial",
            "content_sha256": content_hash,
            "content": f"---{frontmatter}---\n{normalized_body}",
        }
    )
    return updated


def repair_source_capture_metadata(
    existing: str | None,
    corrected: str,
    *,
    provider_id: str,
) -> str:
    """Reverse only a proven false unavailable-payload override.

    The immutable provider evidence is unchanged: the old body must become the
    freshly rendered native-export body by removing exactly one injected
    ``Unavailable payloads`` block, and the frontmatter may differ only in the
    fidelity/status/body-hash fields that the override itself changed.
    """
    if existing is None:
        raise RuntimeError("capture-metadata repair requested but source is missing")
    if not all(AUDIT.source_checks(existing, provider_id).values()):
        raise RuntimeError("Existing source contract failed before capture repair")
    if not all(AUDIT.source_checks(corrected, provider_id).values()):
        raise RuntimeError("Corrected source contract failed before capture repair")

    old_parts = existing.replace("\r\n", "\n").replace("\r", "\n").split("---", 2)
    new_parts = corrected.replace("\r\n", "\n").replace("\r", "\n").split("---", 2)
    if len(old_parts) != 3 or len(new_parts) != 3:
        raise RuntimeError("Capture repair requires YAML frontmatter")

    def stable_frontmatter(value: str) -> str:
        for field in ("capture_fidelity", "raw_status", "content_sha256"):
            value = re.sub(
                rf"(?m)^{field}:\s*.*$", f"{field}: <capture-repair>", value, count=1
            )
        return value

    if stable_frontmatter(old_parts[1]) != stable_frontmatter(new_parts[1]):
        raise RuntimeError("Capture repair would change unrelated source frontmatter")
    if AUDIT.field_value(existing, "capture_fidelity") != "partial":
        raise RuntimeError("Capture repair requires an existing partial source")
    if AUDIT.field_value(corrected, "capture_fidelity") != "export-native":
        raise RuntimeError("Capture repair requires a corrected export-native source")
    asset_count = int(AUDIT.field_value(corrected, "asset_count") or 0)
    archived_count = int(
        AUDIT.field_value(corrected, "archived_asset_count") or 0
    )
    if asset_count <= 0 or archived_count != asset_count:
        raise RuntimeError("Capture repair requires every referenced asset to be archived")

    old_body = old_parts[2].lstrip("\n")
    new_body = new_parts[2].lstrip("\n")
    repaired_body, removed = re.subn(
        r"(?ms)^## Unavailable payloads\n\n(?:- [^\n]*\n)+\n(?=## (?:Messages|Conversation)\n)",
        "",
        old_body,
        count=1,
    )
    if removed != 1 or repaired_body != new_body:
        raise RuntimeError("Capture repair would change immutable message evidence")
    return corrected


def reusable_existing_source(
    existing: str | None,
    *,
    provider_id: str,
    expected_message_count: int,
) -> dict[str, Any]:
    """Verify an immutable historical source before routing-only backfill."""
    if existing is None:
        raise RuntimeError("reuse_existing_source requested but source is missing")
    checks = AUDIT.source_checks(existing, provider_id)
    if not all(checks.values()):
        raise RuntimeError(f"Existing source contract failed: {checks}")
    actual_count = int(AUDIT.field_value(existing, "message_count") or -1)
    if actual_count != expected_message_count:
        raise RuntimeError(
            "Existing source/export message-count mismatch: "
            f"{actual_count} != {expected_message_count}"
        )
    return {
        "content": existing,
        "message_count": actual_count,
        "content_sha256": AUDIT.field_value(existing, "content_sha256"),
        "capture_fidelity": (
            AUDIT.field_value(existing, "capture_fidelity") or "partial"
        ),
        "checks": checks,
    }


# The bulk lane's own coarse classification vocabulary. A re-digest replaces
# these by design, so reporting them would fire on every single record and train
# the reader to ignore the field. Calibrated 2026-08-06 against all eight
# re-digest pairs in the corpus: with this set excluded the check is silent on
# seven and fires on exactly one — the real eviction (`emfieldcalc` off
# `02741e03`).
REDIGEST_CHURN_TAGS = frozenset({
    "transient",
    "engineering-concept",
    "engineering-tool",
    "software-dev",
})


def digest_tags(spec: dict[str, Any], provider: str) -> list[str]:
    """The digest's final tag list, deduplicated in order.

    Shared with the carryover check below so the two cannot disagree about what
    this digest is actually going to carry.
    """
    tags = [
        "conversation",
        "conversation-digest",
        provider,
        *[str(tag) for tag in spec.get("tags") or []],
    ]
    if spec.get("private") and "private" not in tags:
        tags.append("private")
    return list(dict.fromkeys(tags))


def is_sibling_segment(prior_digest: str | None, digest_path: str) -> bool:
    """True when the other digest is a co-segment, not a predecessor.

    One long conversation may be deliberately digested as two segments, both
    alive, each covering its own span and cross-linked to the other in
    ``related:`` (`a99580db` is the corpus's example). Their tag sets *should*
    differ, so comparing them would report an eviction forever — which is the
    standing complaint about any "two notes share a source id" detector.

    A superseded stub does not link forward: all three in the corpus name only
    the ledger, because a note written by an earlier pass cannot know the path
    of the one that will replace it. That asymmetry is the discriminator.
    """
    if not prior_digest or not digest_path:
        return False
    link = digest_path.removesuffix(".md")
    stem = link.rsplit("/", 1)[-1]
    fm = AUDIT.frontmatter(prior_digest)
    block = re.search(
        r"(?m)^related:[ \t]*\n(?P<items>(?:^[ \t]+-[^\r\n]*(?:\r?\n|$))+)", fm
    )
    if not block:
        return False
    return any(
        target in (link, stem)
        for target in re.findall(r"\[\[([^\]|#]+)", block.group("items"))
    )


def dropped_carryover_tags(prior_digest: str | None, new_tags: list[str]) -> list[str]:
    """Tags the predecessor carried that this digest drops, minus known churn.

    A re-digest rewrites ``tags:`` wholesale from the spec, so a durable marker
    survives only if the spec repeats it. That fails silently and in the
    direction that looks fine: the record reads *better* afterwards, and nothing
    reports that it has left the group it belonged to.

    Found 2026-08-06. `02741e03` — the 64-message record that found a 48 % error
    in a shipped exposure calculation, the largest conversation in its project —
    lost `emfieldcalc` when it was properly digested, and was invisible to a
    cluster scan until the tag was restored by hand. The signal degrades exactly
    as a backlog is worked, and the best-digested records are the most exposed.

    Advisory only. A dropped tag is often correct — the point is that it should
    be a decision rather than a side effect (`.claude/rules/audits.md`: an
    ambiguous signal must never gate).
    """
    if not prior_digest:
        return []
    was = _frontmatter_tags(AUDIT.frontmatter(prior_digest))
    if not was:
        return []
    keep = {str(t).strip() for t in new_tags}
    return sorted({t for t in was if t not in keep} - REDIGEST_CHURN_TAGS)


def _frontmatter_tags(fm: str) -> set[str]:
    """Tags from a frontmatter block, in either style.

    CLAUDE.md specifies block style, but 416 conversation notes in the corpus
    use the inline `tags: [a, b]` form, so reading only one style would make
    this check silently blind on exactly those.
    """
    block = re.search(
        r"(?m)^tags:[ \t]*\n(?P<items>(?:^[ \t]+-[^\r\n]*(?:\r?\n|$))+)", fm
    )
    if block:
        found = re.findall(r"^[ \t]+-[ \t]*(.+?)[ \t]*$", block.group("items"), re.M)
    else:
        inline = re.search(r"(?m)^tags:[ \t]*\[(?P<items>[^\]]*)\]", fm)
        if not inline:
            return set()
        found = inline.group("items").split(",")
    return {t.strip().strip("\"'") for t in found if t.strip().strip("\"'")}


def repair_source_digest_link(
    existing: str,
    *,
    provider_id: str,
    digest_path: str,
) -> str:
    """Relink source provenance without changing immutable message evidence.

    Historical Claude sources can point at a title-only digest that a later
    same-title conversation replaced. The message body and its recorded hash
    are immutable; only the ``related`` frontmatter link may change here.
    """
    checks = AUDIT.source_checks(existing, provider_id)
    if not all(checks.values()):
        raise RuntimeError(f"Existing source contract failed: {checks}")
    normalized = AUDIT.normalize_conversation_path(digest_path)
    if normalized != digest_path or "/originals/" in digest_path:
        raise ValueError(f"Unsafe digest repair path: {digest_path}")
    old_path = AUDIT.source_related_digest(existing)
    if old_path == digest_path:
        return existing
    if not old_path:
        raise RuntimeError("Existing source has no repairable related digest link")

    parts = existing.replace("\r\n", "\n").replace("\r", "\n").split("---", 2)
    if len(parts) != 3:
        raise RuntimeError("Existing source is missing YAML frontmatter")
    before_body = parts[2].lstrip("\n")
    old_link = old_path.removesuffix(".md")
    new_link = digest_path.removesuffix(".md")
    frontmatter = parts[1]
    if f"[[{old_link}]]" not in frontmatter:
        raise RuntimeError("Related digest link is not a plain repairable wikilink")
    frontmatter = frontmatter.replace(
        f"[[{old_link}]]", f"[[{new_link}]]", 1
    )
    repaired = f"---{frontmatter}---\n{before_body}"
    if AUDIT.body(repaired) != AUDIT.body(existing):
        raise RuntimeError("Source digest-link repair changed the immutable body")
    repaired_checks = AUDIT.source_checks(repaired, provider_id)
    if not all(repaired_checks.values()):
        raise RuntimeError(f"Repaired source contract failed: {repaired_checks}")
    return repaired


def fact_check_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return (
            "No material external factual claim required verification. "
            "The source and routing limitations were still reviewed."
        )
    lines = [
        "| Claim | Class | Check | Durable treatment | Source |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        source = str(row.get("source") or "-")
        if row.get("source_url"):
            source = f"[{source}]({row['source_url']})"
        lines.append(
            "| "
            + " | ".join(
                markdown_cell(value)
                for value in (
                    row.get("claim"),
                    row.get("class"),
                    row.get("check"),
                    row.get("durable_treatment"),
                    source,
                )
            )
            + " |"
        )
    return "\n".join(lines)


def derived_paths(spec: dict[str, Any]) -> list[str]:
    return [str(row["path"]) for row in spec.get("derived_mutations") or []]


def digest_markdown(
    spec: dict[str, Any],
    *,
    provider: str,
    provider_id: str,
    source_path: str,
    digest_path: str,
    source_url: str,
    message_count: int,
    capture_fidelity: str,
) -> str:
    paths = derived_paths(spec)
    tags = digest_tags(spec, provider)
    tag_lines = "\n".join(f"  - {tag}" for tag in tags)
    if paths:
        derived_frontmatter = "\n" + "\n".join(
            f'  - "[[{path.removesuffix(".md")}]]"' for path in paths
        )
    else:
        derived_frontmatter = " []"
    decisions = APPLY.bullet_list(
        [str(value) for value in spec.get("decisions") or []],
        empty="No durable decision.",
    )
    evidence_rows = [
        "| Derived note | Change | Evidence class | Reciprocal link |",
        "|---|---|---|---|",
    ]
    if paths:
        for row in spec.get("derived_mutations") or []:
            path = str(row["path"]).removesuffix(".md")
            evidence_rows.append(
                f"| [[{path}]] | {markdown_cell(row.get('change') or row.get('mode'))} "
                f"| {markdown_cell(row.get('claim_class') or 'verified')} "
                "| verified by atomic batch readback |"
            )
    else:
        evidence_rows.append(
            "| None | No living-note mutation; see routing disposition | "
            "not-a-factual-claim | n/a |"
        )
    source_summary = str(
        spec.get("source_summary")
        or f"Native {provider} export; {message_count} exported message nodes."
    ).strip()
    return f"""---
record_kind: conversation-digest
author: ai
provider: {provider}
source_conversation_id: {yaml_quote(provider_id)}
source_url: {yaml_quote(source_url)}
title: {yaml_quote(str(spec['title']))}
conversation_date: {spec['conversation_date']}
digested_at: {yaml_quote(str(spec['digested_at']))}
status: audited-complete
tags:
{tag_lines}
source_archive: "[[{source_path.removesuffix('.md')}]]"
derived_notes:{derived_frontmatter}
related:
  - "[[ai-conversation-ingestion-ledger]]"
---

## Digest

{str(spec['digest']).strip()}

## Domain coverage

{APPLY.coverage_table(spec)}

## Decisions and durable deltas

{decisions}

## Fact-check notes

{fact_check_table(spec.get('fact_checks') or [])}

## Routing

{APPLY.routing_table(spec, digest_path, paths)}

## Evidence chain

{chr(10).join(evidence_rows)}

- Provider conversation: {source_url}
- Immutable source: [[{source_path.removesuffix('.md')}]]
- Ledger: [[{LEDGER_PATH.removesuffix('.md')}]]

## Source

- {source_summary}
- Provider ID: `{provider_id}`
- Capture fidelity: `{capture_fidelity}`
- Message nodes: `{message_count}`
"""


def ledger_row(
    spec: dict[str, Any],
    *,
    provider: str,
    provider_id: str,
    source_path: str,
    digest_path: str,
    message_count: int,
    capture_fidelity: str,
) -> str:
    paths = derived_paths(spec)
    derived = (
        ", ".join(f"[[{path.removesuffix('.md')}]]" for path in paths)
        if paths
        else "accounted-no-mutation"
    )
    return (
        f"- {spec['ingestion_date']} · {provider}-native · {provider_id} · "
        f"{spec['title']} · complete · {capture_fidelity} · {message_count} messages · "
        f"[[{source_path.removesuffix('.md')}|source]] · "
        f"[[{digest_path.removesuffix('.md')}|digest]] · derived: {derived} · "
        "four-layer readback verified"
    )


def ledger_after(current: str, provider_id: str, row: str) -> tuple[str, str]:
    if row in current.splitlines():
        return current, "reused"
    return current.rstrip() + "\n\n" + row + "\n", (
        "appended-completion-receipt"
        if provider_id in current
        else "appended"
    )


def backup_path(path: str, provider_id: str, ingestion_date: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(path).stem).strip("-")[:80]
    key = hashlib.sha256(path.encode("utf-8")).hexdigest()[:10]
    return (
        "99_Attachments/temp-backup/"
        f"{stem}-{ingestion_date}-{provider_id[:8]}-{key}.md"
    )


def prospective_reader(
    base_url: str,
    headers: dict[str, str],
    overlays: dict[str, str],
) -> Any:
    return APPLY.prospective_reader(
        lambda path: APPLY.api_read(base_url, headers, path),
        overlays,
    )


def sniff_export_mime_type(content: bytes) -> str:
    """Identify provider binaries whose export filename lost its media type."""
    if (
        len(content) >= 12
        and content[:4] == b"RIFF"
        and content[8:12] == b"WAVE"
    ):
        return "audio/wav"
    return ""


def planned_chatgpt_assets(
    helper: Any,
    conversation: dict[str, Any],
    export_values: list[str],
    source_path: str,
) -> list[dict[str, Any]]:
    """Resolve provider-export binaries into deterministic Vault destinations."""
    root = str(Path(source_path).parent).replace("\\", "/")
    provider_id = str(
        conversation.get("conversation_id") or conversation.get("id") or ""
    ).lower()
    plans: list[dict[str, Any]] = []
    for index, record in enumerate(
        helper.conversation_asset_records(conversation), start=1
    ):
        if not record.get("recoverable_id"):
            continue
        identifier = str(record["provider_asset_id"])
        resolved = helper.read_export_asset(export_values, identifier)
        if resolved is None:
            continue
        content, export_member = resolved
        original_name = str(record.get("name") or "").strip()
        mime_type = str(record.get("mime_type") or "")
        if not mime_type or mime_type.endswith("/unknown"):
            detected_mime_type = sniff_export_mime_type(content)
            if detected_mime_type:
                mime_type = detected_mime_type
        if not original_name:
            suffix = mimetypes.guess_extension(
                mime_type
            ) or ".dat"
            original_name = f"{identifier}{suffix}"
        safe_name = re.sub(
            r"[^A-Za-z0-9._-]+", "-", Path(original_name).name
        ).strip("-")
        # A source note lists archived asset paths in YAML frontmatter.  Names
        # such as ``"260610 - P530678"`` otherwise sanitize to ``260610---P530678``;
        # the generic evidence reader then mistakes that run for the closing
        # frontmatter delimiter and hashes the wrong body.
        safe_name = re.sub(r"-{2,}", "-", safe_name)
        if not safe_name:
            safe_name = f"{identifier}.dat"
        path = (
            f"{root}/assets/{provider_id[:8]}/"
            f"{index:03d}-{safe_name}"
        )
        plans.append(
            {
                "provider_asset_id": identifier,
                "path": path,
                "name": original_name,
                "mime_type": mime_type,
                "size": len(content),
                "content_sha256": hashlib.sha256(content).hexdigest(),
                "export_member": export_member,
                "content": content,
            }
        )
    return plans


def api_read_bytes(
    base_url: str, headers: dict[str, str], path: str
) -> bytes | None:
    url = f"{base_url}/api/vault/file?" + urllib.parse.urlencode({"path": path})
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def write_bytes_exact(
    base_url: str,
    headers: dict[str, str],
    plan: dict[str, Any],
) -> str:
    """Write one immutable binary through EmptyOS and verify byte readback."""
    path = str(plan["path"])
    content = bytes(plan["content"])
    expected_hash = str(plan["content_sha256"])
    current = api_read_bytes(base_url, headers, path)
    if current is not None:
        current_hash = hashlib.sha256(current).hexdigest()
        if current_hash != expected_hash:
            raise RuntimeError(f"Immutable binary conflict at {path}")
        return "reused"
    payload = json.dumps(
        {
            "path": path,
            "content_base64": base64.b64encode(content).decode("ascii"),
            "content_sha256": expected_hash,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/api/vault/write-bytes",
        data=payload,
        method="POST",
        headers={**headers, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.load(response)
    if not result.get("ok"):
        raise RuntimeError(f"Binary Vault write failed for {path}: {result}")
    readback = api_read_bytes(base_url, headers, path)
    if readback is None or hashlib.sha256(readback).hexdigest() != expected_hash:
        raise RuntimeError(f"Binary Vault readback mismatch for {path}")
    return str(result.get("status") or "written")


def apply_item(
    *,
    provider: str,
    export_values: list[str],
    helper: Any,
    base_url: str,
    headers: dict[str, str],
    spec: dict[str, Any],
) -> dict[str, Any]:
    provider_id = str(spec["provider_id"]).lower()
    conversation = helper.find_conversation(export_values, provider_id)
    actual_title = str(
        conversation.get("title") or conversation.get("name") or "Untitled"
    )
    if actual_title != str(spec["title"]):
        raise ValueError(
            f"Title mismatch for {provider_id}: {actual_title!r} != {spec['title']!r}"
        )
    rendered = helper.render_source(
        conversation,
        captured_at=str(spec["captured_at"]),
        force_private=bool(spec.get("private")),
    )
    asset_plans: list[dict[str, Any]] = []
    if provider == "chatgpt":
        asset_plans = planned_chatgpt_assets(
            helper,
            conversation,
            export_values,
            str(rendered["path"]),
        )
        render_assets = [
            {key: value for key, value in plan.items() if key != "content"}
            for plan in asset_plans
        ]
        rendered = helper.render_source(
            conversation,
            captured_at=str(spec["captured_at"]),
            force_private=bool(spec.get("private")),
            archived_assets=render_assets,
        )
    rendered = apply_source_gap_overrides(rendered, spec)
    source_path_override = spec.get("source_path_override")
    if source_path_override:
        override = str(source_path_override).replace("\\", "/")
        expected_root = f"40_Archive/AI Conversations/originals/{provider}/"
        expected_suffix = f"--{provider_id[:8]}.md"
        if (
            not override.startswith(expected_root)
            or not override.endswith(expected_suffix)
            or ".." in Path(override).parts
        ):
            raise ValueError(
                "source_path_override must stay in the provider originals root "
                "and retain the provider-ID suffix"
            )
        rendered = {**rendered, "path": override}
    source_path = str(rendered["path"])
    digest_path = str(rendered["digest_path"])
    current_source = APPLY.api_read(base_url, headers, source_path)
    source_metadata_repair = False
    superseded_digest_path: str | None = None
    if spec.get("reuse_existing_source") and spec.get("repair_source_capture_metadata"):
        raise ValueError("Source reuse and capture-metadata repair are mutually exclusive")
    if spec.get("reuse_existing_source"):
        selected_source = reusable_existing_source(
            current_source,
            provider_id=provider_id,
            expected_message_count=int(rendered["message_count"]),
        )
        source = str(selected_source["content"])
        message_count = int(selected_source["message_count"])
        source_hash = str(selected_source["content_sha256"])
        capture_fidelity = str(selected_source["capture_fidelity"])
        if spec.get("repair_source_digest_link"):
            # Captured before the repair rewrites the link: for a legacy-path
            # record the predecessor is the digest the source still points at,
            # not the (absent) note at the new suffixed path.
            superseded_digest_path = AUDIT.source_related_digest(source)
            source = repair_source_digest_link(
                source,
                provider_id=provider_id,
                digest_path=digest_path,
            )
            source_metadata_repair = source != current_source
    elif spec.get("repair_source_capture_metadata"):
        source = repair_source_capture_metadata(
            current_source,
            str(rendered["content"]),
            provider_id=provider_id,
        )
        message_count = int(rendered["message_count"])
        source_hash = str(rendered["content_sha256"])
        capture_fidelity = str(rendered["capture_fidelity"])
        source_metadata_repair = source != current_source
    else:
        source = str(rendered["content"])
        message_count = int(rendered["message_count"])
        source_hash = str(rendered["content_sha256"])
        capture_fidelity = str(rendered["capture_fidelity"])
    source_url = (
        f"https://chatgpt.com/c/{provider_id}"
        if provider == "chatgpt"
        else f"https://claude.ai/chat/{provider_id}"
    )
    digest = digest_markdown(
        spec,
        provider=provider,
        provider_id=provider_id,
        source_path=source_path,
        digest_path=digest_path,
        source_url=source_url,
        message_count=message_count,
        capture_fidelity=capture_fidelity,
    )
    source_checks = AUDIT.source_checks(source, provider_id)
    if not all(source_checks.values()):
        raise RuntimeError(f"Source contract failed: {source_checks}")

    derived_plans = APPLY.plan_derived_mutations(
        base_url=base_url,
        headers=headers,
        spec=spec,
        provider_id=provider_id,
        source_path=source_path,
        digest_path=digest_path,
    )
    if current_source not in {None, source} and not source_metadata_repair:
        raise RuntimeError(f"Immutable source conflict at {source_path}")
    current_digest = APPLY.api_read(base_url, headers, digest_path)
    # In-place re-digest supersedes the note at this path; a legacy-path record
    # supersedes the one the source pointed at before the link was repaired.
    predecessor = current_digest
    if predecessor is None and superseded_digest_path:
        predecessor = APPLY.api_read(base_url, headers, superseded_digest_path)
    if is_sibling_segment(predecessor, digest_path):
        predecessor = None
    carryover_dropped = dropped_carryover_tags(
        predecessor, digest_tags(spec, provider)
    )
    if carryover_dropped:
        # stderr, not the envelope: a nested JSON key is easy to miss, and the
        # whole failure mode here is that nobody notices (`agent-cli.md` keeps
        # stdout to the one envelope object and diagnostics on stderr).
        print(
            f"warning: {provider_id[:8]} drops tag(s) its predecessor carried: "
            f"{', '.join(carryover_dropped)}. Repeat them in the spec if the "
            f"record still belongs to that group.",
            file=sys.stderr,
        )
    current_ledger = APPLY.api_read(base_url, headers, LEDGER_PATH)
    if current_ledger is None:
        raise RuntimeError("Ingestion ledger is missing")
    final_row = ledger_row(
        spec,
        provider=provider,
        provider_id=provider_id,
        source_path=source_path,
        digest_path=digest_path,
        message_count=message_count,
        capture_fidelity=capture_fidelity,
    )
    next_ledger, ledger_status = ledger_after(
        current_ledger, provider_id, final_row
    )
    overlays = {
        source_path: source,
        digest_path: digest,
        LEDGER_PATH: next_ledger,
        **{plan["path"]: plan["after"] for plan in derived_plans},
    }
    index = AUDIT.ledger_note_candidates(next_ledger, helper)
    audit_item = {
        "provider_id": provider_id,
        "provider": provider,
        "title": spec["title"],
        "created_at": spec["conversation_date"],
        "message_count": message_count,
        "attachment_count": int(
            helper.inventory_item(conversation, 0, 0).get("attachment_count") or 0
        ),
        "file_count": int(
            helper.inventory_item(conversation, 0, 0).get("file_count") or 0
        ),
    }
    prospective = AUDIT.audit_item(
        audit_item,
        prospective_reader(base_url, headers, overlays),
        helper,
        index.get(provider_id),
        ledger_entry_present=True,
    )
    if not prospective["new_schema_complete"]:
        raise APPLY.ApplyRefused("prospective evidence graph is not clean", prospective)

    backups: list[dict[str, str]] = []
    if source_metadata_repair and current_source is not None:
        target = backup_path(
            source_path, provider_id, str(spec["ingestion_date"])
        )
        backups.append(
            {
                "path": target,
                "status": APPLY.write_exact(
                    base_url, headers, target, current_source
                ),
            }
        )
    if current_digest is not None and current_digest != digest:
        target = backup_path(
            digest_path, provider_id, str(spec["ingestion_date"])
        )
        backups.append(
            {
                "path": target,
                "status": APPLY.write_exact(
                    base_url, headers, target, current_digest
                ),
            }
        )
    for plan in derived_plans:
        requested = plan.get("backup_path")
        if not requested or plan["before"] == plan["after"]:
            continue
        if plan["before"] is None:
            raise RuntimeError(f"Backup requested for new note: {plan['path']}")
        backups.append(
            {
                "path": str(requested),
                "status": APPLY.write_exact(
                    base_url, headers, str(requested), str(plan["before"])
                ),
            }
        )

    asset_receipts = [
        {
            "path": str(plan["path"]),
            "provider_asset_id": str(plan["provider_asset_id"]),
            "content_sha256": str(plan["content_sha256"]),
            "size": int(plan["size"]),
            "status": write_bytes_exact(base_url, headers, plan),
        }
        for plan in asset_plans
    ]
    source_status = (
        APPLY.write_update_exact(
            base_url,
            headers,
            source_path,
            current_source,
            source,
        )
        if source_metadata_repair
        else APPLY.write_exact(base_url, headers, source_path, source)
    )
    derived_receipts: list[dict[str, str]] = []
    for plan in derived_plans:
        derived_receipts.append(
            {
                "path": str(plan["path"]),
                "status": APPLY.write_update_exact(
                    base_url,
                    headers,
                    str(plan["path"]),
                    plan["before"],
                    str(plan["after"]),
                ),
            }
        )
    digest_status = APPLY.write_update_exact(
        base_url,
        headers,
        digest_path,
        current_digest,
        digest,
    )
    if next_ledger != current_ledger:
        APPLY.api_write(base_url, headers, LEDGER_PATH, next_ledger)
    if APPLY.api_read(base_url, headers, LEDGER_PATH) != next_ledger:
        raise RuntimeError(f"Ledger readback failed for {provider_id}")

    final = AUDIT.audit_item(
        audit_item,
        lambda path: (
            value
            if (value := APPLY.api_read(base_url, headers, path)) is not None
            else (_ for _ in ()).throw(FileNotFoundError(path))
        ),
        helper,
        AUDIT.ledger_note_candidates(next_ledger, helper).get(provider_id),
        ledger_entry_present=True,
    )
    if not final["new_schema_complete"]:
        raise APPLY.ApplyRefused("post-write readback audit is not clean", final)
    return {
        "provider_id": provider_id,
        "source": {
            "status": source_status,
            "path": source_path,
            "message_count": message_count,
            "hash": source_hash,
            "readback": "verified",
            "assets": asset_receipts,
        },
        "digest": {
            "status": digest_status,
            "path": digest_path,
            "provider_id_readback": "verified",
            # Present only when the predecessor carried a non-churn tag this
            # spec does not repeat. Advisory: often correct, never a gate.
            **(
                {"dropped_carryover_tags": carryover_dropped}
                if carryover_dropped else {}
            ),
        },
        "derived": derived_receipts or {
            "status": "accounted-no-mutation",
            "dispositions": spec.get("delta_dispositions") or [],
        },
        "ledger": {
            "status": ledger_status,
            "path": LEDGER_PATH,
            "provider_id_readback": "verified",
        },
        "backups": backups,
        "new_schema_complete": True,
    }


def load_specs(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    rows = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("Spec must be an array or an object with an items array")
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("claude", "chatgpt"), required=True)
    parser.add_argument("--export", action="append", required=True)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--provider-id", action="append", dest="provider_ids")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return APPLY.run_cli(lambda: _collect_receipts(args))


def _collect_receipts(args: argparse.Namespace) -> list:
    rows = load_specs(args.spec)
    if args.provider_ids:
        selected = {value.lower() for value in args.provider_ids}
        rows = [
            row for row in rows if str(row.get("provider_id") or "").lower() in selected
        ]
        found = {str(row["provider_id"]).lower() for row in rows}
        if selected - found:
            raise ValueError(f"Missing specs: {sorted(selected - found)}")
    helper = helper_for(args.provider)
    base_url, headers = APPLY.connection(args.config)
    receipts = [
        apply_item(
            provider=args.provider,
            export_values=args.export,
            helper=helper,
            base_url=base_url,
            headers=headers,
            spec=row,
        )
        for row in rows
    ]
    return receipts


if __name__ == "__main__":
    raise SystemExit(main())
