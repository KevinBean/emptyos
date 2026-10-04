#!/usr/bin/env python3
"""Write staged routing-evidence receipts into digest disposition tables.

The narrowest writer in this skill. It edits exactly one thing: the routing
column of the delta-disposition table inside an existing digest. It never
opens the export, never touches the immutable source, never writes the ledger,
and never creates a derived note.

That narrowness is the point. The 331 Claude digests were written by throwaway
scripts in a different format (prose fact-check bullets, different
frontmatter), so round-tripping them through ``apply_native_export_batch``
would rewrite 331 whole notes — lossily — to add one missing column. The defect
is one cell wide, so the fix is too.

Every write is preceded by a byte-exact staleness check against the hash the
stager recorded, backed up, and followed by an in-process re-audit that refuses
the batch if the item did not actually become schema-complete.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
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
    "conversation_evidence_audit_writer",
    SCRIPT_DIR / "audit_evidence_graph.py",
)
APPLY = load_module(
    "conversation_apply_shared",
    SCRIPT_DIR / "apply_no_mutation_browser_batch.py",
)

HEADER = "| Domain | Disposition | Target | Reason | Routing evidence |"
SEPARATOR = "|---|---|---|---|---|"
BACKUP_DIR = "99_Attachments/temp-backup"


def escape_cell(value: str) -> str:
    """Neutralise a pipe so it cannot split one cell into two on re-read.

    `routing_table` writes `\\|`, but `markdown_table_cells` protects only
    wikilinks and then splits on the raw character — a backslash does not save
    it. So an escaped pipe still truncates the cell when the auditor parses it
    back. We substitute a divides sign instead, which survives the round trip.
    """
    return str(value).replace("|", "∣").strip()


def append_cell(line: str, value: str) -> str:
    """Append a cell to a row's raw text, leaving existing cells untouched."""
    body = line.rstrip()[:-1].rstrip()
    return f"{body} | {value} |"


def replace_last_cell(line: str, value: str) -> str:
    body = line.rstrip()[:-1]
    cut = body.rfind("|")
    return f"{body[:cut]}| {value} |"


def is_table_row(line: str) -> bool:
    cells = AUDIT.markdown_table_cells(line)
    return bool(cells) and cells[0] in AUDIT.DOMAIN_LABELS


def is_header_row(line: str) -> bool:
    cells = AUDIT.markdown_table_cells(line)
    return bool(cells) and cells[0].strip().lower() == "domain"


def is_separator_row(line: str) -> bool:
    stripped = line.strip()
    return (
        stripped.startswith("|")
        and set(stripped) <= set("|-: ")
        and "-" in stripped
    )


# `## Domain coverage` is also a table whose header starts with "Domain" and
# whose rows start with a domain label, so a document-wide row match rewrites
# it too — relabelling its columns and padding its rows. The auditor keeps
# reading it because `domain_statuses` is positional and tolerant, which is
# precisely how that corruption would have ridden through 331 files unnoticed.
# Patching is therefore scoped to the disposition section by name.
DISPOSITION_HEADINGS = ("Delta disposition", "Routing")


def section_bounds(text: str) -> tuple[int, int]:
    """Line range of the disposition section, or (-1, -1) when absent."""
    lines = text.splitlines()
    for heading in DISPOSITION_HEADINGS:
        target = f"## {heading}".lower()
        for index, line in enumerate(lines):
            if line.strip().lower() != target:
                continue
            end = len(lines)
            for offset in range(index + 1, len(lines)):
                if lines[offset].startswith("## "):
                    end = offset
                    break
            return index, end
    return -1, -1


