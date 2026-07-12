"""check-button-tips.py — find <button> tags without a hover tip.

Convention (2026-06-11): every button should carry a native ``title="..."``
hover tip explaining what it does (shared components in eos-components.js
set them; app pages set their own). This scan lists buttons that have
neither ``title=`` nor ``data-tip`` so new pages don't regress.

Static file scan — no daemon needed. Scans:
  - apps/**/pages/*.html, apps/**/pages/*.js
  - emptyos/web/static/*.js
  - emptyos/web/auto_ui.py

Buttons whose attributes are assembled dynamically (the tag doesn't close
on the same line, or it concatenates an ``attrs`` variable) can't be judged
statically and are counted separately as "dynamic", never as findings.

Usage:
  python scripts/check-button-tips.py             # report, always exit 0
  python scripts/check-button-tips.py --strict    # exit 1 when findings exist
  python scripts/check-button-tips.py --json      # agent-cli envelope
  python scripts/check-button-tips.py --path apps/public/core/task
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# A button tag that opens and closes on one line.
BUTTON_RE = re.compile(r"<button\b[^>]*>", re.IGNORECASE)
# Tags we can't judge statically: attribute block spans lines or is concatenated in.
DYNAMIC_MARKERS = ("' + attrs", '" + attrs', "${attrs", "+ attrs +")

SCAN_GLOBS = (
    ("apps", "**/pages/*.html"),
    ("apps", "**/pages/*.js"),
    ("emptyos/web/static", "*.js"),
)
EXTRA_FILES = ("emptyos/web/auto_ui.py",)
SKIP_DIRS = ("node_modules", "_retired", "_archive", "dist")


def has_tip(tag: str) -> bool:
    low = tag.lower()
    return "title=" in low or "data-tip" in low


def scan_file(path: Path) -> tuple[list[tuple[int, str]], int]:
    """Return ([(lineno, tag), ...] findings, dynamic_count)."""
    findings: list[tuple[int, str]] = []
    dynamic = 0
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return findings, dynamic
    for lineno, line in enumerate(text.splitlines(), 1):
        if "<button" not in line.lower():
            continue
        tags = BUTTON_RE.findall(line)
        if not tags and "<button" in line.lower():
            dynamic += 1  # tag opens here but closes on a later line
            continue
        for tag in tags:
            if any(m in tag for m in DYNAMIC_MARKERS):
                dynamic += 1
                continue
            if not has_tip(tag):
                findings.append((lineno, tag.strip()[:120]))
    return findings, dynamic


def main() -> int:
    ap = argparse.ArgumentParser(description="Find <button> tags without a title= tip")
    ap.add_argument("--path", default="", help="limit scan to a sub-path (relative to repo root)")
    ap.add_argument("--strict", action="store_true", help="exit 1 when findings exist")
    ap.add_argument("--json", action="store_true", dest="as_json", help="machine-readable envelope")
    args = ap.parse_args()

    files: list[Path] = []
    for base, glob in SCAN_GLOBS:
        files.extend((ROOT / base).glob(glob))
    files.extend(ROOT / f for f in EXTRA_FILES if (ROOT / f).is_file())
    if args.path:
        limit = (ROOT / args.path).resolve()
        files = [f for f in files if str(f.resolve()).startswith(str(limit))]
    files = sorted(
        f for f in set(files)
        if not any(part in SKIP_DIRS for part in f.parts)
    )

    per_file: dict[str, list[dict]] = {}
    total = 0
    dynamic_total = 0
    for f in files:
        findings, dynamic = scan_file(f)
        dynamic_total += dynamic
        if findings:
            rel = str(f.relative_to(ROOT)).replace("\\", "/")
            per_file[rel] = [{"line": ln, "tag": tag} for ln, tag in findings]
            total += len(findings)

    clean = total == 0
    # Agent-cli envelope rule: `ok` mirrors the exit code. Without --strict the
    # command is report-only (exit 0 regardless), so ok stays true and the
    # finding count lives in message + data.
    ok = clean or not args.strict
    if args.as_json:
        print(json.dumps({
            "ok": ok,
            "code": "ok" if clean else "missing_tips",
            "message": f"{total} buttons without title= across {len(per_file)} files",
            "data": {"files": per_file, "total": total, "dynamic_skipped": dynamic_total},
        }))
    else:
        for rel, rows in per_file.items():
            print(f"{rel}  ({len(rows)})")
            for r in rows:
                print(f"  {r['line']:>5}: {r['tag']}")
        print(f"\n{total} buttons without title= across {len(per_file)} files "
              f"({dynamic_total} dynamic tags skipped)")
        if not clean:
            print("Add title=\"...\" describing what the button does (see eos-components.js for examples).")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
