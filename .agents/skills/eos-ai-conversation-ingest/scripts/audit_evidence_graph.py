#!/usr/bin/env python3
"""Audit conversation source/digest/evidence chains through the EmptyOS API."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable


REQUIRED_DIGEST_HEADINGS = (
    "## Digest",
    "## Domain coverage",
    "## Decisions and durable deltas",
    "## Fact-check notes",
    "## Routing",
    "## Evidence chain",
    "## Source",
)

DOMAIN_LABELS = (
    "Personal chronology",
    "Emotional and health",
    "People and relationships",
    "Work and career",
    "Projects and execution",
    "Tasks and commitments",
    "Knowledge fragments",
    "Calculations and engineering",
    "Tools, code, and calculators",
    "Finance and assets",
    "Legal, immigration, and administration",
    "Media and creative work",
    "Preferences and operating principles",
    "Open questions and contradictions",
    "Transient/no durable delta",
)

DOMAIN_STATUSES = {
    "delta",
    "mentioned-no-delta",
    "not-present",
    "needs-review",
}

NO_MUTATION_DISPOSITIONS = {
    "digest-contained",
    "reused-no-change",
    "duplicate-of",
    "deferred-unverified",
}

ROUTING_EVIDENCE_RE = re.compile(
    r"(?is)\bqueries:\s*(?P<queries>.+?)\s*[·|]\s*"
    r"checked:\s*(?P<checked>.+?)\s*$"
)

GENERIC_NO_MUTATION_REASON_MARKERS = {
    "the legacy run preserved this delta",
    "left no reciprocal evidence of a living-note mutation",
    "this backfill does not invent one",
    "routed to the verified source archive and this audited digest",
    "no separate living-note update was made unless the digest explicitly records one",
}

SOURCE_FIELDS = (
    "record_kind: conversation-source",
    "archive_schema: eos-ai-conversation-v1",
    "author: both",
    "source_conversation_id:",
    "source_url:",
    "capture_fidelity:",
    "raw_status:",
    "message_count:",
    "content_sha256:",
)


def load_queue_helper(provider: str = "claude") -> Any:
    helper_name = (
        "chatgpt_export_queue.py"
        if provider == "chatgpt"
        else "claude_export_queue.py"
    )
    helper_path = Path(__file__).with_name(helper_name)
    spec = importlib.util.spec_from_file_location(
        f"{provider}_export_queue", helper_path
    )
    if not spec or not spec.loader:
        raise RuntimeError(f"Cannot load queue helper: {helper_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def optional_read(
    reader: Callable[[str], str], path: str
) -> str | None:
    try:
        return reader(path)
    except FileNotFoundError:
        # Direct-vault mode must match the API reader's 404 semantics so
        # first_existing() can continue through legacy path candidates.
        return None
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def _split_frontmatter(text: str) -> tuple[str, str] | None:
    """Return (frontmatter_block, body) or None when there is no frontmatter.

    LINE-ANCHORED. `text.split("---", 2)` is not: a `---` appearing anywhere
    inside a YAML *value* — an asset filename, a title, an alias — terminates the
    block early, and every downstream reader then hashes the wrong body. That was
    a live defect here, and it was patched by sanitizing one input so it could
    not contain `---`, which left the parser wrong for every other input.

    This mirrors `emptyos/frontmatter.py::fm_end`, which exists in the main tree
    for exactly this reason. These skill scripts are standalone and cannot import
    it, so the rule is restated rather than shared — but it is the same rule, and
    both public helpers below go through this one function, so the parser and the
    body splitter cannot disagree about where the block ends.
    """
    norm = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = norm.split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1:])
    return None


def frontmatter(text: str) -> str:
    split = _split_frontmatter(text)
    return split[0] if split else ""


def body(text: str) -> str:
    split = _split_frontmatter(text)
    if split is not None:
        return split[1].lstrip("\n")

    norm = text.replace("\r\n", "\n").replace("\r", "\n")
    # `_split_frontmatter` returns None for two different documents: one with no
    # opening fence at all, and one that opens a fence and never closes it. Only
    # the second has no trustworthy body. Distinguish them with the SAME opening
    # test the splitter uses — an earlier version asked `startswith("---")`,
    # which is also true of a document opening with a `----` horizontal rule, and
    # returned "" for it. That is content loss on a document that simply has no
    # frontmatter, and it feeds the content hash.
    first = norm.split("\n", 1)[0]
    return "" if first.strip() == "---" else norm


def field_value(text: str, field: str) -> str:
    match = re.search(
        rf"(?m)^{re.escape(field)}:\s*[\"']?(.*?)[\"']?\s*$", frontmatter(text)
    )
    return match.group(1).strip().strip("\"'") if match else ""


def derived_links(text: str) -> list[str]:
    fm = frontmatter(text)
    block = re.search(
        r"(?m)^derived_notes:[ \t]*(?:\[\][ \t]*$|"
        r"\n(?P<items>(?:^[ \t]+-[^\r\n]*(?:\r?\n|$))+))",
        fm,
    )
    if not block or not block.group("items"):
        return []
    links: list[str] = []
    for match in re.finditer(r"\[\[([^\]|#]+)", block.group("items")):
        path = match.group(1).strip()
        if path and path not in links:
            links.append(path)
    return links


def markdown_section(text: str, heading: str) -> str:
    """Return one level-two Markdown section without the heading."""
    marker = f"## {heading}"
    if marker not in text:
        return ""
    return text.partition(marker)[2].lstrip("\r\n").partition("\n## ")[0].strip()


def markdown_table_cells(line: str) -> list[str]:
    """Split a Markdown table row without breaking wikilink aliases."""
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return []
    protected: list[str] = []

    def preserve_wikilink(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return f"\x00{len(protected) - 1}\x00"

    safe = re.sub(r"\[\[[^\]]+\]\]", preserve_wikilink, stripped[1:-1])
    cells = [cell.strip() for cell in safe.split("|")]
    for index, cell in enumerate(cells):
        for protected_index, value in enumerate(protected):
            cell = cell.replace(f"\x00{protected_index}\x00", value)
        cells[index] = cell
    return cells


def domain_statuses(text: str) -> dict[str, str]:
    """Return exact domain -> status rows from the coverage table."""
    statuses: dict[str, str] = {}
    for line in markdown_section(text, "Domain coverage").splitlines():
        cells = markdown_table_cells(line)
        if (
            len(cells) >= 2
            and cells[0] in DOMAIN_LABELS
            and cells[1] in DOMAIN_STATUSES
        ):
            statuses[cells[0]] = cells[1]
    return statuses


def delta_dispositions(text: str) -> list[dict[str, str]]:
    """Parse per-domain no-mutation dispositions from the dedicated table."""
    rows: list[dict[str, str]] = []
    section = markdown_section(text, "Delta disposition") or markdown_section(
        text, "Routing"
    )
    for line in section.splitlines():
        cells = markdown_table_cells(line)
        if len(cells) < 2 or cells[0] not in DOMAIN_LABELS:
            continue
        rows.append(
            {
                "domain": cells[0],
                "disposition": cells[1].strip().lower(),
                "target": cells[2].strip() if len(cells) >= 3 else "",
                "reason": cells[3].strip() if len(cells) >= 4 else "",
                "routing_evidence": cells[4].strip() if len(cells) >= 5 else "",
            }
        )
    return rows


def valid_digest_contained_routing_evidence(value: str) -> bool:
    """Require query terms plus concrete candidate-note comparison evidence."""
    match = ROUTING_EVIDENCE_RE.search(value.strip())
    if not match:
        return False
    queries = match.group("queries").strip()
    checked = match.group("checked").strip()
    if not queries or queries in {"-", "—"}:
        return False
    if not checked or checked in {"-", "—"}:
        return False
    return bool(first_wikilink(checked)) or checked.lower() == "no candidate found"


def first_wikilink(text: str) -> str:
    match = re.search(r"\[\[([^\]|#]+)", text)
    return match.group(1).strip() if match else ""


def assess_no_mutation_routing(
    text: str,
    *,
    has_derived_notes: bool,
) -> tuple[dict[str, str], list[dict[str, str]], dict[str, Any], list[str]]:
    """Prove that every durable delta was routed or explicitly accounted for.

    A generic paragraph is sufficient only when the scan found no non-transient
    delta. If a digest claims a durable delta but mutates no living note, each
    such domain needs a structured disposition. Unverified or needs-review
    content remains incomplete instead of being hidden behind ``skipped``.
    """
    statuses = domain_statuses(text)
    dispositions = delta_dispositions(text)
    disposition_by_domain = {
        row["domain"]: row for row in dispositions if row["domain"] in statuses
    }
    durable_deltas = [
        domain
        for domain, status in statuses.items()
        if status == "delta" and domain != "Transient/no durable delta"
    ]
    needs_review = [
        domain for domain, status in statuses.items() if status == "needs-review"
    ]
    reasons: list[str] = []

    if not has_derived_notes:
        for domain in needs_review:
            reasons.append("needs-review-unresolved:" + domain)
        for domain in durable_deltas:
            row = disposition_by_domain.get(domain)
            if row is None:
                reasons.append("delta-disposition-missing:" + domain)
                continue
            disposition = row["disposition"]
            if disposition not in NO_MUTATION_DISPOSITIONS:
                reasons.append("delta-disposition-invalid:" + domain)
                continue
            if not row["reason"] or row["reason"] in {"-", "—"}:
                reasons.append("delta-disposition-reason-missing:" + domain)
            normalized_reason = re.sub(
                r"\s+", " ", row["reason"].strip().lower()
            )
            if any(
                marker in normalized_reason
                for marker in GENERIC_NO_MUTATION_REASON_MARKERS
            ):
                reasons.append("delta-disposition-generic:" + domain)
            target = first_wikilink(row["target"])
            if disposition in {"reused-no-change", "duplicate-of"} and not target:
                reasons.append("delta-disposition-target-missing:" + domain)
            if disposition == "duplicate-of" and target:
                normalized = normalize_conversation_path(target)
                if normalized is None:
                    reasons.append("delta-duplicate-target-invalid:" + domain)
            if (
                disposition == "digest-contained"
                and not valid_digest_contained_routing_evidence(
                    row.get("routing_evidence", "")
                )
            ):
                reasons.append(
                    "delta-digest-contained-routing-evidence-missing:" + domain
                )
            if disposition == "deferred-unverified":
                reasons.append("delta-deferred-unverified:" + domain)

    routing_review_required = bool(
        not has_derived_notes
        and (
            needs_review
            or any(reason.startswith("delta-") for reason in reasons)
        )
    )
    routing_checks = {
        "durable_delta_count": len(durable_deltas),
        "needs_review_count": len(needs_review),
        "delta_dispositions_required": bool(durable_deltas)
        and not has_derived_notes,
        "delta_dispositions_verified": not any(
            reason.startswith("delta-") for reason in reasons
        ),
        "needs_review_resolved": not needs_review,
        "routing_search_evidence_required": any(
            row.get("disposition") == "digest-contained"
            for row in dispositions
        )
        and not has_derived_notes,
        "routing_search_evidence_verified": not any(
            reason.startswith(
                "delta-digest-contained-routing-evidence-missing:"
            )
            for reason in reasons
        ),
        "routing_review_required": routing_review_required,
    }
    return statuses, dispositions, routing_checks, reasons


def compact_markdown(text: str, limit: int = 480) -> str:
    """Flatten a short Markdown explanation for a telemetry receipt."""
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or re.fullmatch(r"\|?[\s:|-]+\|?", line):
            continue
        line = re.sub(r"^[-*]\s+", "", line)
        lines.append(line)
    compact = re.sub(r"\s+", " ", " ".join(lines)).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def derived_skip_reason(text: str) -> str:
    """Explain why a digest intentionally routed no living-note delta."""
    routing = compact_markdown(markdown_section(text, "Routing"))
    decisions = compact_markdown(markdown_section(text, "Decisions and durable deltas"))
    reason = routing or decisions
    coverage = markdown_section(text, "Domain coverage")
    transient_row = next(
        (
            compact_markdown(line, limit=240)
            for line in coverage.splitlines()
            if "Transient/no durable delta" in line
        ),
        "",
    )
    if transient_row and transient_row not in reason:
        reason = f"{reason} Domain scan: {transient_row}".strip()
    return reason


def digest_checks(
    text: str | None, provider_id: str, source_path: str | None
) -> dict[str, bool]:
    """Return the digest readback checks used by the completion contract."""
    if text is None:
        return {
            "required_sections_verified": False,
            "domain_coverage_verified": False,
            "derived_frontmatter_declared": False,
            "provider_id_verified": False,
            "source_link_verified": False,
        }
    statuses = domain_statuses(text)
    return {
        "required_sections_verified": all(
            heading in text for heading in REQUIRED_DIGEST_HEADINGS
        ),
        "domain_coverage_verified": all(
            statuses.get(domain) in DOMAIN_STATUSES for domain in DOMAIN_LABELS
        ),
        "derived_frontmatter_declared": "derived_notes:" in frontmatter(text),
        "provider_id_verified": provider_id in text,
        "source_link_verified": bool(source_path)
        and source_path.removesuffix(".md") in text,
    }


THIN_DIGEST_MIN_MESSAGES = 7
THIN_DIGEST_MIN_CHARS = 300


def digest_body_chars(text: str | None) -> int:
    """Return the character count of the ``## Digest`` section body.

    Measures substance, not structure. ``digest_checks`` already confirms the
    heading exists; this asks whether anything was written under it.
    """
    if not text:
        return 0
    # markdown_section() prepends "## " itself -- pass the bare heading.
    return len(markdown_section(text, "Digest").strip())


def thin_digest_advisory(
    text: str | None, message_count: int
) -> dict[str, Any]:
    """Flag a substantive conversation whose digest body is a stub.

    Advisory only -- deliberately kept out of ``reasons`` so it cannot flip
    ``new_schema_complete``. Calibrated 2026-08-05 over 1,490 digests joined to
    their ledger message counts: fires on 217 records, 215 of them from the
    2026-08-01 bulk-lane day, and on 1 of 209 records from the days after the
    bulk lane was retired (0.5% false positive). Short digests do NOT track
    short conversations -- the flagged median is 15 messages and the worst is
    252 messages under 300 characters.

    Conversations below ``THIN_DIGEST_MIN_MESSAGES`` are never flagged: a
    genuinely transient two-message exchange has little to say and a short
    digest there is honest.
    """
    chars = digest_body_chars(text)
    flagged = (
        text is not None
        and message_count >= THIN_DIGEST_MIN_MESSAGES
        and chars < THIN_DIGEST_MIN_CHARS
    )
    return {
        "digest_body_chars": chars,
        "message_count": message_count,
        "thin_digest": flagged,
    }


def normalize_conversation_path(value: str) -> str | None:
    """Return a safe vault-relative conversation note path."""
    path = value.strip().strip("\"'").replace("\\", "/")
    if path.endswith(".md"):
        path = path[:-3]
    if (
        not path
        or path.startswith("/")
        or ":" in path.split("/", 1)[0]
        or ".." in path.split("/")
    ):
        return None
    if not (
        path.startswith("30_Resources/conversations/")
        or path.startswith("40_Archive/AI Conversations/")
    ):
        return None
    return path + ".md"


def normalize_vault_note_path(value: str) -> str | None:
    """Return a safe vault-relative Markdown path for a referenced note."""
    path = value.strip().strip("\"'").replace("\\", "/")
    if path.endswith(".md"):
        path = path[:-3]
    if (
        not path
        or path.startswith("/")
        or ":" in path.split("/", 1)[0]
        or ".." in path.split("/")
    ):
        return None
    return path + ".md"


def ledger_note_candidates(markdown: str, helper: Any) -> dict[str, dict[str, Any]]:
    """Index source and digest paths explicitly recorded on completed rows.

    Legacy imports used capture-date or custom slugs that cannot be recovered
    from the provider export's title/date. The ledger is the authoritative
    index for those locations, so audit it before trying convention-derived
    paths.
    """
    indexed: dict[str, dict[str, list[str]]] = {}
    for line in markdown.splitlines():
        row = helper.LEDGER_ROW_RE.search(line)
        if not row:
            continue
        provider_id = row.group("id").lower()
        entry = indexed.setdefault(
            provider_id,
            {
                "source": [],
                "digest": [],
                "empty_or_transient": False,
            },
        )
        if "empty-or-transient" in line.lower():
            entry["empty_or_transient"] = True
        values = [
            match.group(1) or match.group(2)
            for match in re.finditer(
                r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]|`([^`\r\n]+\.md)`",
                line,
            )
        ]
        # Completion receipts are append-only corrections. A later receipt for
        # the same provider ID is authoritative for path resolution, but link
        # order inside that receipt is meaningful: writers list canonical
        # source/digest before preserved legacy paths and derived notes.
        row_paths: dict[str, list[str]] = {"source": [], "digest": []}
        for value in values:
            path = normalize_conversation_path(value)
            if not path:
                continue
            kind = "source" if "/originals/" in path else "digest"
            if path not in row_paths[kind]:
                row_paths[kind].append(path)
        for kind, paths in row_paths.items():
            if not paths:
                continue
            entry[kind] = paths + [
                path for path in entry[kind] if path not in paths
            ]
    return indexed


def superseded_provider_ids(markdown: str) -> set[str]:
    """Return IDs explicitly superseded by an append-only alias receipt.

    A bad historical full UUID remains in the ledger for provenance, but it
    must not count as a second conversation after a canonical recapture proves
    the correct provider identity.
    """
    return {
        match.group(1).lower()
        for match in re.finditer(
            r"(?i)\bsuperseded-provider-id\s+"
            r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
            r"[0-9a-f]{4}-[0-9a-f]{12})\b",
            markdown,
        )
    }


def combined_candidates(*groups: list[str]) -> list[str]:
    """Preserve candidate priority while removing duplicates."""
    combined: list[str] = []
    for group in groups:
        for path in group:
            if path not in combined:
                combined.append(path)
    return combined


def digest_candidates(item: dict[str, Any], helper: Any) -> list[str]:
    """Paths a digest for this conversation may live at, in priority order.

    The last pair is a legacy location: an early pass filed 31 digests under
    ``30_Resources/conversations/<provider>/`` rather than flat.

    Today every one of those resolves anyway, because ``source_related_digest()``
    is consulted before this list and their sources' ``related:`` links name them
    — verified 2026-08-05 against all 20 that have no flat twin. So these two
    entries are a fallback, not a repair, and adding them changed no audit
    outcome.

    They earn their place against one specific way that could stop being true:
    ``repair_source_digest_link()`` rewrites exactly that ``related:`` link. A
    legacy-folder digest whose source gets relinked to a new flat path would
    otherwise become unreachable — resolvable by no candidate path and no
    frontmatter link. Kept last so a correctly-filed digest always wins.
    """
    date = str(item.get("created_at") or "")[:10]
    slug = helper.slugify(str(item.get("title") or "Untitled"))
    provider = str(item.get("provider") or "claude").lower()
    short_id = str(item.get("provider_id") or "")[:8]
    return [
        f"30_Resources/conversations/{date}-{slug}--{short_id}.md",
        f"40_Archive/AI Conversations/{provider}/{date}-{slug}--{short_id}.md",
        f"30_Resources/conversations/{date}-{slug}.md",
        f"40_Archive/AI Conversations/{provider}/{date}-{slug}.md",
        f"30_Resources/conversations/{provider}/{date}-{slug}--{short_id}.md",
        f"30_Resources/conversations/{provider}/{date}-{slug}.md",
    ]


def source_related_digest(source_text: str | None) -> str | None:
    """Return an explicitly linked digest path from source frontmatter.

    A title-derived path is not unique: separate conversations can share a title
    on the same date. The immutable source archive is the authoritative place to
    disambiguate its digest, but only conversation-note paths are accepted.
    """
    if not source_text:
        return None
    fm = frontmatter(source_text)
    block = re.search(
        r"(?ms)^related:\s*(?P<items>(?:^[ \t]+-.*$\n?)+)",
        fm,
    )
    if not block:
        return None
    for match in re.finditer(r"\[\[([^\]|#]+)", block.group("items")):
        path = match.group(1).strip().replace("\\", "/")
        if path.endswith(".md"):
            path = path[:-3]
        if (
            path.startswith("30_Resources/conversations/")
            or path.startswith("40_Archive/AI Conversations/")
        ) and ".." not in path.split("/"):
            return path + ".md"
        if (
            "/" not in path
            and "\\" not in path
            and ".." not in path
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ -]*", path)
        ):
            return f"30_Resources/conversations/{path}.md"
    return None


def source_candidates(item: dict[str, Any], helper: Any) -> list[str]:
    date = str(item.get("created_at") or "")[:10]
    slug = helper.slugify(str(item.get("title") or "Untitled"))
    short_id = str(item["provider_id"])[:8]
    provider = str(item.get("provider") or "claude").lower()
    return [
        f"30_Resources/conversations/originals/{provider}/{date}-{slug}--{short_id}.md",
        f"40_Archive/AI Conversations/originals/{provider}/{date}-{slug}--{short_id}.md",
    ]


def ledger_inventory(
    markdown: str, provider: str, provider_ids: set[str]
) -> dict[str, dict[str, Any]]:
    """Build minimal item metadata from canonical shared-ledger rows.

    Browser-only providers do not have a native export, but their canonical
    ledger rows retain the displayed title and ingestion date. Preserve those
    fields so conventional source/digest paths remain discoverable when an
    early row predates explicit wikilinks.
    """
    inventory: dict[str, dict[str, Any]] = {}
    for line in markdown.splitlines():
        row = re.match(
            r"^\s*-\s+(?P<date>\d{4}-\d{2}-\d{2})\s+·\s+"
            r"(?P<provider>[a-z0-9-]+)\s+·\s+"
            r"(?P<id>(?:[0-9a-f]{16}|[0-9a-f-]{36}))\s+·\s+"
            r"(?P<title>.*?)\s+·\s+",
            line,
            re.IGNORECASE,
        )
        if not row:
            continue
        provider_id = row.group("id").lower()
        row_provider = row.group("provider").lower().split("-", 1)[0]
        if provider_id not in provider_ids or row_provider != provider:
            continue
        inventory[provider_id] = {
            "provider_id": provider_id,
            "provider": provider,
            "title": row.group("title").strip(),
            "created_at": row.group("date"),
            "message_count": 1,
            "attachment_count": 0,
            "file_count": 0,
        }
    return inventory


def source_paths_from_search(
    files: list[dict[str, Any]], provider: str, provider_id: str
) -> list[str]:
    """Keep only exact provider source archives returned by VaultIndex."""
    short_id = provider_id[:8].lower()
    roots = (
        f"30_Resources/conversations/originals/{provider}/",
        f"40_Archive/AI Conversations/originals/{provider}/",
    )
    paths: list[str] = []
    for item in files:
        path = str(item.get("path") or "").replace("\\", "/")
        if (
            path.startswith(roots)
            and path.lower().endswith(f"--{short_id}.md")
            and ".." not in path.split("/")
            and path not in paths
        ):
            paths.append(path)
    return paths


def vault_search_files(
    base_url: str, token: str, query: str, limit: int = 20
) -> list[dict[str, Any]]:
    """Search indexed Vault filenames through the mounted Assistant API."""
    params = urllib.parse.urlencode({"q": query, "limit": limit})
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = urllib.request.Request(
        f"{base_url}/assistant/api/vault-files?{params}",
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    files = payload.get("files", [])
    return files if isinstance(files, list) else []


def first_existing(
    reader: Callable[[str], str], candidates: list[str]
) -> tuple[str, str] | tuple[None, None]:
    for path in candidates:
        text = optional_read(reader, path)
        if text is not None:
            return path, text
    return None, None


def source_checks(text: str | None, provider_id: str) -> dict[str, Any]:
    if text is None:
        return {
            "contract_complete": False,
            "hash_verified": False,
            "message_count_verified": False,
            "provider_id_verified": False,
        }
    normalized_body = body(text)
    expected_hash = field_value(text, "content_sha256")
    try:
        expected_count = int(field_value(text, "message_count"))
    except ValueError:
        expected_count = -1
    actual_count = len(re.findall(r"^### \d{3} ", normalized_body, re.MULTILINE))
    return {
        "contract_complete": all(field in text for field in SOURCE_FIELDS),
        "hash_verified": bool(expected_hash)
        and hashlib.sha256(normalized_body.encode("utf-8")).hexdigest()
        == expected_hash,
        "message_count_verified": expected_count >= 0
        and actual_count == expected_count,
        "provider_id_verified": provider_id in frontmatter(text),
    }


def audit_item(
    item: dict[str, Any],
    reader: Callable[[str], str],
    helper: Any,
    ledger_paths: dict[str, Any] | None = None,
    *,
    ledger_entry_present: bool = True,
    ledger_path: str = "30_Resources/conversations/ai-conversation-ingestion-ledger.md",
) -> dict[str, Any]:
    provider_id = str(item["provider_id"])
    recorded = ledger_paths or {
        "source": [],
        "digest": [],
        "empty_or_transient": False,
    }
    export_empty = all(
        int(item.get(field) or 0) == 0
        for field in ("message_count", "attachment_count", "file_count")
    )
    empty_export_verified = bool(recorded.get("empty_or_transient")) and export_empty
    if empty_export_verified:
        reasons = [] if ledger_entry_present else ["ledger-provider-id-missing"]
        no_mutation_reason = (
            "Provider export contains zero messages, attachments, and files; "
            "the ledger records empty-or-transient, so no source or digest is "
            "truthfully required."
        )
        return {
            "provider_id": provider_id,
            "title": item.get("title"),
            "date": str(item.get("created_at") or "")[:10],
            "source_path": None,
            "digest_path": None,
            "path_resolution": {"source": None, "digest": None},
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
            "digest_sha256": "",
            "domain_statuses": {},
            "derived_notes": [],
            "derived_skip_reason": no_mutation_reason,
            "no_mutation_reason": no_mutation_reason,
            "delta_dispositions": [],
            "disposition_targets": {},
            "routing_checks": {
                "durable_delta_count": 0,
                "needs_review_count": 0,
                "delta_dispositions_required": False,
                "delta_dispositions_verified": True,
                "needs_review_resolved": True,
                "routing_search_evidence_required": False,
                "routing_search_evidence_verified": True,
                "routing_review_required": False,
            },
            "routing_review_required": False,
            "digest_substance": {
                "digest_body_chars": 0,
                "message_count": 0,
                "thin_digest": False,
            },
            "reciprocal_links": {},
            "empty_export_verified": True,
            "ledger_receipt": {
                "path": ledger_path,
                "provider_id_verified": ledger_entry_present,
                "empty_or_transient_verified": True,
            },
            "new_schema_complete": not reasons,
            "reasons": reasons,
        }
    source_options = combined_candidates(
        recorded.get("source", []), source_candidates(item, helper)
    )
    source_path, source_text = first_existing(reader, source_options)
    candidates = combined_candidates(
        recorded.get("digest", []), digest_candidates(item, helper)
    )
    linked_digest = source_related_digest(source_text)
    if linked_digest:
        candidates = combined_candidates(
            recorded.get("digest", []),
            [linked_digest],
            digest_candidates(item, helper),
        )
    digest_path, digest_text = first_existing(reader, candidates)
    checks = source_checks(source_text, provider_id)
    digest_readback = digest_checks(digest_text, provider_id, source_path)
    reasons: list[str] = []
    if source_text is None:
        reasons.append("source-missing-at-expected-path")
    else:
        reasons.extend(
            key.replace("_", "-")
            for key, passed in checks.items()
            if not passed
        )
    derived: list[str] = []
    reciprocal: dict[str, bool] = {}
    skip_reason = ""
    statuses: dict[str, str] = {}
    dispositions: list[dict[str, str]] = []
    disposition_targets: dict[str, bool] = {}
    routing_checks: dict[str, Any] = {
        "durable_delta_count": 0,
        "needs_review_count": 0,
        "delta_dispositions_required": False,
        "delta_dispositions_verified": False,
        "needs_review_resolved": False,
        "routing_search_evidence_required": False,
        "routing_search_evidence_verified": False,
        "routing_review_required": False,
    }
    if digest_text is None:
        reasons.append("digest-missing-at-expected-path")
    else:
        for heading in REQUIRED_DIGEST_HEADINGS:
            if heading not in digest_text:
                reasons.append("missing-" + heading[3:].lower().replace(" ", "-"))
        if not digest_readback["domain_coverage_verified"]:
            reasons.append("domain-coverage-incomplete")
        if not digest_readback["derived_frontmatter_declared"]:
            reasons.append("derived-notes-frontmatter-missing")
        if not digest_readback["provider_id_verified"]:
            reasons.append("digest-provider-id-mismatch")
        if not digest_readback["source_link_verified"]:
            reasons.append("digest-source-link-missing")
        derived = derived_links(digest_text)
        (
            statuses,
            dispositions,
            routing_checks,
            routing_reasons,
        ) = assess_no_mutation_routing(
            digest_text,
            has_derived_notes=bool(derived),
        )
        reasons.extend(routing_reasons)
        if not derived:
            for disposition in dispositions:
                if disposition["disposition"] not in {
                    "reused-no-change",
                    "duplicate-of",
                }:
                    continue
                target = first_wikilink(disposition["target"])
                target_path = normalize_vault_note_path(target) if target else None
                if target_path is None:
                    continue
                target_text = optional_read(reader, target_path)
                target_ok = target_text is not None and target_path != digest_path
                disposition_targets[target] = target_ok
                if target_path == digest_path:
                    reasons.append(
                        "delta-disposition-self-target:"
                        + disposition["domain"]
                    )
                elif target_text is None:
                    reasons.append(
                        "delta-disposition-target-not-found:"
                        + disposition["domain"]
                    )
            routing_checks["delta_dispositions_verified"] = not any(
                reason.startswith("delta-") for reason in reasons
            )
        if not derived:
            skip_reason = derived_skip_reason(digest_text)
            if not skip_reason:
                reasons.append("derived-skip-reason-missing")
        digest_stem = digest_path.removesuffix(".md") if digest_path else ""
        source_stem = source_path.removesuffix(".md") if source_path else ""
        for target in derived:
            target_path = target if target.endswith(".md") else target + ".md"
            target_text = optional_read(reader, target_path)
            linked = bool(target_text) and all(
                marker in target_text
                for marker in (provider_id, digest_stem, source_stem)
            )
            reciprocal[target] = linked
            if not linked:
                reasons.append("reciprocal-link-missing:" + target)
    if not ledger_entry_present:
        reasons.append("ledger-provider-id-missing")
    return {
        "provider_id": provider_id,
        "title": item.get("title"),
        "date": str(item.get("created_at") or "")[:10],
        "source_path": source_path,
        "digest_path": digest_path,
        "path_resolution": {
            "source": (
                "ledger"
                if source_path and source_path in recorded.get("source", [])
                else "convention"
                if source_path
                else None
            ),
            "digest": (
                "source-link"
                if digest_path and digest_path == linked_digest
                else "ledger"
                if digest_path and digest_path in recorded.get("digest", [])
                else "convention"
                if digest_path
                else None
            ),
        },
        "source_checks": checks,
        "digest_checks": digest_readback,
        "digest_sha256": (
            hashlib.sha256(digest_text.encode("utf-8")).hexdigest()
            if digest_text is not None
            else ""
        ),
        "domain_statuses": statuses,
        "derived_notes": derived,
        "derived_skip_reason": skip_reason,
        "no_mutation_reason": skip_reason,
        "delta_dispositions": dispositions,
        "disposition_targets": disposition_targets,
        "routing_checks": routing_checks,
        "routing_review_required": bool(
            routing_checks.get("routing_review_required")
        ),
        "digest_substance": thin_digest_advisory(
            digest_text, int(item.get("message_count") or 0)
        ),
        "reciprocal_links": reciprocal,
        "empty_export_verified": False,
        "ledger_receipt": {
            "path": ledger_path,
            "provider_id_verified": ledger_entry_present,
        },
        "new_schema_complete": not reasons,
        "reasons": reasons,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--export",
        action="append",
        default=[],
        help="Provider native-export path/glob; repeat for multiple batches.",
    )
    parser.add_argument(
        "--provider",
        choices=("claude", "chatgpt", "gemini", "codex"),
        default="claude",
        help="Provider rows to audit from the shared ingestion ledger.",
    )
    parser.add_argument(
        "--ledger-only",
        action="store_true",
        help=(
            "Build the audit inventory from ledger-recorded source/digest paths. "
            "Use for browser-captured providers that have no native export."
        ),
    )
    parser.add_argument("--config", type=Path, default=Path("emptyos.toml"))
    parser.add_argument(
        "--vault-root",
        type=Path,
        help="Read notes directly from this vault when the daemon is offline",
    )
    parser.add_argument(
        "--ledger",
        default="30_Resources/conversations/ai-conversation-ingestion-ledger.md",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.ledger_only and not args.export:
        raise SystemExit("--export is required unless --ledger-only is used")
    helper = load_queue_helper(args.provider)
    if args.vault_root:
        reader = lambda path: helper.local_vault_read(args.vault_root, path)
    else:
        base_url, token = helper.load_connection(args.config)
        reader = lambda path: helper.vault_read(base_url, token, path)
    ledger = reader(args.ledger)
    processed_raw = helper.processed_ids_from_ledger(
        ledger, provider=args.provider
    )
    # Provider-less legacy rows remain eligible in the shared helper. Gemini's
    # canonical IDs are uniquely 16 lowercase hex characters, so keep that
    # shape here rather than accidentally auditing UUID-shaped legacy rows
    # belonging to another provider.
    if args.provider == "gemini":
        processed_raw = {
            provider_id
            for provider_id in processed_raw
            if re.fullmatch(r"[0-9a-f]{16}", provider_id)
        }
    superseded = superseded_provider_ids(ledger)
    processed = processed_raw - superseded
    ledger_paths = ledger_note_candidates(ledger, helper)
    if args.ledger_only and not args.vault_root:
        for provider_id in processed:
            entry = ledger_paths.setdefault(
                provider_id,
                {
                    "source": [],
                    "digest": [],
                    "empty_or_transient": False,
                },
            )
            if entry["source"]:
                continue
            files = vault_search_files(base_url, token, provider_id[:8])
            entry["source"] = source_paths_from_search(
                files, args.provider, provider_id
            )
    inventory: dict[str, dict[str, Any]] = {}
    if args.ledger_only:
        inventory.update(ledger_inventory(ledger, args.provider, processed))
        for provider_id in processed - inventory.keys():
            # audit_item resolves the authoritative paths from the ledger. A
            # non-zero synthetic message count prevents pathless
            # empty-or-transient handling; the immutable source contract still
            # verifies its real message_count and content hash independently.
            inventory[provider_id] = {
                "provider_id": provider_id,
                "provider": args.provider,
                "title": "",
                "created_at": "",
                "message_count": 1,
                "attachment_count": 0,
                "file_count": 0,
            }
    else:
        for item in helper.iter_inventory_items(args.export):
            inventory.setdefault(item["provider_id"], item)
    records = [
        audit_item(
            inventory[provider_id],
            reader,
            helper,
            ledger_paths.get(provider_id),
            ledger_entry_present=provider_id in processed,
            ledger_path=args.ledger,
        )
        for provider_id in sorted(processed)
        if provider_id in inventory
    ]
    missing_from_export = sorted(processed - inventory.keys())
    report = {
        "schema_version": 4,
        "provider": args.provider,
        "inventory_mode": "ledger-only" if args.ledger_only else "native-export",
        "ledger_path": args.ledger,
        "processed_in_ledger": len(processed),
        "ledger_provider_ids_raw": len(processed_raw),
        "superseded_provider_ids": sorted(superseded),
        "audited_in_export": len(records),
        "new_schema_complete": sum(
            1 for record in records if record["new_schema_complete"]
        ),
        "needs_backfill": sum(
            1 for record in records if not record["new_schema_complete"]
        ),
        "living_note_mutations": sum(
            1 for record in records if record.get("derived_notes")
        ),
        "accounted_no_mutation": sum(
            1
            for record in records
            if not record.get("derived_notes")
            and record.get("routing_checks", {}).get("durable_delta_count")
            and not record.get("routing_review_required")
        ),
        "no_durable_delta": sum(
            1
            for record in records
            if not record.get("derived_notes")
            and not record.get("routing_checks", {}).get("durable_delta_count")
            and record.get("new_schema_complete")
        ),
        "routing_review_required": sum(
            1 for record in records if record.get("routing_review_required")
        ),
        "thin_digest_advisory": sum(
            1
            for record in records
            if record.get("digest_substance", {}).get("thin_digest")
        ),
        "missing_from_export": missing_from_export,
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {key: report[key] for key in report if key != "records"},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
