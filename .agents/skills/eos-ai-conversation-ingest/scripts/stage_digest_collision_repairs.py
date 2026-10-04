#!/usr/bin/env python3
"""Stage narrowly-scoped Claude digest path collision repairs.

This script is read-only with respect to the Vault. It copies the original
human-reviewed ingest specifications for audit rows that have exactly the two
known title-path collision failures, then opts those rows into the native
writer's verified source-link repair. The original source message body and the
reviewed digest analysis are never regenerated here.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
EXPECTED_REASONS = {
    "digest-provider-id-mismatch",
    "digest-source-link-missing",
}


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load helper: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


APPLY = load_module(
    "conversation_collision_stage_io",
    SCRIPT_DIR / "apply_no_mutation_browser_batch.py",
)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def index_reviewed_specs(spec_paths: list[Path]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    origins: dict[str, Path] = {}
    for path in spec_paths:
        payload = read_json(path)
        if str(payload.get("provider") or "claude").lower() != "claude":
            continue
        for raw in payload.get("items") or []:
            if not isinstance(raw, dict):
                continue
            provider_id = str(raw.get("provider_id") or "").strip()
            if not provider_id:
                raise ValueError(f"Reviewed spec without provider_id: {path}")
            if provider_id in indexed:
                raise ValueError(
                    "Duplicate reviewed spec for "
                    f"{provider_id}: {origins[provider_id]} and {path}"
                )
            indexed[provider_id] = raw
            origins[provider_id] = path
    return indexed


def build(args: argparse.Namespace) -> list[dict[str, Any]]:
    queue = read_json(args.queue)
    if str(queue.get("provider") or "").lower() != "claude":
        raise ValueError("Collision repair staging only accepts a Claude queue")

    queue_items = list(queue.get("items") or [])
    if args.id:
        wanted = set(args.id)
        queue_items = [
            item
            for item in queue_items
            if str(item.get("provider_id") or "") in wanted
        ]
        found = {str(item.get("provider_id") or "") for item in queue_items}
        missing = sorted(wanted - found)
        if missing:
            raise ValueError(f"Requested IDs are absent from the queue: {missing}")

    spec_paths = sorted(args.spec_dir.glob(args.spec_glob))
    if not spec_paths:
        raise ValueError(
            f"No reviewed specs match {args.spec_glob!r} in {args.spec_dir}"
        )
    reviewed = index_reviewed_specs(spec_paths)

    staged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for queue_item in queue_items:
        provider_id = str(queue_item.get("provider_id") or "").strip()
        if not provider_id:
            raise ValueError("Queue item is missing provider_id")
        if provider_id in seen:
            raise ValueError(f"Duplicate queue item for {provider_id}")
        seen.add(provider_id)

        reasons = set(queue_item.get("reasons") or [])
        if reasons != EXPECTED_REASONS:
            raise ValueError(
                f"Refusing non-collision repair for {provider_id}: {sorted(reasons)}"
            )
        reviewed_item = reviewed.get(provider_id)
        if reviewed_item is None:
            raise ValueError(f"No reviewed spec found for {provider_id}")

        item = deepcopy(reviewed_item)
        item["ingestion_date"] = args.repair_date
        item["reuse_existing_source"] = True
        item["repair_source_digest_link"] = True
        staged.append(item)

    output = {
        "schema_version": 1,
        "record_kind": "digest-collision-repair-stage",
        "provider": "claude",
        "repair_date": args.repair_date,
        "items": staged,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(args.output.suffix + ".tmp")
    temp.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(args.output)
    return staged


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--spec-dir", type=Path, required=True)
    parser.add_argument("--spec-glob", default="reviewed-spec-*.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repair-date", default=date.today().isoformat())
    parser.add_argument("--id", action="append", default=[])
    args = parser.parse_args()

    return APPLY.run_cli(lambda: build(args), noun="collision repairs staged")


if __name__ == "__main__":
    raise SystemExit(main())
