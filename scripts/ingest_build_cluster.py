#!/usr/bin/env python3
"""Build a T2 cluster manifest — the cluster is T2's real unit of work.

Registry-driven since 2026-08-07: ``--cluster-id <id>`` loads tag/artifacts/
task/out from ``ingest_clusters.json`` beside this script (EMFieldCalc, cabletool,
simplecalc); explicit flags override individual fields. The EMF story below is
the origin case and still the best explanation of why clusters exist.

The T1 thin-digest gate (``message_count >= 7 AND digest body < 300 chars``) is
a per-record threshold, and inside a verified project it has roughly half the
recall you would want: of the 34 conversations that built this calculator,
2025-04-30 to 2025-05-08, it flags 18. The 16 it misses are not better digested
— they are *equally* thin, and escape only by having fewer than 7 messages or by
crossing 300 characters by a handful. The largest digest in the whole eight-day
arc, before re-digestion started, was 567 characters.

So the second signal the gate wants is **cluster membership**, and the vault
already carries it: the `emfieldcalc` tag, applied by an earlier pass. Two
structurally different detections agree on the same 34 (the rule this backlog
imposes on itself for any heuristic finding):

  * tag scan   — `emfieldcalc` on the digest;
  * artefact   — the calculator's filename in the source body, across its four
                 renames plus the multi-file variants.

Read-only. Writes one manifest to data/imports/.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from vault_paths import require_vault_root  # noqa: E402

VAULT = require_vault_root()
CONV = VAULT / "30_Resources/conversations"
ORIGINALS = CONV / "originals/claude"
# Anchored to the repo, not the cwd — this is run from wherever, and a relative
# path here would silently resolve against the caller's directory.
QUEUE = REPO / "data/imports/t2-thin-digest-queue.json"
OUT = REPO / "data/imports/t2-emfieldcalc-cluster.json"

DIGEST_RE = re.compile(r"^## Digest\s*\n(.*?)(?=\n## |\Z)", re.S | re.M)
SID_RE = re.compile(r"source_conversation_id:\s*['\"]?([0-9a-f-]{36})")

# Done is the T1 bar itself, not a tag. An earlier version keyed off spec tags
# (`port-fidelity` / `defect-found`) and mis-reported the arc's flagship record
# as outstanding, because the tag set is whatever the reviewed spec said — see
# the caveat below.
DONE_MIN_CHARS = 800

# CAVEAT, found 2026-08-06: a re-digest rewrites `tags:` wholesale from the
# spec, so a member can be silently evicted from its own cluster by the act of
# being properly digested. `02741e03` — the 64-message record that found the
# 48 % error, the largest in the arc — lost `emfieldcalc` that way and was
# invisible to this scan until the tag was restored by hand. Any spec for a
# cluster member must carry the cluster tag.


def digest_chars(text: str) -> int:
    m = DIGEST_RE.search(text)
    return len(m.group(1).strip()) if m else 0


def tag_in_frontmatter(text: str, tag: str) -> bool:
    """True iff the digest's FRONTMATTER tags block carries ``tag``.

    A plain substring check over the whole digest turned prose mentions into
    members: a cable-tool digest saying "the simplecalc thread becomes the
    main line" joined the simplecalc cluster (caught 2026-08-07). Membership
    must be structural — a block-style ``- <tag>`` line inside the leading
    frontmatter — never textual.
    """
    fm_end = text.find("\n---", 4)
    frontmatter = text[:fm_end] if fm_end > 0 else text[:2000]
    return bool(re.search(rf"^\s*-\s*{re.escape(tag)}\s*$", frontmatter, re.M))


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cluster-id", default=None,
                    help="load tag/artifacts/task/out from clusters.json next to "
                         "this script; explicit flags override individual fields")
    ap.add_argument("--tag", default=None, help="cluster tag on the digest")
    ap.add_argument("--artifact", action="append", default=None,
                    help="corroboration filename in the SOURCE body (repeatable); "
                         "a record joins the cluster on tag OR artifact match")
    ap.add_argument("--queue", default=str(QUEUE), help="flagged-queue json for t1_flagged")
    ap.add_argument("--out", default=None, help="manifest output path")
    ap.add_argument("--task", default=None, help="manifest task name")
    args = ap.parse_args()

    # The registry is the durable record of each cluster's parameters — the
    # cabletool artifact set originally lived only in a shell history and had
    # to be reconstructed from the emitted manifest's criterion string.
    reg: dict = {}
    if args.cluster_id:
        registry = json.loads(
            (Path(__file__).parent / "ingest_clusters.json").read_text(encoding="utf-8"))
        reg = registry.get(args.cluster_id)
        if args.cluster_id.startswith("_") or not isinstance(reg, dict):
            known = [k for k in registry if not k.startswith("_")]
            raise SystemExit(f"unknown cluster id {args.cluster_id!r}; known: {known}")
    tag = args.tag or reg.get("tag") or "emfieldcalc"
    artifacts = args.artifact if args.artifact is not None else reg.get("artifacts", [])
    queue_path = Path(args.queue)
    out_path = Path(args.out or (REPO / reg["out"] if reg.get("out") else OUT))
    task_name = args.task or reg.get("task") or "T2-emfieldcalc-cluster"

    flagged = {
        i["provider_id"][:8]: i
        for i in json.loads(queue_path.read_text(encoding="utf-8"))["items"]
    }

    originals_dirs = [ORIGINALS, CONV / "originals/chatgpt"]

    def find_src(short: str):
        for od in originals_dirs:
            if od.is_dir():
                hits = sorted(od.glob(f"*--{short}.md"))
                if hits:
                    return hits[0]
        return None

    # Artifact corroboration: scan source bodies for the named filenames and
    # collect matching short_ids, so a member missing the tag still joins.
    artifact_shorts: set[str] = set()
    if artifacts:
        for od in originals_dirs:
            if not od.is_dir():
                continue
            for sp in od.glob("*.md"):
                m2 = re.search(r"--([0-9a-f]{8})\.md$", sp.name)
                if not m2:
                    continue
                body = sp.read_text(encoding="utf-8", errors="replace")
                if any(a in body for a in artifacts):
                    artifact_shorts.add(m2.group(1))

    rows: dict[str, dict] = {}
    for path in sorted(CONV.glob("*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "record_kind: conversation-digest" not in text:
            continue
        m = SID_RE.search(text)
        if not m:
            continue
        sid = m.group(1)
        short = sid[:8]
        if not tag_in_frontmatter(text, tag) and short not in artifact_shorts:
            continue
        chars = digest_chars(text)
        prior = rows.get(short)
        # A superseded stub sits beside its replacement; the longer one is
        # canonical and the stub carries a `## Superseded` block.
        if prior and prior["digest_chars"] >= chars:
            continue
        srcp = find_src(short)
        src = [srcp] if srcp else []
        msgs = None
        if src:
            mm = re.search(r"^message_count:\s*(\d+)", src[0].read_text(
                encoding="utf-8", errors="replace"), re.M)
            msgs = int(mm.group(1)) if mm else None
        rows[short] = {
            "short_id": short,
            "provider_id": sid,
            "date": path.name[:10],
            "title": path.stem,
            "digest_path": f"30_Resources/conversations/{path.name}",
            "source_path": (
                str(src[0].relative_to(VAULT)).replace("\\", "/") if src else None
            ),
            "source_kb": (src[0].stat().st_size // 1024) if src else None,
            "message_count": msgs,
            "digest_chars": chars,
            "t1_flagged": short in flagged,
            "done": chars >= DONE_MIN_CHARS,
        }

    items = sorted(rows.values(), key=lambda r: (r["date"], r["short_id"]))
    done = [r for r in items if r["done"]]
    todo = [r for r in items if not r["done"]]
    payload = {
        "schema_version": 1,
        "task": task_name,
        "criterion": f"digest carries the `{tag}` tag OR source contains one of {artifacts or ['(no artifact set)']}",
        "total": len(items),
        "done": len(done),
        "remaining": len(todo),
        "t1_flagged": sum(1 for r in items if r["t1_flagged"]),
        "t1_recall": round(sum(1 for r in items if r["t1_flagged"]) / max(len(items), 1), 3),
        "source_bytes_total_kb": sum(r["source_kb"] or 0 for r in items),
        "date_range": [items[0]["date"], items[-1]["date"]] if items else None,
        "items": items,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{len(items)} cluster members  ({len(done)} done, {len(todo)} remaining)")
    print(f"T1 gate flags {payload['t1_flagged']} of {len(items)} — recall {payload['t1_recall']:.0%}")
    print(f"source archives total {payload['source_bytes_total_kb']} KB")
    print(f"-> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
