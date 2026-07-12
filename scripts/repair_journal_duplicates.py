"""Repair journal notes that were corrupted by the textarea-dump bug.

The bug (fixed in f4254be): loadToday() dumped the full rendered ### Journal
section into the entry textarea; every autosave/submit POSTed that back as a
new entry's text, cascading. Affected files on Kevin's vault (as of
2026-04-19): 2026-04-07, 2026-04-08, 2026-04-12, 2026-04-17.

This script:
  1. Reads each target file.
  2. Parses the ### Journal section.
  3. Keeps rows matching `- **HH:MM** <emoji> <text>` where <text> does NOT
     start with `- **` (the corruption signature — text was another entry line).
  4. Dedupes by (time, emoji, text).
  5. Sorts by time.
  6. Rewrites only the ### Journal section. Frontmatter, ### Milestone,
     ### Milestones, #### Three successful things etc. are untouched.

Default mode is --preview: no writes, shows stats + samples. Pass --apply to
actually rewrite the files. Each rewrite is preceded by a .bak copy next to
the original.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

ENTRY_RE = re.compile(r"^- \*\*(\d{2}:\d{2})\*\*\s+(\S+)\s+(.*)$")
CORRUPT_PREFIX = "- **"


def split_journal_section(content: str) -> tuple[str, list[str], str]:
    """Split into (head, journal_lines, tail).

    head = everything up to and including the '### Journal' header + the blank
    line that typically follows it.
    journal_lines = raw body lines between the header and the next '### '
    or '## ' heading.
    tail = that heading + everything after.
    """
    lines = content.split("\n")
    header_idx = None
    end_idx = None
    for i, line in enumerate(lines):
        if line.strip() == "### Journal":
            header_idx = i
            for j in range(i + 1, len(lines)):
                if lines[j].startswith("### ") or lines[j].startswith("## "):
                    end_idx = j
                    break
            if end_idx is None:
                end_idx = len(lines)
            break
    if header_idx is None:
        return content, [], ""
    # Keep one blank line after the header in the head block if present
    body_start = header_idx + 1
    if body_start < len(lines) and lines[body_start].strip() == "":
        body_start += 1
    head = "\n".join(lines[:body_start])
    body = lines[body_start:end_idx]
    tail = "\n".join(lines[end_idx:])
    return head, body, tail


def repair_journal_body(body: list[str]) -> tuple[list[str], dict]:
    """Return (clean_lines, stats).

    Timestamped entries (`- **HH:MM** emoji text`) are deduped by
    (time, emoji, text), dropping the corrupt rows where text starts with
    '- **'. Non-entry lines (manual freeform notes, non-timestamped dashes)
    are kept and deduped by exact content — we can't tell apart duplication
    from intent, so we err on the side of preservation.
    """
    seen_entries: set[tuple[str, str, str]] = set()
    kept_entries: list[tuple[str, str, str]] = []  # (time, emoji, text)
    seen_nonentry: set[str] = set()
    kept_nonentry: list[str] = []
    dropped_corrupt = []
    dropped_entry_dupe = 0
    dropped_nonentry_dupe = 0

    for raw in body:
        line = raw.rstrip()
        if not line.strip():
            continue
        m = ENTRY_RE.match(line.strip())
        if m:
            time_, emoji, text = m.group(1), m.group(2), m.group(3).strip()
            if text.startswith(CORRUPT_PREFIX):
                dropped_corrupt.append(line.strip()[:120])
                continue
            key = (time_, emoji, text)
            if key in seen_entries:
                dropped_entry_dupe += 1
                continue
            seen_entries.add(key)
            kept_entries.append(key)
        else:
            # Non-entry line — manual note, freeform text, etc. Keep it, dedupe
            # by exact stripped content so we don't amplify whatever put it
            # here repeatedly but still preserve intentional prose.
            stripped = line.strip()
            if stripped in seen_nonentry:
                dropped_nonentry_dupe += 1
                continue
            seen_nonentry.add(stripped)
            kept_nonentry.append(stripped)

    kept_entries.sort(key=lambda e: e[0])
    clean = [f"- **{t}** {em} {tx}" for t, em, tx in kept_entries]
    # Append non-entry lines after the sorted timestamped block.
    clean.extend(kept_nonentry)
    stats = {
        "kept_entries": len(kept_entries),
        "kept_nonentry": len(kept_nonentry),
        "dropped_corrupt": len(dropped_corrupt),
        "dropped_entry_dupe": dropped_entry_dupe,
        "dropped_nonentry_dupe": dropped_nonentry_dupe,
        "sample_corrupt": dropped_corrupt[:3],
        "sample_nonentry_kept": kept_nonentry[:3],
    }
    return clean, stats


def process_file(path: Path, apply: bool) -> dict:
    content = path.read_text(encoding="utf-8")
    original_lines = len(content.split("\n"))
    original_bytes = len(content.encode("utf-8"))

    head, body, tail = split_journal_section(content)
    if not body:
        return {"path": str(path), "skipped": "no ### Journal section"}

    clean, stats = repair_journal_body(body)

    new_body = "\n".join(clean) + ("\n" if clean else "")
    if tail:
        new_content = head + "\n" + new_body + "\n" + tail
    else:
        new_content = head + "\n" + new_body
    # Normalize trailing whitespace but preserve one trailing newline
    new_content = new_content.rstrip() + "\n"

    new_lines = len(new_content.split("\n"))
    new_bytes = len(new_content.encode("utf-8"))

    result = {
        "path": str(path),
        "before": {"lines": original_lines, "bytes": original_bytes},
        "after": {"lines": new_lines, "bytes": new_bytes},
        **stats,
    }

    if apply:
        bak = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, bak)
        path.write_text(new_content, encoding="utf-8")
        result["backup"] = str(bak)
        result["applied"] = True
    else:
        result["applied"] = False

    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="+", help="Journal file paths to repair")
    ap.add_argument(
        "--apply", action="store_true", help="Actually rewrite files (default is preview-only)"
    )
    args = ap.parse_args()

    any_changes = False
    for p in args.paths:
        path = Path(p)
        if not path.exists():
            print(f"[skip] missing: {p}")
            continue
        result = process_file(path, apply=args.apply)
        if "skipped" in result:
            print(f"[skip] {path}: {result['skipped']}")
            continue

        before = result["before"]
        after = result["after"]
        print(f"\n-- {path.name} --")
        print(f"  lines: {before['lines']:>8,}  ->  {after['lines']:>8,}")
        print(f"  bytes: {before['bytes']:>8,}  ->  {after['bytes']:>8,}")
        print(f"  kept timestamped entries: {result['kept_entries']}")
        print(f"  kept non-entry lines:     {result['kept_nonentry']}")
        print(f"  dropped corrupt:          {result['dropped_corrupt']}")
        print(f"  dropped entry duplicates: {result['dropped_entry_dupe']}")
        print(f"  dropped nonentry dupes:   {result['dropped_nonentry_dupe']}")
        if result["sample_corrupt"]:
            print("  sample corrupt lines dropped:")
            for s in result["sample_corrupt"]:
                print(f"    {s}")
        if result.get("sample_nonentry_kept"):
            print("  sample non-entry lines kept:")
            for s in result["sample_nonentry_kept"]:
                print(f"    {s[:120]}")
        if result["applied"]:
            print(f"  OK rewrote (backup: {result['backup']})")
            any_changes = True
        else:
            print("  (preview only — pass --apply to rewrite)")

    if not args.apply:
        print("\nRun with --apply to rewrite. Each file gets a .bak next to it first.")
    elif any_changes:
        print("\nDone. Backups (.bak) are next to each original.")


if __name__ == "__main__":
    sys.exit(main() or 0)
