#!/usr/bin/env python3
"""Read-only audit of an EmptyOS AI-conversation archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


CONTROL_STEMS = {
    "ai-conversation-ingestion-ledger",
    "ai-conversation-ingestion-sop",
    "ai-conversation-index",
}
ROLE_RE = re.compile(
    r"(?mi)^###\s+(?:\d+\s*[·.-]\s*)?"
    r"(User|Human|Claude|ChatGPT|Gemini|Codex|Assistant|AI)\b"
)
FACT_CHECK_RE = re.compile(r"(?mi)^##\s+Fact-check notes\b")
SOURCE_ID_PATTERNS = (
    re.compile(r"claude\.ai/chat/([0-9a-f-]{20,})", re.I),
    re.compile(r"chatgpt\.com/(?:c|share)/([0-9a-f-]{20,})", re.I),
    re.compile(r"gemini\.google\.com/app/([^/?#\s]+)", re.I),
)


@dataclass
class Entry:
    path: str
    title: str
    provider: str
    source_id: str
    source_url: str
    record_kind: str
    archive_status: str
    capture_fidelity: str
    message_count_declared: int
    message_count_observed: int
    hash_status: str
    fact_check: bool
    private: bool
    chars: int


def normalize_body(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def split_frontmatter(text: str) -> tuple[str, str]:
    normalized = normalize_body(text)
    if not normalized.startswith("---\n"):
        return "", normalized
    end = normalized.find("\n---\n", 4)
    if end < 0:
        return "", normalized
    body = normalized[end + 5 :]
    # Standard Obsidian Markdown leaves one blank line between the closing
    # frontmatter fence and the first body heading. The archive hash contract
    # starts at that heading, so remove exactly that separator newline.
    if body.startswith("\n"):
        body = body[1:]
    return normalized[4:end], body


def scalar(frontmatter: str, key: str) -> str:
    match = re.search(
        rf"(?mi)^{re.escape(key)}:\s*(?:\"([^\"]*)\"|'([^']*)'|([^\n#]*))",
        frontmatter,
    )
    if not match:
        return ""
    return next((part for part in match.groups() if part is not None), "").strip()


def integer(frontmatter: str, key: str) -> int:
    value = scalar(frontmatter, key)
    try:
        return int(value)
    except ValueError:
        return 0


def infer_source_id(frontmatter: str, text: str) -> str:
    for key in ("source_conversation_id", "source_id", "conversation_id"):
        value = scalar(frontmatter, key)
        if value:
            return value
    for pattern in SOURCE_ID_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return ""


def infer_provider(frontmatter: str, source_url: str) -> str:
    raw = (
        scalar(frontmatter, "provider")
        or scalar(frontmatter, "source")
        or "unknown"
    ).lower()
    if "claude-code" in raw:
        return "claude-code"
    if "codex" in raw:
        return "codex"
    if "claude" in raw or "claude.ai" in source_url:
        return "claude"
    if "chatgpt" in raw or "openai" in raw or "chatgpt.com" in source_url:
        return "chatgpt"
    if "gemini" in raw or "gemini.google.com" in source_url:
        return "gemini"
    return raw


def tags_contain_private(frontmatter: str) -> bool:
    match = re.search(
        r"(?ms)^tags:\s*(.*?)(?=^[A-Za-z_][A-Za-z0-9_-]*:\s*|\Z)",
        frontmatter,
    )
    return bool(match and re.search(r"(?i)\bprivate\b", match.group(1)))


def classify(path: Path, root: Path) -> Entry | None:
    if path.stem in CONTROL_STEMS or path.stem.startswith(
        "ai-conversation-legacy-audit-"
    ):
        return None

    text = path.read_text(encoding="utf-8-sig", errors="replace")
    frontmatter, body = split_frontmatter(text)
    source_url = scalar(frontmatter, "source_url") or scalar(frontmatter, "url")
    source_id = infer_source_id(frontmatter, text)
    provider = infer_provider(frontmatter, source_url)
    declared_kind = scalar(frontmatter, "record_kind").lower()
    path_parts = {part.lower() for part in path.parts}
    looks_source = (
        declared_kind == "conversation-source"
        or "originals" in path_parts
        or path.name.lower().endswith(".raw.md")
    )

    roles = ROLE_RE.findall(body)
    observed = len(roles)
    user_turns = sum(role.lower() in {"user", "human"} for role in roles)
    assistant_turns = observed - user_turns
    declared = integer(frontmatter, "message_count")
    fidelity = scalar(frontmatter, "capture_fidelity")
    expected_hash = scalar(frontmatter, "content_sha256").lower()
    actual_hash = hashlib.sha256(normalize_body(body).encode("utf-8")).hexdigest()

    if expected_hash:
        hash_status = "verified" if expected_hash == actual_hash else "mismatch"
    else:
        hash_status = "missing"

    raw_status = scalar(frontmatter, "raw_status").lower()
    if looks_source:
        record_kind = "source"
        identity_and_integrity = (
            declared > 0
            and declared == observed
            and hash_status == "verified"
            and bool(source_id)
            and bool(source_url)
        )
        complete = (
            identity_and_integrity
            and raw_status == "complete"
            and fidelity in {"export-native", "verbatim-text"}
        )
        partial = (
            identity_and_integrity
            and raw_status == "partial"
            and fidelity == "partial"
        )
        if complete:
            archive_status = "complete"
        elif partial:
            archive_status = "partial"
        else:
            archive_status = "source-contract-failed"
    elif user_turns >= 2 and assistant_turns >= 2:
        record_kind = "legacy-transcript"
        archive_status = "legacy-unverified"
    else:
        record_kind = "digest"
        archive_status = "digest-only"

    return Entry(
        path=path.relative_to(root).as_posix(),
        title=scalar(frontmatter, "title") or path.stem,
        provider=provider,
        source_id=source_id,
        source_url=source_url,
        record_kind=record_kind,
        archive_status=archive_status,
        capture_fidelity=fidelity or "unspecified",
        message_count_declared=declared,
        message_count_observed=observed,
        hash_status=hash_status,
        fact_check=bool(FACT_CHECK_RE.search(body)),
        private=tags_contain_private(frontmatter),
        chars=len(text),
    )


def audit(root: Path) -> dict:
    entries = [
        entry
        for path in sorted(root.rglob("*.md"))
        if (entry := classify(path, root)) is not None
    ]
    identity_groups: dict[str, list[str]] = defaultdict(list)
    for entry in entries:
        if entry.source_id:
            identity_groups[f"{entry.provider}:{entry.source_id}"].append(entry.path)

    duplicate_identities = {
        key: paths for key, paths in identity_groups.items() if len(paths) > 1
    }
    return {
        "root": root.as_posix(),
        "counts": {
            "notes": len(entries),
            "providers": dict(sorted(Counter(e.provider for e in entries).items())),
            "record_kinds": dict(
                sorted(Counter(e.record_kind for e in entries).items())
            ),
            "archive_status": dict(
                sorted(Counter(e.archive_status for e in entries).items())
            ),
            "fact_checked": sum(
                e.fact_check for e in entries if e.record_kind != "source"
            ),
            "missing_fact_check": sum(
                not e.fact_check for e in entries if e.record_kind != "source"
            ),
            "with_source_id": sum(bool(e.source_id) for e in entries),
            "with_source_url": sum(bool(e.source_url) for e in entries),
            "complete_sources": sum(e.archive_status == "complete" for e in entries),
            "partial_sources": sum(e.archive_status == "partial" for e in entries),
        },
        "duplicate_identities": duplicate_identities,
        "entries": [asdict(entry) for entry in entries],
    }


def esc(value: object) -> str:
    return str(value).replace("|", r"\|").replace("\n", " ")


def render_markdown(report: dict) -> str:
    counts = report["counts"]
    lines = [
        "# AI conversation legacy audit",
        "",
        "> Read-only classification. A legacy summary is never treated as a raw original.",
        "",
        "## Summary",
        "",
        f"- Conversation notes: **{counts['notes']}**",
        f"- Provider mix: **{', '.join(f'{k} {v}' for k, v in counts['providers'].items())}**",
        f"- Contract-complete source archives: **{counts['complete_sources']}**",
        f"- Integrity-verified partial source archives: **{counts['partial_sources']}**",
        f"- Legacy transcript-like notes: **{counts['record_kinds'].get('legacy-transcript', 0)}**",
        f"- Digest-only notes: **{counts['record_kinds'].get('digest', 0)}**",
        f"- Notes with fact-check sections: **{counts['fact_checked']}**",
        f"- Notes missing fact-check sections: **{counts['missing_fact_check']}**",
        f"- Notes with recoverable provider IDs: **{counts['with_source_id']}**",
        f"- Notes with canonical URLs: **{counts['with_source_url']}**",
        "",
        "## Migration policy",
        "",
        "- Keep every legacy note in place; do not rename or rewrite it in bulk.",
        "- Backfill a new source archive only from the live provider chat or a provider export.",
        "- Link a backfilled source archive to the existing digest.",
        "- Record unavailable sources as `source-unavailable`; do not reconstruct them.",
        "- Review duplicate identities manually; never auto-delete vault files.",
        "",
        "## Inventory",
        "",
        "| Note | Provider | Kind | Archive status | Source ID | Fact-check | Messages | Hash |",
        "|---|---|---|---|---|---:|---:|---|",
    ]
    for entry in report["entries"]:
        source_id = entry["source_id"] or "—"
        message_count = (
            entry["message_count_declared"]
            or entry["message_count_observed"]
            or "—"
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    f"[[{Path(entry['path']).stem}]]",
                    esc(entry["provider"]),
                    esc(entry["record_kind"]),
                    esc(entry["archive_status"]),
                    esc(source_id),
                    (
                        "n/a"
                        if entry["record_kind"] == "source"
                        else ("yes" if entry["fact_check"] else "no")
                    ),
                    esc(message_count),
                    esc(entry["hash_status"]),
                ]
            )
            + " |"
        )

    lines.extend(["", "## Missing fact-check queue", ""])
    missing = [
        e
        for e in report["entries"]
        if e["record_kind"] != "source" and not e["fact_check"]
    ]
    if missing:
        lines.extend(f"- [[{Path(e['path']).stem}]]" for e in missing)
    else:
        lines.append("- None")

    lines.extend(["", "## Duplicate provider identities", ""])
    if report["duplicate_identities"]:
        for identity, paths in sorted(report["duplicate_identities"].items()):
            lines.append(f"- `{identity}`: " + ", ".join(f"`{p}`" for p in paths))
    else:
        lines.append("- None detected")

    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.root.is_dir():
        raise SystemExit(f"archive root does not exist: {args.root}")
    report = audit(args.root)
    if args.format == "markdown":
        print(render_markdown(report))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
