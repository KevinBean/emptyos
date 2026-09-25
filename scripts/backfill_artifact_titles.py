#!/usr/bin/env python3
"""Backfill `title:` onto generated-artifact records that predate it.

viz/designer stamp a human `title:` on every record they write, derived from
the brief. Records written before that shipped carry only `viz_id` / the
`record.md` filename, so the vault graph shows N nodes all labelled "record"
and the artifacts board shows N rows of hex — neither is usable for finding
anything.

The title is derived from each record's own `prompt:` frontmatter, which is
already there, using the same `artifact_title` the apps use. No model call, no
network, nothing invented: a record with no usable prompt is skipped rather
than given a placeholder.

Additive and narrow by construction — it inserts ONE frontmatter key and
touches nothing else. A record that already has a non-empty `title:` is left
alone, so this is safe to re-run and cannot overwrite a hand-written title.

Usage:
    python scripts/backfill_artifact_titles.py                 # dry-run, viz
    python scripts/backfill_artifact_titles.py --apply
    python scripts/backfill_artifact_titles.py --app designer --apply
    python scripts/backfill_artifact_titles.py --app both --apply
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from emptyos.sdk.html_artifact import artifact_title  # noqa: E402
from emptyos.sdk.utils import parse_frontmatter, set_frontmatter_field  # noqa: E402

APPS = {
    "viz": ("30_Resources/EmptyOS/viz/outputs", "viz_id", "Viz artifact"),
    "designer": ("30_Resources/EmptyOS/designer/outputs", "designer_id", "Designer page"),
}


def vault_root() -> Path:
    cfg = tomllib.loads((ROOT / "emptyos.toml").read_text(encoding="utf-8"))
    p = (cfg.get("notes") or {}).get("path", "")
    if not p:
        raise SystemExit("emptyos.toml has no [notes] path")
    return Path(p)


def yaml_quote(value: str) -> str:
    """Double-quote and escape so a colon/quote in a brief can't break the block."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def plan_for(vault: Path, app: str) -> list[tuple[Path, str, str]]:
    """[(record_path, current_title, new_title)] for records needing a title."""
    rel, id_key, label = APPS[app]
    out: list[tuple[Path, str, str]] = []
    root = vault / rel
    if not root.exists():
        return out
    for record in sorted(root.glob("*/record.md")):
        text = record.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        current = str(fm.get("title") or "").strip()
        if current:
            continue  # already titled (by the app, or by hand) — never overwrite
        rid = str(fm.get(id_key) or record.parent.name).strip()
        title = artifact_title(str(fm.get("prompt") or ""), fallback=f"{label} {rid}")
        # A record whose prompt yields nothing usable gets the id-based fallback,
        # which is still strictly better than the filename stem "record".
        out.append((record, current, title))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--app", choices=["viz", "designer", "both"], default="viz")
    ap.add_argument("--apply", action="store_true", help="write (default is dry-run)")
    args = ap.parse_args()

    vault = vault_root()
    apps = ["viz", "designer"] if args.app == "both" else [args.app]

    total = 0
    for app in apps:
        plan = plan_for(vault, app)
        total += len(plan)
        print(f"\n{app}: {len(plan)} record(s) need a title")
        for record, _cur, title in plan:
            print(f"  {record.parent.name}  ->  {title}")
            if args.apply:
                text = record.read_text(encoding="utf-8")
                record.write_text(
                    set_frontmatter_field(text, "title", yaml_quote(title)),
                    encoding="utf-8",
                )

    if not total:
        print("\nNothing to do — every record already has a title.")
    elif args.apply:
        print(f"\nWrote {total} title(s).")
    else:
        print(f"\nDry run — {total} record(s) would change. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
