#!/usr/bin/env python3
"""Install a verified candidate list as a picture pack.

The write step of pack authoring, and deliberately the *only* one: candidates are
produced by hand or by a model, checked by ``verify_pack_titles.py``, and only
then installed here. Nothing in this script talks to Wikimedia — if a row was
never verified, this will happily write a pack that renders emoji tiles forever.

    python scripts/install_pack.py candidates.json --id food --title "Food" --emoji 🥕

A candidate row needs ``slug``, ``name``, ``chinese``, ``wiki`` and ``group``;
``pinyin``, ``emoji``, ``hint`` and ``image_hint`` are optional. ``group`` is the
pack-scoped category and never touches the object.

**An object already in the store is reused, not duplicated** — that is the whole
point of the split. A candidate whose slug already exists contributes a
membership row only, so it keeps its one progress record and its one cached
photo. The run reports how many were reused, and refuses when a reused slug
carries a *different* ``wiki`` than the shipped object, because silently
retitling an object that a downloaded photo and a learner's history are keyed to
is not a decision a script gets to make.

Writes nothing until every check passes. --dry-run prints the plan and exits.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKS = ROOT / "apps" / "public" / "englishos" / "dictionary" / "packs"

OBJECT_KEYS = ("slug", "name", "chinese", "pinyin", "emoji", "wiki", "hint", "image_hint")
REQUIRED = ("slug", "name", "chinese", "wiki", "group")


def _load_objects() -> dict[str, dict]:
    p = PACKS / "objects.jsonl"
    if not p.exists():
        return {}
    out = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            row = json.loads(line)
            out[row["slug"]] = row
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("candidates")
    ap.add_argument("--id", required=True, help="pack id, e.g. food")
    ap.add_argument("--title", required=True)
    ap.add_argument("--emoji", default="")
    ap.add_argument("--labels", default="", help='JSON map of group id -> label')
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    labels = json.loads(args.labels) if args.labels else {}
    existing = _load_objects()

    problems: list[str] = []
    new_objects: list[dict] = []
    groups: dict[str, list[str]] = {}
    group_order: list[str] = []
    reused: list[str] = []
    seen: set[str] = set()

    for i, row in enumerate(rows, 1):
        missing = [f for f in REQUIRED if not str(row.get(f) or "").strip()]
        if missing:
            problems.append(f"row {i}: missing {missing}")
            continue
        slug = str(row["slug"]).strip()
        if not re.fullmatch(r"[a-z0-9-]+", slug):
            problems.append(f"row {i}: slug {slug!r} is not url-safe")
            continue
        if slug in seen:
            problems.append(f"row {i}: {slug!r} appears twice in the candidates")
            continue
        seen.add(slug)

        if slug in existing:
            shipped_wiki = str(existing[slug].get("wiki") or "")
            if shipped_wiki != str(row["wiki"]).strip():
                problems.append(
                    f"row {i}: {slug!r} is already in the store with wiki "
                    f"{shipped_wiki!r}, candidate says {row['wiki']!r} — resolve by hand"
                )
                continue
            reused.append(slug)
        else:
            obj = {k: str(row.get(k) or "").strip() for k in OBJECT_KEYS
                   if str(row.get(k) or "").strip()}
            new_objects.append(obj)

        gid = str(row["group"]).strip()
        if gid not in groups:
            groups[gid] = []
            group_order.append(gid)
        groups[gid].append(slug)

    thin = {g: len(s) for g, s in groups.items() if len(s) < 4}
    if thin:
        problems.append(
            f"groups too small to build a 4-option quiz round: {thin} "
            "(merge them, or add members)"
        )

    print(f"pack {args.id!r}: {len(new_objects)} new objects, {len(reused)} reused, "
          f"{len(group_order)} groups")
    for g in group_order:
        print(f"    {g:<14} {len(groups[g]):>3}  {', '.join(groups[g][:6])}"
              + (" ..." if len(groups[g]) > 6 else ""))
    if reused:
        print(f"  reusing (one object, one photo, one progress row): {', '.join(reused)}")

    if problems:
        print("\nREFUSED — nothing written:")
        for p in problems:
            print(f"  - {p}")
        return 1
    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    # objects.jsonl — append only; an existing object is never rewritten.
    if new_objects:
        with (PACKS / "objects.jsonl").open("a", encoding="utf-8", newline="\n") as f:
            for obj in new_objects:
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    (PACKS / f"{args.id}.pack.json").write_text(json.dumps({
        "id": args.id,
        "groups": [{"id": g,
                    "label": labels.get(g, g.replace("-", " ").capitalize()),
                    "members": groups[g]} for g in group_order],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    meta_path = PACKS / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.setdefault("packs", [])
    meta["packs"] = [p for p in meta["packs"] if p.get("id") != args.id]
    meta["packs"].append({"id": args.id, "title": args.title,
                          "emoji": args.emoji, "members": f"{args.id}.pack.json"})
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")

    print(f"\nwrote {args.id}.pack.json, appended {len(new_objects)} objects, "
          f"registered in meta.json")
    print("the photos are NOT fetched yet, and nothing has been reviewed by eye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
