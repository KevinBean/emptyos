#!/usr/bin/env python3
"""Mark each superseded thin digest, append-only, from a writer receipt.

    python data/imports/t2-work/supersede_batch.py \
        --receipts data/imports/t2-work/receipts-2025-04-30.json

``supersede.py`` beside this file does one record with the paths, the date, and
the reason hardcoded in its ``__main__``. Seven day-batches need the same move
8, 5, 7, 4, 2, 2 and 3 times, so this takes the *writer's own receipt* as input
rather than a hand-typed path pair.

That matters for correctness, not just convenience: the new digest path is
``<date>-<slug>--<id-short>.md``, which is derived from the export title inside
the writer. Deriving it a second time here would be a second implementation of
the same rule, free to drift. ``digest.path`` in the receipt is what was
actually written.

The old path must come from a manifest frozen **before** the write.
``build_cluster.py`` rebuilds from the Vault, so once a record is re-digested
its ``digest_path`` points at the *new* suffixed note and the path this script
needs is gone. Default is therefore the frozen snapshot, not the live manifest.
All 31 remaining records are legacy-path, so old != new for every one of them;
when they are equal the writer reused the note in place, or the manifest has
already been rebuilt, and there is nothing to supersede.

Append-only and idempotent: a note already carrying ``## Superseded`` is left
alone. Vault writes go through the daemon API, never the filesystem.
"""
from __future__ import annotations

import argparse
import json
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CLUSTER = REPO / "data/imports/t2-work/cluster-prewrite-snapshot.json"
CONFIG = REPO / "emptyos.toml"
BASE = "http://127.0.0.1:9000"  # 127.0.0.1, not localhost: urllib tries ::1 first
MARK = "## Superseded"

TOKEN = tomllib.load(CONFIG.open("rb"))["network"]["auth_token"]
AUTH = {"Authorization": f"Bearer {TOKEN}"}
WRITE_HDR = {**AUTH, "Content-Type": "application/json"}


def read_note(path: str) -> str:
    url = f"{BASE}/api/vault/read?path={urllib.parse.quote(path, safe='')}"
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers=AUTH), timeout=30
        ) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return ""
        raise
    return payload.get("content") or payload.get("text") or ""


def write_note(path: str, content: str) -> None:
    body = json.dumps({"path": path, "content": content}).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE}/api/vault/write", data=body, headers=WRITE_HDR, method="POST"
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        json.load(response)


def block_for(new_path: str, record: dict, today: str) -> str:
    chars = record.get("digest_chars")
    messages = record.get("message_count")
    flagged = record.get("t1_flagged")
    gate = (
        "flagged by the thin-digest advisory"
        if flagged
        else (
            "below the advisory's notice — it has "
            f"{messages} messages, under the `>=7` threshold, or cleared 300 "
            "characters by a handful. Found instead by cluster membership"
        )
    )
    return (
        f"\n{MARK}\n\n"
        f"**Superseded {today} by [[{new_path.removesuffix('.md')}]].**\n\n"
        f"The earlier pass rendered a {chars}-character digest of a "
        f"{messages}-message conversation and recorded `derived_notes: []`, and "
        f"still passed as `audited-complete` because that auditor validated "
        f"structure, not substance. This record was {gate}.\n\n"
        "This note is kept as the record of what the earlier pass concluded, and\n"
        "of how it passed. It is not the current digest for this conversation;\n"
        "the source archive's `related:` link now points at the replacement.\n"
        "Retained rather than deleted — a wrong prior conclusion is evidence\n"
        "about the pipeline, not litter.\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipts", required=True, help="writer stdout envelope")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=CLUSTER,
        help="cluster manifest frozen BEFORE the write (default: the snapshot)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    payload = json.loads(Path(args.receipts).read_text(encoding="utf-8-sig"))
    # The writer emits one agent-cli envelope: {ok, code, message, data:{receipts:[...]}}.
    # Tolerate a bare list too, so a hand-made receipt file still works.
    receipts = payload
    if isinstance(receipts, dict):
        receipts = receipts.get("data", receipts)
    if isinstance(receipts, dict):
        receipts = receipts.get("receipts")
    if not isinstance(receipts, list):
        raise SystemExit("receipts file has no receipts array")

    cluster = json.loads(args.manifest.read_text(encoding="utf-8"))
    by_provider = {str(i["provider_id"]).lower(): i for i in cluster["items"]}
    today = date.today().isoformat()

    marked = skipped = same = missing = failed = 0
    for receipt in receipts:
        provider_id = str(receipt.get("provider_id") or "").lower()
        record = by_provider.get(provider_id)
        new_path = (receipt.get("digest") or {}).get("path") or ""
        if record is None or not new_path:
            print(f"  ?? {provider_id}: no manifest row or no digest path")
            missing += 1
            continue
        old_path = record["digest_path"]
        short = record["short_id"]
        if old_path == new_path:
            print(f"  == {short}: reused in place, nothing to supersede")
            same += 1
            continue

        text = read_note(old_path)
        if not text:
            print(f"  -- {short}: {old_path} not present")
            missing += 1
            continue
        if MARK in text:
            print(f"  == {short}: already marked")
            skipped += 1
            continue

        out = text.rstrip() + "\n" + block_for(new_path, record, today)
        if args.dry_run:
            print(f"  DRY {short}: would append {len(out) - len(text)} chars to {old_path}")
            marked += 1
            continue
        write_note(old_path, out)
        back = read_note(old_path)
        if back == out:
            print(f"  OK {short}: +{len(out) - len(text)} chars, readback matches")
            marked += 1
        else:
            print(f"  FAIL {short}: readback DIFFERS")
            failed += 1

    print(
        f"\nmarked={marked} already={skipped} reused-in-place={same} "
        f"missing={missing} failed={failed}"
    )
    return 1 if failed or missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
