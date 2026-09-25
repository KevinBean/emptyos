#!/usr/bin/env python3
"""Stage cleaned copies of the KB notes a hosted Cable Pulling+ build publishes.

    python scripts/stage_cable_pulling_hosted_kb.py [--out <vault-relative dir>] [--overwrite]

Reads each note in ``HOSTED_KB_SLUGS`` from the vault (path from emptyos.toml
``[notes].path``), runs ``clean_note`` (apps/extension/engineering/
cable-pulling/pulling_hosted.py) and writes the result plus a review sheet
(``_review.md``) to a REVIEW folder in the vault — never into
``portfolio/vault``: anything there ships with the next portfolio release.
The originals are never touched.

The cleaner substitutes only phrases it can reword safely; every other
problem (a name, a link to an unpublished note, a removed-link marker,
conversation leftovers) stays visible in the copy and is listed on the review
sheet for a human to reword. A staged copy that already exists is never
overwritten without ``--overwrite``, so re-running does not erase that
editing. Copy the reviewed notes into the deployment's vault only after a
human has read them (plan ``10_Projects/emptyos/log/_plans/cable-pulling-hosted.md`` T7).
Exit 1 if a note is missing or still needs rewording.
"""

from __future__ import annotations

import argparse
import re
import importlib.util
import sys
import tomllib
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_MOD = ROOT / "apps" / "extension" / "engineering" / "cable-pulling" / "pulling_hosted.py"
DEFAULT_OUT = "30_Resources/EmptyOS/cable-pulling/outputs/hosted-kb"


def _load_hosted():
    spec = importlib.util.spec_from_file_location("cable_pulling_hosted", _MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _find(vault: Path, slug: str) -> Path | None:
    hits = [p for p in (vault / "30_Resources").rglob(f"{slug}.md")
            if ".stversions" not in p.parts and "outputs" not in p.parts]
    return hits[0] if len(hits) == 1 else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=DEFAULT_OUT, help="vault-relative review folder")
    ap.add_argument("--vault", help="vault root (default: emptyos.toml [notes].path)")
    ap.add_argument("--overwrite", action="store_true",
                    help="replace staged copies that already exist (discards any rewording in them)")
    args = ap.parse_args(argv)
    h = _load_hosted()
    try:
        vault = Path(args.vault or tomllib.loads(
            (ROOT / "emptyos.toml").read_text(encoding="utf-8"))["notes"]["path"])
    except (OSError, KeyError, ValueError) as exc:
        print(f"no vault configured (emptyos.toml [notes].path): {exc}", file=sys.stderr)
        return 1
    out_dir = vault / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    review = [f"# Hosted KB notes — review sheet ({date.today().isoformat()})", "",
              "Cleaned copies of the notes a hosted Cable Pulling+ build publishes. Read each",
              "note before it is copied into a deployment's vault. Originals are untouched.", ""]
    problems = 0
    headings: dict[str, set[str]] = {}
    for slug in sorted(h.HOSTED_KB_SLUGS):
        # A renamed note is read from its vault slug and staged under its
        # public name (pulling_hosted.PUBLIC_SLUGS).
        src = _find(vault, h.VAULT_SLUGS.get(slug, slug))
        if src is None:
            review += [f"## {slug}", "", "**Not found** (or found more than once) — not staged.", ""]
            problems += 1
            continue
        cleaned, log = h.clean_note(src.read_text(encoding="utf-8"))
        dest = out_dir / f"{slug}.md"
        if dest.exists() and not args.overwrite:
            # Report on the copy a human may have edited, not on a fresh clean.
            cleaned = dest.read_text(encoding="utf-8")
            kept = True
        else:
            dest.write_text(cleaned, encoding="utf-8")
            kept = False
        headings[slug] = set(re.findall(r"(?m)^#{1,6}[ \t]+(.+?)[ \t]*$", cleaned))
        flags = h.residual_flags(cleaned)
        superseded = h.superseded_sections(cleaned)
        problems += bool(flags)
        review += [f"## {slug}", "",
                   f"- Source: `{src.relative_to(vault).as_posix()}`"
                   + (" — existing staged copy kept (use --overwrite to re-clean)" if kept else ""),
                   f"- Frontmatter dropped: {', '.join(log['frontmatter_dropped']) or 'none'}; "
                   f"related entries dropped: {log['related_dropped']}",
                   f"- HTML comments removed: {log['comments']}; sections removed: {log['sections_dropped']}; "
                   f"links to unpublished notes removed: {log['links_removed']}",
                   "- Replacements: " + (", ".join(f"“{k}” ×{v}" for k, v in log["replacements"].items()) or "none")]
        if superseded:
            review.append("- **Your call:** the note corrects itself in "
                          + "; ".join(f"“{t}”" for t in superseded)
                          + ". Published as-is it states a claim and later retracts it — fold the correction in first?")
        unverified = h.unverified_markers(cleaned)
        if unverified:
            review.append(f"- **Your call:** {len(unverified)} `[unverified …]` note(s)-to-self — "
                          "verify, reword, or keep as an honest caveat:")
            review += [f"  - line {u}" for u in unverified]
        if flags:
            review.append("- **Reword before publishing** (a name, a link the reader cannot open, "
                          "a removed-link marker, conversation leftovers, or broken grammar):")
            lines = cleaned.split("\n")
            for f in flags:
                n = int(f.split(":", 1)[0])
                terms = ", ".join(dict.fromkeys(h.flag_terms(lines[n - 1])))
                review.append(f"  - line {n} ({terms}): {lines[n - 1].strip()[:200]}")
        review.append("")
    # A "why?" link or an info button opens a section by its exact heading; a
    # staged copy that renamed or dropped it gives an empty popover.
    missing = [(slug, sec) for slug, sec in h.HOSTED_SECTION_REFS
               if slug in headings and sec not in headings[slug]]
    if missing:
        problems += len(missing)
        review += ["## Sections the app opens that the staged copies lack", ""]
        review += [f"- `{slug}` has no heading “{sec}”" for slug, sec in missing]
        review.append("")
    # A file left in the folder that is not in the publish list (a note
    # staged under an old name) would ship if the folder is copied whole.
    stale = sorted(p.name for p in out_dir.glob("*.md")
                   if p.name != "_review.md" and p.stem not in h.HOSTED_KB_SLUGS)
    if stale:
        problems += len(stale)
        review += ["## Not in the publish list — delete before copying the folder", ""]
        review += [f"- `{name}`" for name in stale]
        review.append("")
    (out_dir / "_review.md").write_text("\n".join(review), encoding="utf-8")
    print(f"staged {len(h.HOSTED_KB_SLUGS)} notes -> {out_dir}  ({problems} need attention)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
