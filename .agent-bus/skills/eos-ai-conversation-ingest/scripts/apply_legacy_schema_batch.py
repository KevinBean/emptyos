#!/usr/bin/env python3
"""Insert the missing v4 sections into a legacy digest, and nothing else.

Adds exactly three things the auditor requires and the legacy format predates:
a `## Domain coverage` table, an `## Evidence chain` section, and a
`derived_notes:` frontmatter key. Existing prose is never touched -- not the
digest, not the decisions, not the fact-check, not the routing note. Those were
written and reviewed once and are not this writer's to revise.

Routing receipts are deliberately NOT this script's job. A legacy digest that
gains its first `delta` rows becomes an ordinary routing-review record, and
goes through `stage_routing_evidence.py` + `apply_routing_evidence_batch.py`
like any other. One evidence bar, one code path.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from datetime import date
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
REPO = SCRIPT_DIR.parents[3]
CONFIG = REPO / "emptyos.toml"


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load helper: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AUDIT = load_module(
    "conversation_legacy_audit_w", SCRIPT_DIR / "audit_evidence_graph.py"
)
APPLY = load_module(
    "conversation_legacy_io_w",
    SCRIPT_DIR / "apply_no_mutation_browser_batch.py",
)

BACKUP_DIR = "99_Attachments/temp-backup"


def escape_cell(value: str) -> str:
    """`markdown_table_cells` splits on the raw pipe; a backslash won't save it."""
    return str(value).replace("|", "∣").strip()


def coverage_table(coverage: dict[str, dict[str, str]]) -> str:
    lines = [
        "## Domain coverage",
        "",
        "| Domain | Status | Finding |",
        "|---|---|---|",
    ]
    for domain in AUDIT.DOMAIN_LABELS:
        row = coverage.get(domain) or {}
        status = str(row.get("status") or "needs-review")
        notes = escape_cell(row.get("notes") or "No explanation supplied.")
        lines.append(f"| {domain} | {status} | {notes} |")
    lines.append("")
    return "\n".join(lines)


def disposition_table(coverage: dict[str, dict[str, str]]) -> str:
    """One row per durable delta, with the receipt left deliberately empty.

    Completing the coverage table is what first makes these deltas visible to
    the auditor, and a durable delta with no derived note needs a disposition
    row or the record stays in routing review. The row is written here; the
    `Routing evidence` cell is NOT, because earning it means searching the
    Vault and comparing real notes -- `stage_routing_evidence.py`'s job. A
    placeholder keeps the table rectangular and keeps the record honestly
    flagged as owing evidence rather than quietly appearing to have it.
    """
    rows = [
        (domain, row)
        for domain, row in coverage.items()
        if row.get("status") == "delta"
        and domain != "Transient/no durable delta"
    ]
    if not rows:
        return ""
    lines = [
        "## Delta disposition",
        "",
        "| Domain | Disposition | Target | Reason | Routing evidence |",
        "|---|---|---|---|---|",
    ]
    for domain, row in rows:
        reason = escape_cell(
            row.get("notes") or "Retained in the digest at this granularity."
        )
        lines.append(
            f"| {domain} | digest-contained | This digest | {reason} | - |"
        )
    lines.extend(
        [
            "",
            "No living Vault note was changed by this schema backfill. Each"
            " row above still owes a search receipt.",
            "",
        ]
    )
    return "\n".join(lines)


def evidence_chain(
    *, digest_path: str, source_path: str, provider_id: str
) -> str:
    lines = [
        "## Evidence chain",
        "",
        f"- Source archive: [[{source_path.removesuffix('.md')}]]",
        f"- Digest: [[{digest_path.removesuffix('.md')}]]",
        f"- Provider conversation ID: `{provider_id}`",
        "- Derived living-note mutations: none recorded by this backfill.",
        "  Domain coverage was classified from the digest that already"
        " existed; no living note was created, changed, or claimed.",
        "- Ledger: [[ai-conversation-ingestion-ledger]]",
        "",
    ]
    return "\n".join(lines)


def insert_before(text: str, heading: str, block: str) -> str:
    """Place a section immediately before an existing one, or at the end."""
    marker = f"\n## {heading}"
    index = text.find(marker)
    if index < 0:
        return text.rstrip("\n") + "\n\n" + block
    return text[: index + 1] + block + "\n" + text[index + 1 :]


def add_derived_frontmatter(text: str) -> str:
    """Declare an empty derived-notes list without disturbing other keys."""
    if not text.startswith("---"):
        raise APPLY.ApplyRefused(
            "Digest has no frontmatter to declare derived_notes in",
            {"reason": "no-frontmatter"},
        )
    end = text.find("\n---", 3)
    if end < 0:
        raise APPLY.ApplyRefused(
            "Unterminated frontmatter", {"reason": "bad-frontmatter"}
        )
    head = text[: end + 1]
    if "derived_notes:" in head:
        return text
    return head + "derived_notes: []\n" + text[end + 1 :]