def patch_table(text: str, patches: dict[str, dict[str, Any]]) -> str:
    """Rewrite the disposition table to five columns, in place.

    Cells 0-3 are preserved byte-for-byte for every row, including rows this
    batch does not touch — the reason text carries the original digest's own
    words and is not this writer's to reword.
    """
    lines = text.splitlines()
    start, end = section_bounds(text)
    if start < 0:
        raise APPLY.ApplyRefused(
            "No disposition section found to patch",
            {"reason": "no-disposition-section"},
        )
    out: list[str] = list(lines[:start])
    seen_rows = 0
    for line in lines[start:end]:
        if is_header_row(line):
            out.append(HEADER)
            continue
        if is_separator_row(line) and out and out[-1] == HEADER:
            out.append(SEPARATOR)
            continue
        if not is_table_row(line):
            out.append(line)
            continue

        seen_rows += 1
        cells = AUDIT.markdown_table_cells(line)
        domain = cells[0]
        patch = patches.get(domain)

        if patch is None:
            # Not ours to touch. Widen it to five columns so the table stays
            # rectangular, but never re-render cells we did not author.
            out.append(line if len(cells) >= 5 else append_cell(line, "-"))
            continue

        evidence = escape_cell(
            str(patch.get("routing_evidence") or "")
        ) or "-"
        rewrites_row = any(
            patch.get(key) for key in ("disposition", "target", "reason")
        )

        if not rewrites_row:
            # The common case, and all of bucket A: only the receipt changes.
            # Editing the raw text means cells 0-3 cannot be mangled by a
            # parse-then-rebuild round trip.
            #
            # Cell count alone cannot tell a five-column row from a four-column
            # row whose reason contains a pipe -- both parse as five. Guessing
            # would overwrite real prose with a receipt, so an ambiguous row is
            # refused and left for a human instead.
            if len(cells) == 4:
                out.append(append_cell(line, evidence))
            elif len(cells) == 5 and (
                cells[4].strip() in {"", "-", "—"}
                or AUDIT.valid_digest_contained_routing_evidence(cells[4])
            ):
                out.append(replace_last_cell(line, evidence))
            else:
                raise APPLY.ApplyRefused(
                    "Ambiguous disposition row: cannot tell a trailing "
                    "receipt from a pipe inside the reason",
                    {
                        "domain": domain,
                        "cells": len(cells),
                        "last_cell": cells[-1] if cells else "",
                    },
                )
            continue

        rebuilt = (
            "| "
            + " | ".join(
                [
                    domain,
                    escape_cell(
                        patch.get("disposition")
                        or (cells[1] if len(cells) >= 2 else "digest-contained")
                    ),
                    escape_cell(
                        patch.get("target")
                        or (cells[2] if len(cells) >= 3 else "")
                    )
                    or "-",
                    escape_cell(
                        patch.get("reason")
                        or (cells[3] if len(cells) >= 4 else "")
                    )
                    or "-",
                    evidence,
                ]
            )
            + " |"
        )
        # A rebuild is the only path that can lose text, so prove it did not:
        # the row must parse back to exactly what we meant to write.
        reparsed = AUDIT.markdown_table_cells(rebuilt)
        if len(reparsed) != 5 or reparsed[0] != domain:
            raise APPLY.ApplyRefused(
                "Rebuilt disposition row does not round-trip",
                {"domain": domain, "rebuilt": rebuilt, "parsed": reparsed},
            )
        out.append(rebuilt)
    out.extend(lines[end:])
    if not seen_rows:
        raise APPLY.ApplyRefused(
            "No disposition table row found to patch",
            {"reason": "no-table-rows"},
        )
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def backup_path(digest_path: str, content: str) -> str:
    stem = Path(digest_path).stem
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:10]
    stamp = date.today().isoformat()
    return f"{BACKUP_DIR}/{stem}-{stamp}-routing-{digest}.md"


