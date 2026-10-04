#!/usr/bin/env python3
"""Build a supersede_batch receipts file from a cluster manifest + short ids.

    python data/imports/t2-work/make_receipts.py \
        --manifest data/imports/t2-simplecalc-cluster.json \
        --out data/imports/t2-work/receipts-sc-p4.json \
        abc12345 def67890 ...

Exists because this session hand-derived ``<old>--<sid>.md`` in seven inline
heredocs — the exact "second implementation of the path rule, free to drift"
that ``supersede_batch.py``'s docstring warns about. The derivation lives here
once now, and it is **verified, not trusted**: every derived digest path must
exist on disk (the writer has already run), or this script refuses. That turns
the drift risk into a hard error at build time instead of a silent
wrong-target supersede.

Prefer feeding ``supersede_batch.py --receipts`` the writer's own stdout
envelope when you still have it — that is the zero-derivation path. This tool
is for the common case where the envelope went through a summarizing filter
and only the manifest + the short-id list remain.

Read-only with respect to the Vault; writes one JSON receipts file.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from vault_paths import require_vault_root  # noqa: E402

VAULT = require_vault_root()


def derive_new_path(old_digest_path: str, short_id: str) -> str:
    """The writer's suffix rule, in one place: ``<stem>--<sid>.md``.

    Idempotent on an already-suffixed path, so a rebuilt manifest (whose
    ``digest_path`` now names the new digest) derives the same answer as the
    pre-write snapshot.
    """
    if old_digest_path.endswith(f"--{short_id}.md"):
        return old_digest_path
    return old_digest_path.replace(".md", f"--{short_id}.md")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", required=True, help="cluster manifest (pre-write paths)")
    ap.add_argument("--out", required=True, help="receipts JSON to write")
    ap.add_argument("short_ids", nargs="+", help="short ids written this batch")
    args = ap.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    rows = {r["short_id"]: r for r in manifest["items"]}

    receipts = []
    problems = []
    for sid in args.short_ids:
        row = rows.get(sid)
        if row is None:
            problems.append(f"{sid}: not in manifest")
            continue
        new = derive_new_path(row["digest_path"], sid)
        if not (VAULT / new).exists():
            problems.append(f"{sid}: derived path does not exist on disk: {new}")
            continue
        receipts.append({"provider_id": row["provider_id"], "digest": {"path": new}})

    if problems:
        for p in problems:
            print(f"  REFUSED {p}", file=sys.stderr)
        return 1

    Path(args.out).write_text(json.dumps(receipts, indent=1), encoding="utf-8")
    print(f"{len(receipts)} receipt(s) -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