def complete_schema(
    text: str,
    *,
    coverage: dict[str, dict[str, str]],
    digest_path: str,
    source_path: str,
    provider_id: str,
) -> str:
    out = text
    if "## Domain coverage" not in out:
        out = insert_before(
            out, "Decisions and durable deltas", coverage_table(coverage)
        )
    if "## Delta disposition" not in out:
        table = disposition_table(coverage)
        if table:
            out = insert_before(out, "Evidence chain", table) if (
                "## Evidence chain" in out
            ) else insert_before(out, "Source", table)
    if "## Evidence chain" not in out:
        out = insert_before(
            out,
            "Source",
            evidence_chain(
                digest_path=digest_path,
                source_path=source_path,
                provider_id=provider_id,
            ),
        )
    return add_derived_frontmatter(out)


def backup_path(digest_path: str, content: str) -> str:
    stem = Path(digest_path).stem
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:10]
    return f"{BACKUP_DIR}/{stem}-{date.today().isoformat()}-schema-{digest}.md"


def apply_one(
    staged: dict[str, Any],
    *,
    base_url: str,
    headers: dict[str, str],
    queue_index: dict[str, dict[str, Any]],
    dry_run: bool,
) -> dict[str, Any]:
    provider_id = str(staged.get("provider_id") or "")
    digest_path = str(staged.get("digest_path") or "")
    if staged.get("needs_review_domains"):
        return {
            "provider_id": provider_id,
            "digest": "held",
            "reason": "classifier left domains at needs-review",
            "domains": staged["needs_review_domains"],
        }

    current = APPLY.api_read(base_url, headers, digest_path)
    if current is None:
        raise APPLY.ApplyRefused(
            f"Digest missing at {digest_path}", {"provider_id": provider_id}
        )

    entry = queue_index.get(provider_id) or {}
    source_path = str(entry.get("source_path") or "")
    patched = complete_schema(
        current,
        coverage=staged["domain_coverage"],
        digest_path=digest_path,
        source_path=source_path or digest_path,
        provider_id=provider_id,
    )

    # Derived from the text on disk, so "already done" is decided by what the
    # writer would actually produce rather than by a checklist that can drift
    # out of step with it. This also has to precede the staleness check: a
    # digest we already completed has by definition changed since staging.
    if patched == current:
        return {
            "provider_id": provider_id,
            "digest": "reused",
            "reason": "schema already complete",
        }

    expected = str(staged.get("digest_sha256") or "")
    actual = hashlib.sha256(current.encode("utf-8")).hexdigest()
    if expected and expected != actual:
        raise APPLY.ApplyRefused(
            f"Digest changed since staging at {digest_path}",
            {"provider_id": provider_id},
        )

    check = AUDIT.domain_statuses(patched)
    incomplete = [
        d for d in AUDIT.DOMAIN_LABELS if check.get(d) not in AUDIT.DOMAIN_STATUSES
    ]
    if incomplete:
        raise APPLY.ApplyRefused(
            "Patched coverage table still does not parse for every domain",
            {"provider_id": provider_id, "domains": incomplete},
        )

    if dry_run:
        return {
            "provider_id": provider_id,
            "digest": "dry-run",
            "delta_domains": staged.get("delta_domains"),
            "grew_by": len(patched) - len(current),
        }

    backup = backup_path(digest_path, current)
    APPLY.write_exact(base_url, headers, backup, current)
    state = APPLY.write_update_exact(
        base_url, headers, digest_path, before=current, after=patched
    )
    return {
        "provider_id": provider_id,
        "digest": state,
        "digest_path": digest_path,
        "backup": backup,
        "delta_domains": staged.get("delta_domains"),
    }


def build(args: argparse.Namespace) -> list[dict[str, Any]]:
    base_url, headers = APPLY.connection(args.config)
    staged = json.loads(args.staged.read_text(encoding="utf-8-sig"))
    queue = json.loads(args.queue.read_text(encoding="utf-8-sig"))
    queue_index = {
        str(i.get("provider_id")): i for i in queue.get("items") or []
    }
    items = list(staged.get("items") or [])
    if args.provider_id:
        wanted = set(args.provider_id)
        items = [i for i in items if i.get("provider_id") in wanted]
    items = items[: args.limit]
    return [
        apply_one(
            row,
            base_url=base_url,
            headers=headers,
            queue_index=queue_index,
            dry_run=args.dry_run,
        )
        for row in items
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", type=Path, required=True)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--provider-id", action="append", default=[])
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    return APPLY.run_cli(lambda: build(args), noun="digests completed")


if __name__ == "__main__":
    raise SystemExit(main())