def apply_one(
    staged: dict[str, Any],
    *,
    base_url: str,
    headers: dict[str, str],
    ledger_index: dict[str, Any],
    helper: Any,
    dry_run: bool,
) -> dict[str, Any]:
    provider_id = str(staged.get("provider_id") or "")
    digest_path = str(staged.get("digest_path") or "")
    patches = {
        str(row["domain"]): row
        for row in staged.get("patches") or []
        if str(row.get("routing_evidence") or "").strip()
    }
    if not patches:
        return {
            "provider_id": provider_id,
            "digest": "skipped",
            "reason": "no staged rows carried a receipt",
        }

    current = APPLY.api_read(base_url, headers, digest_path)
    if current is None:
        raise APPLY.ApplyRefused(
            f"Digest missing at {digest_path}",
            {"provider_id": provider_id, "path": digest_path},
        )
    # Re-derive from the text just read, never from the staged copy, so a
    # re-run is idempotent against whatever is actually on disk now. This is
    # checked BEFORE the staleness hash on purpose: a digest we already
    # patched has by definition changed since staging, and treating that as a
    # stale-input refusal would abort the whole batch on a re-run.
    rows = AUDIT.delta_dispositions(current)
    unevidenced = [
        row["domain"]
        for row in rows
        if row["domain"] in patches
        and not AUDIT.valid_digest_contained_routing_evidence(
            row["routing_evidence"]
        )
    ]
    if not unevidenced:
        return {
            "provider_id": provider_id,
            "digest": "reused",
            "reason": "every targeted row already carries a valid receipt",
        }

    expected = str(staged.get("digest_sha256") or "")
    actual = hashlib.sha256(current.encode("utf-8")).hexdigest()
    if expected and expected != actual:
        raise APPLY.ApplyRefused(
            f"Digest changed since staging at {digest_path}",
            {
                "provider_id": provider_id,
                "staged_sha256": expected,
                "current_sha256": actual,
            },
        )

    patched = patch_table(current, patches)
    for row in AUDIT.delta_dispositions(patched):
        if row["domain"] in patches and row[
            "disposition"
        ] == "digest-contained" and not (
            AUDIT.valid_digest_contained_routing_evidence(
                row["routing_evidence"]
            )
        ):
            raise APPLY.ApplyRefused(
                "Patched row still fails the routing-evidence grammar",
                {
                    "provider_id": provider_id,
                    "domain": row["domain"],
                    "routing_evidence": row["routing_evidence"],
                },
            )

    if dry_run:
        return {
            "provider_id": provider_id,
            "digest": "dry-run",
            "rows_patched": len(patches),
            "preview": AUDIT.markdown_section(patched, "Delta disposition")[
                :600
            ]
            or AUDIT.markdown_section(patched, "Routing")[:600],
        }

    # `/api/vault/write` is a bare overwrite with no versioning, so this backup
    # is the only undo that exists for the edit below.
    backup = backup_path(digest_path, current)
    APPLY.write_exact(base_url, headers, backup, current)
    state = APPLY.write_update_exact(
        base_url, headers, digest_path, before=current, after=patched
    )

    receipt = {
        "provider_id": provider_id,
        "digest": state,
        "digest_path": digest_path,
        "backup": backup,
        "rows_patched": sorted(patches),
    }

    entry = ledger_index.get(provider_id)
    if entry is not None:
        item = {
            "provider_id": provider_id,
            "title": staged.get("title") or "",
            "created_at": "",
            "message_count": 1,
            "attachment_count": 0,
            "file_count": 0,
        }
        audited = AUDIT.audit_item(
            item,
            lambda path: APPLY.api_read(base_url, headers, path),
            helper,
            entry,
            ledger_entry_present=True,
        )
        receipt["new_schema_complete"] = bool(audited.get("new_schema_complete"))
        receipt["routing_review_required"] = bool(
            audited.get("routing_review_required")
        )
        if audited.get("routing_review_required"):
            raise APPLY.ApplyRefused(
                "Item still requires routing review after the patch",
                {"provider_id": provider_id, "reasons": audited.get("reasons")},
            )
    return receipt


def build(args: argparse.Namespace) -> list[dict[str, Any]]:
    base_url, headers = APPLY.connection(args.config)
    staged = json.loads(args.staged.read_text(encoding="utf-8-sig"))
    items = list(staged.get("items") or [])
    if args.provider_id:
        wanted = set(args.provider_id)
        items = [row for row in items if row.get("provider_id") in wanted]
    if args.skip_review:
        items = [row for row in items if not row.get("needs_strong_review")]
    items = items[: args.limit]

    helper = AUDIT.load_queue_helper(str(staged.get("provider") or "claude"))
    ledger_text = (
        APPLY.api_read(base_url, headers, args.ledger) or ""
    )
    ledger_index = AUDIT.ledger_note_candidates(ledger_text, helper)

    return [
        apply_one(
            row,
            base_url=base_url,
            headers=headers,
            ledger_index=ledger_index,
            helper=helper,
            dry_run=args.dry_run,
        )
        for row in items
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--provider-id", action="append", default=[])
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--skip-review",
        action="store_true",
        help="Apply only rows the stager did not flag for strong review.",
    )
    parser.add_argument(
        "--ledger",
        default="30_Resources/conversations/ai-conversation-ingestion-ledger.md",
    )
    args = parser.parse_args()

    def run() -> list[dict[str, Any]]:
        return build(args)

    return APPLY.run_cli(run, noun="digests patched")


if __name__ == "__main__":
    raise SystemExit(main())
