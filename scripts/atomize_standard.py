#!/usr/bin/env python
"""Atomize a standard's verbatim archive into layered ``clause`` notes.

A ``kind: reference`` note points at a flat ``.txt`` archive (``local_text`` /
``source_file``). This script turns that archive into one atomic ``clause`` note
per level-2 §X.Y section, so the standard lives in the KB as connectable notes
(the reference note composes them into the readable document). The archive stays
as the immutable source.

Pure: uses ``emptyos.sdk.standard_atomize`` (which uses ``doc_slice``) — no
kernel boot, so it's safe to run standalone while the daemon is up. Skips any
section whose clause number already has a (hand-curated) note.

    python scripts/atomize_standard.py transgrid-cdim-1-0            # dry-run
    python scripts/atomize_standard.py transgrid-cdim-1-0 --write    # write files

Block-style YAML frontmatter matches the vault convention (CLAUDE.md gotcha).
"""

from __future__ import annotations

import re
import sys
import time
import tomllib
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from emptyos.sdk.standard_atomize import plan_atomization  # noqa: E402

SOURCES = "30_Resources/EmptyOS/kb/sources"
NOTES = "30_Resources/EmptyOS/kb/notes"


def _find_reference(vault: Path, slug: str) -> Path:
    """Reference notes live under sources/ (standards) or notes/ (TBs, papers)."""
    for d in (SOURCES, NOTES):
        p = vault / d / f"{slug}.md"
        if p.exists():
            return p
    return vault / SOURCES / f"{slug}.md"  # nonexistent — caller reports it


def _vault_root() -> Path:
    cfg = tomllib.load(open(Path(__file__).resolve().parent.parent / "emptyos.toml", "rb"))
    return Path(cfg["notes"]["path"])


def _norm_edition(e) -> str:
    """Normalize an edition string for dedup comparison.

    Collapses cosmetic differences (``2015 (2.1)`` == ``2015``) so a curated
    clause note isn't duplicated by a re-run whose reference spells the edition
    differently — while keeping genuine revisions distinct (``Rev 0.2`` !=
    ``Rev 1.0``). Strips parenthetical qualifiers, lowercases, collapses space.
    """
    s = re.sub(r"\([^)]*\)", "", str(e or ""))
    return re.sub(r"\s+", " ", s).strip().lower()


def _norm_standard(value) -> str:
    """Normalize legacy ``standard:`` titles for reference-to-clause joins."""
    text = str(value or "").upper().strip()
    text = text.replace("‑", "-").replace("–", "-").replace("—", "-")
    text = re.sub(r"\s+", " ", text)
    return re.sub(r"([A-Z])(\d)", r"\1 \2", text)


def _same_standard(fm: dict, standard_id: str, standard_name: str) -> bool:
    """Accept canonical IDs or the KB's legacy normalized-title convention."""
    clause_id = str(fm.get("standard_id") or "").strip().lower()
    if clause_id and clause_id == str(standard_id or "").strip().lower():
        return True
    want_name = _norm_standard(standard_name)
    clause_name = _norm_standard(fm.get("standard"))
    return bool(want_name and clause_name and clause_name.startswith(want_name))


def _clean(text: str) -> str:
    out = [ln for ln in text.splitlines()
           if ln.strip() != "Official" and not re.match(r"^\d+\s*\|[\s|_]*$", ln.strip())]
    return "\n".join(out)


def _read_frontmatter(md: str) -> dict:
    """Minimal block-style frontmatter reader (scalars + simple block lists)."""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", md, re.S)
    if not m:
        return {}
    fm: dict = {}
    key = None
    for line in m.group(1).split("\n"):
        if re.match(r"^\s*-\s+", line) and key:
            fm.setdefault(key, [])
            if isinstance(fm[key], list):
                fm[key].append(line.split("-", 1)[1].strip().strip('"'))
            continue
        km = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", line)
        if km:
            key = km.group(1)
            val = km.group(2).strip()
            fm[key] = val.strip('"') if val else []  # empty → start of a block list
    return fm


