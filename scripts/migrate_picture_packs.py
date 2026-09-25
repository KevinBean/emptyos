#!/usr/bin/env python3
"""Migrate the dictionary's picture packs to the shared object store.

    packs/animals.jsonl                 ->  packs/objects.jsonl      (identity)
                                        +   packs/animals.pack.json  (membership)
                                        +   packs/meta.json          (schema 2)

An object is stored once and referred to from many packs. Before the split,
membership lived inside the object row — where there is only room for one — so
the second pack to claim a slug lost it silently. See picture_catalog.py.

Run order:

    python scripts/migrate_picture_packs.py            # write the new layout
    python scripts/migrate_picture_packs.py --verify   # prove it changed nothing

``--verify`` is the gate, and it does not take the migration's word for anything:
it pulls the pre-migration ``animals.jsonl`` out of git, runs the REAL
``picture_catalog.load_packs`` over both layouts, and compares the indexes the
app actually consumes. Then it checks the live learner stores, because "no
progress orphaned" is a claim about real data, not about a data structure.

Exits non-zero on any mismatch. Prints what differs; never edits anything under
``--verify``.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "apps" / "extension" / "english-learning" / "dictionary"
PACKS = APP / "packs"
LEGACY_REL = "apps/extension/english-learning/dictionary/packs/animals.jsonl"

# The learner stores the migration must not orphan.
PROGRESS = ROOT / "data" / "apps" / "dictionary" / "picture-progress.json"
IMAGE_INDEX = ROOT / "data" / "apps" / "dictionary" / "images" / "_index.json"


def _catalog():
    """The real loader, imported as a pure module (no kernel, no daemon)."""
    spec = importlib.util.spec_from_file_location("_mig_catalog", APP / "picture_catalog.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _read_legacy_rows(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _legacy_from_git(rev: str = "") -> str:
    """The pre-migration animals.jsonl, from git.

    Walks the file's history rather than assuming a rev, so --verify keeps
    working after the file is deleted in this same commit.
    """
    if rev:
        revs = [rev]
    else:
        out = subprocess.run(
            ["git", "log", "--format=%H", "--", LEGACY_REL],
            cwd=ROOT, capture_output=True, check=True,
        ).stdout.decode("ascii")
        revs = out.split()
    for r in revs:
        # Bytes, not text=True: the pack is full of 中文 and emoji, and the
        # default decode here is the console codepage (cp1252 on Windows).
        got = subprocess.run(["git", "show", f"{r}:{LEGACY_REL}"],
                             cwd=ROOT, capture_output=True)
        if got.returncode == 0 and got.stdout.strip():
            return got.stdout.decode("utf-8")
    raise SystemExit(f"could not find {LEGACY_REL} in git history")


# ─── Write ───────────────────────────────────────────────────────────


def migrate() -> int:
    cat = _catalog()
    legacy = PACKS / "animals.jsonl"
    if not legacy.exists():
        print(f"nothing to do: {legacy.name} is already gone")
        return 0

    rows = _read_legacy_rows(legacy.read_text(encoding="utf-8"))
    meta = json.loads((PACKS / "meta.json").read_text(encoding="utf-8"))
    pack_row = next((p for p in meta.get("packs", []) if p.get("id") == "animals"), {})

    # objects.jsonl — every key the row had, minus `category`, in its original
    # order. The per-line diff is then literally "the category was removed",
    # which is the point: a reviewer can see that nothing else moved.
    obj_lines = []
    groups: dict[str, list[str]] = {}
    group_order: list[str] = []
    for row in rows:
        obj = {k: v for k, v in row.items() if k != "category"}
        obj_lines.append(json.dumps(obj, ensure_ascii=False))
        gid = str(row["category"]).strip()
        if gid not in groups:
            groups[gid] = []
            group_order.append(gid)
        groups[gid].append(str(row["slug"]).strip())

    (PACKS / "objects.jsonl").write_text("\n".join(obj_lines) + "\n", encoding="utf-8")

    # animals.pack.json — membership, plus the labels that used to be a module
    # constant in the loader. A label is a pack's property, not a global.
    pack_doc = {
        "id": "animals",
        "groups": [
            {"id": gid,
             "label": cat.LEGACY_CATEGORY_LABELS.get(gid, gid.replace("-", " ").capitalize()),
             "members": groups[gid]}
            for gid in group_order
        ],
    }
    (PACKS / "animals.pack.json").write_text(
        json.dumps(pack_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # meta.json — schema 2. `categories` is dropped: the loader never read it,
    # and group ids now live in the pack file beside their labels.
    new_meta = {
        "schema": 2,
        "objects": "objects.jsonl",
        "packs": [{
            "id": "animals",
            "title": pack_row.get("title", "Animals"),
            "emoji": pack_row.get("emoji", ""),
            "members": "animals.pack.json",
        }],
    }
    if meta.get("attribution"):
        new_meta["attribution"] = meta["attribution"]
    (PACKS / "meta.json").write_text(
        json.dumps(new_meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    legacy.unlink()

    print(f"wrote objects.jsonl        {len(obj_lines)} objects")
    print(f"wrote animals.pack.json    {len(group_order)} groups "
          f"({', '.join(f'{g} {len(groups[g])}' for g in group_order)})")
    print("wrote meta.json            schema 2")
    print(f"removed {legacy.name}")
    print("\nnow run: python scripts/migrate_picture_packs.py --verify")
    return 0


# ─── Verify ──────────────────────────────────────────────────────────


OBJECT_FIELDS = ("name", "chinese", "pinyin", "emoji", "wiki", "hint", "image_hint")


def verify(rev: str = "") -> int:
    cat = _catalog()
    problems: list[str] = []

    with tempfile.TemporaryDirectory() as td:
        old_dir = Path(td)
        (old_dir / "animals.jsonl").write_text(_legacy_from_git(rev), encoding="utf-8")
        (old_dir / "meta.json").write_text(json.dumps({"packs": [
            {"id": "animals", "title": "Animals", "file": "animals.jsonl"}]}),
            encoding="utf-8")
        old = cat.load_packs(old_dir)

    full = cat.load_packs(PACKS)

    # Scope the comparison to the Animals pack. The object store is shared and
    # grows with every new pack, so comparing whole-store totals against an
    # animals-only baseline would start failing the moment a second pack ships —
    # and would say nothing about whether Animals itself is intact, which is the
    # only thing this gate is about.
    animals = set(full["by_pack"].get("animals", []))
    new = {
        "items": {k: v for k, v in full["items"].items() if k in animals},
        "order": [s for s in full["order"] if s in animals],
        "by_category": {k: v for k, v in full["by_category"].items()
                        if k.startswith("animals:")},
        "categories": [c for c in full["categories"] if c.get("pack") == "animals"],
        "warnings": full["warnings"],
    }

    def note(cond, msg):
        if not cond:
            problems.append(msg)

    note(set(old["items"]) == set(new["items"]),
         f"object set differs: only-old={sorted(set(old['items']) - set(new['items']))[:8]} "
         f"only-new={sorted(set(new['items']) - set(old['items']))[:8]}")
    note(old["order"] == new["order"], "load order differs")

    for slug in sorted(set(old["items"]) & set(new["items"])):
        a, b = old["items"][slug], new["items"][slug]
        diff = {f: (a.get(f), b.get(f)) for f in OBJECT_FIELDS if a.get(f) != b.get(f)}
        note(not diff, f"{slug}: object body changed {diff}")

    note(old["by_category"] == new["by_category"], "membership differs")
    old_labels = {c["id"]: c["label"] for c in old["categories"]}
    new_labels = {c["id"]: c["label"] for c in new["categories"]}
    note(old_labels == new_labels,
         f"group labels differ: {[(k, old_labels.get(k), new_labels.get(k)) for k in set(old_labels) | set(new_labels) if old_labels.get(k) != new_labels.get(k)]}")

    note(not new["warnings"], f"the migrated pack does not load clean: {new['warnings']}")

    # The orphan gate. Progress and the image cache are keyed by slug globally,
    # so a slug the migration lost takes a learner's history with it. Checked
    # against the live stores rather than asserted.
    for label, path, keyfn in (
        ("picture-progress.json", PROGRESS, lambda d: list(d)),
        ("images/_index.json", IMAGE_INDEX, lambda d: list(d)),
    ):
        if not path.exists():
            print(f"  (skipped {label} — not present on this machine)")
            continue
        try:
            keys = keyfn(json.loads(path.read_text(encoding="utf-8")))
        except Exception as e:
            problems.append(f"{label}: unreadable ({e})")
            continue
        orphans = [k for k in keys if k not in full["items"]]
        note(not orphans, f"{label}: {len(orphans)} orphaned key(s) {orphans[:8]}")

    if problems:
        print("VERIFY FAILED\n")
        for p in problems:
            print(f"  - {p}")
        return 1

    counts = {k: len(v) for k, v in new["by_category"].items()}
    print("VERIFY OK  (scoped to the animals pack)")
    print(f"  objects   {len(new['items'])} (order preserved, bodies identical)")
    print(f"  groups    {counts}")
    print(f"  labels    {sorted(new_labels.values())}")
    print("  progress  no orphaned keys in the live learner stores")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--verify", action="store_true",
                    help="compare old and new through the real loader; write nothing")
    ap.add_argument("--rev", default="",
                    help="git rev holding the pre-migration animals.jsonl (default: search history)")
    args = ap.parse_args()
    return verify(args.rev) if args.verify else migrate()


if __name__ == "__main__":
    sys.exit(main())
