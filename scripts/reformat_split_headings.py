#!/usr/bin/env python
"""Repair PyMuPDF-split clause headings in a stored full-text archive.

PyMuPDF extracts IEC/IEEE PDFs with the clause *number* on one line and its
*title* on the next ("4.1\\nThermal resistance ...... 10"), which defeats both
``parse_contents`` (TOC dot-leader rows) and ``slice_clause_text`` (body heading
= number + title on one line). This rejoins a bare-number line with its
following title line so the archive becomes atomic-indexable + atomize-ready.

    python scripts/reformat_split_headings.py <reference-slug>            # dry-run + atomize-plan count
    python scripts/reformat_split_headings.py <reference-slug> --write    # write *.repaired.md + repoint ref

``--drop-french`` additionally strips the French pages of a bilingual IEC
archive (running header ``CEI:YYYY`` with no ``IEC:YYYY``). Pure line surgery;
the original archive stays on disk untouched.
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from emptyos.sdk.standard_atomize import plan_atomization  # noqa: E402
from atomize_standard import _find_reference, _read_frontmatter, _vault_root  # noqa: E402  (sibling script)

SOURCES = "30_Resources/EmptyOS/kb/sources"
_BARE_NUM = re.compile(r"^\s*(\d+(?:\.\d+)*)\s*$")
_PAGE_SPLIT = re.compile(r"(?m)^<!-- Page \d+ of \d+ -->\s*$")
_FR = re.compile(r"CEI\s*:?\s*(19|20)\d{2}")
_EN = re.compile(r"IEC\s*:?\s*(19|20)\d{2}")


def drop_french_pages(text: str) -> tuple[str, int, int]:
    """Drop bilingual-IEC pages whose running header is French (CEI:YYYY, no IEC:YYYY)."""
    chunks = re.split(r"(?m)(^<!-- Page \d+ of \d+ -->\s*$)", text)
    out: list[str] = []
    kept = dropped = 0
    i = 0
    while i < len(chunks):
        seg = chunks[i]
        if _PAGE_SPLIT.match(seg or ""):
            body = chunks[i + 1] if i + 1 < len(chunks) else ""
            if _FR.search(body) and not _EN.search(body):
                dropped += 1
                i += 2
                continue
            out.append(seg)
            out.append(body)
            kept += 1
            i += 2
        else:
            out.append(seg)
            i += 1
    return "".join(out), kept, dropped


def repair(text: str) -> tuple[str, int]:
    """Rejoin each bare clause-number line with the title line that follows it."""
    lines = text.splitlines()
    out: list[str] = []
    joined = 0
    i = 0
    while i < len(lines):
        m = _BARE_NUM.match(lines[i])
        if m and i + 1 < len(lines):
            nxt = lines[i + 1].strip()
            # next line must be title-ish: non-empty, not another number/marker
            if nxt and not _BARE_NUM.match(lines[i + 1]) and not nxt.startswith("<!--") \
                    and not nxt.startswith("---") and nxt[0].isalpha():
                out.append(f"{m.group(1)} {nxt}")
                i += 2
                joined += 1
                continue
        out.append(lines[i])
        i += 1
    return "\n".join(out), joined


_TOC_ROW_START = re.compile(r"^\s*\d+(?:\.\d+)*\.?\s+\S")
_LEADER_END = re.compile(r"\.{2,}\s*\d+\s*$")


def rejoin_wrapped_toc_rows(text: str) -> tuple[str, int]:
    """Rejoin a TOC row whose title wrapped onto the next line.

    ``2.2 Impedance-correction factors ... of\\ngenerators, ... units.....31``
    becomes one dot-leader row so ``parse_contents`` can see the section. Only
    fires when the continuation line ends with a dot-leader + page number and
    is not itself a numbered row, so body prose is never touched.
    """
    lines = text.splitlines()
    out: list[str] = []
    joined = 0
    i = 0
    while i < len(lines):
        ln = lines[i]
        if (_TOC_ROW_START.match(ln) and not _LEADER_END.search(ln)
                and i + 1 < len(lines)):
            nxt = lines[i + 1]
            if _LEADER_END.search(nxt) and not _TOC_ROW_START.match(nxt):
                out.append(f"{ln.rstrip()} {nxt.strip()}")
                joined += 1
                i += 2
                continue
        out.append(ln)
        i += 1
    return "\n".join(out), joined


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    write = "--write" in sys.argv
    if not args:
        print("usage: reformat_split_headings.py <reference-slug> [--write]")
        return 2
    ref_slug = args[0]
    vault = _vault_root()
    ref_path = _find_reference(vault, ref_slug)
    if not ref_path.exists():
        print(f"reference note not found (kb/sources or kb/notes): {ref_slug}.md")
        return 1
    ref_md = ref_path.read_text(encoding="utf-8", errors="replace")
    fm = _read_frontmatter(ref_md)
    archive_rel = fm.get("local_text") or fm.get("source_file") or ""
    archive = vault / archive_rel
    if not archive.exists():
        print(f"archive not found: {archive}")
        return 1

    raw = archive.read_text(encoding="utf-8", errors="replace")
    print(f"reference: {ref_slug}")
    print(f"archive:   {archive_rel}")
    if "--drop-french" in sys.argv:
        raw, kept, dropped = drop_french_pages(raw)
        print(f"french pages dropped: {dropped} (kept {kept})")
    repaired, joined = repair(raw)
    print(f"rejoined split headings: {joined}")
    repaired, wrapped = rejoin_wrapped_toc_rows(repaired)
    print(f"rejoined wrapped TOC rows: {wrapped}")

    specs = plan_atomization(
        repaired,
        reference_slug=ref_slug,
        reference_title=fm.get("title", ref_slug),
        standard_id=fm.get("standard_id", ""),
        edition=fm.get("edition", ""),
        source_file=archive_rel,
        sources_dir=SOURCES,
        today=date.today().isoformat(),
    )
    print(f"atomize plan (level-2 sections): {len(specs)} clause notes")
    for s in specs[:8]:
        print(f"  §{s.clause}  {s.frontmatter.get('clause_title','')[:60]}")
    if len(specs) > 8:
        print(f"  … (+{len(specs)-8} more)")

    if write:
        dest = archive.with_suffix(".repaired.md")
        dest.write_text(repaired, encoding="utf-8")
        new_rel = str(dest.relative_to(vault)).replace("\\", "/")
        key = "local_text" if "local_text:" in ref_md else "source_file"
        new_md = re.sub(rf'(?m)^{key}:\s*.*$', f'{key}: {new_rel}', ref_md, count=1)
        ref_path.write_text(new_md, encoding="utf-8")
        print(f"WROTE {dest.relative_to(vault)}")
        print(f"repointed reference {key} -> {new_rel}")
    else:
        print("DRY RUN — pass --write to emit *.repaired.md and repoint the reference.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