def _yaml_scalar(v) -> str:
    if v is None:
        return "null"
    s = str(v)
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):      # ISO date — bare
        return s
    needs = (
        s == "" or s != s.strip()
        or s[0] in "#&*!?|>%@`\"'[]{},"
        or ":" in s or "[[" in s
        or s.lower() in ("true", "false", "null", "yes", "no")
        or bool(re.match(r"^[\d.+-]+$", s))      # looks numeric
    )
    if needs:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def _to_md(fm: dict, body: str) -> str:
    lines = ["---"]
    for k, v in fm.items():
        if isinstance(v, list):
            lines.append(f"{k}:")
            lines.extend(f"  - {_yaml_scalar(x)}" for x in v)
        else:
            lines.append(f"{k}: {_yaml_scalar(v)}")
    lines.append("---")
    return "\n".join(lines) + "\n\n" + body.lstrip("\n")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    write = "--write" in sys.argv
    throttle = 0.0
    for a in sys.argv:
        if a.startswith("--throttle="):
            throttle = float(a.split("=", 1)[1])
    if not args:
        print("usage: atomize_standard.py <reference-slug> [--write]")
        return 2
    ref_slug = args[0]
    vault = _vault_root()
    src_dir = vault / SOURCES

    ref_path = _find_reference(vault, ref_slug)
    if not ref_path.exists():
        print(f"reference note not found under {SOURCES} or {NOTES}: {ref_slug}.md")
        return 1
    ref_fm = _read_frontmatter(ref_path.read_text(encoding="utf-8", errors="replace"))
    standard_id = ref_fm.get("standard_id", "")
    standard_name = ref_fm.get("standard", "")
    edition = ref_fm.get("edition", "")
    archive_rel = ref_fm.get("local_text") or ref_fm.get("source_file") or ""
    if not (standard_id and archive_rel):
        print(f"reference lacks standard_id / local_text: {ref_fm.get('standard_id')!r} {archive_rel!r}")
        return 1
    archive = vault / archive_rel
    if not archive.exists():
        print(f"archive not found: {archive}")
        return 1
    txt = _clean(archive.read_text(encoding="utf-8", errors="replace"))

    # Existing clause numbers for this standard_id AND THIS EDITION only — a
    # clause that only the *other* revision has must still get a note here.
    # Edition is compared normalized (_norm_edition) so a cosmetic spelling
    # difference ("2015" vs "2015 (2.1)") still dedups, while a real revision
    # ("Rev 0.2" vs "Rev 1.0") stays distinct.
    norm_ed = _norm_edition(edition)
    existing: list[str] = []
    for f in src_dir.glob("*.md"):
        head = f.read_text(encoding="utf-8", errors="replace")[:900]
        fm = _read_frontmatter(head + "\n---\n") if "---" in head else {}
        if (fm.get("kind") == "clause"
                and _same_standard(fm, standard_id, standard_name)
                and _norm_edition(fm.get("edition", "")) == norm_ed):
            if fm.get("clause"):
                existing.append(str(fm["clause"]))

    vlevels = ref_fm.get("voltage_levels") if isinstance(ref_fm.get("voltage_levels"), list) else []
    specs = plan_atomization(
        txt,
        reference_slug=ref_slug,
        reference_title=ref_fm.get("title", ref_slug),
        standard_id=standard_id,
        edition=edition,
        domain=ref_fm.get("domain", ""),
        topic=ref_fm.get("topic", ""),
        source_file=archive_rel,
        existing_clauses=existing,
        extra_frontmatter={"voltage_levels": vlevels},
        sources_dir=SOURCES,
        today=date.today().isoformat(),
    )

    print(f"reference: {ref_slug}  standard_id={standard_id}  edition={edition}")
    print(f"existing curated clause notes: {len(existing)}")
    print(f"planned NEW clause notes: {len(specs)}")
    written = skipped = 0
    for s in specs:
        dest = vault / s.rel
        if dest.exists():
            skipped += 1
            continue
        if write:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(_to_md(s.frontmatter, s.body), encoding="utf-8")
            written += 1
            if throttle:
                time.sleep(throttle)
    if write:
        print(f"WROTE {written} notes, skipped {skipped} (already on disk)")
    else:
        print("DRY RUN — pass --write to create the notes. Sample:")
        for s in specs[:3]:
            print(f"  {s.rel}")
        print("  …")
        print(f"  {specs[-1].rel}" if specs else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
