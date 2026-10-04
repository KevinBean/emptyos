"""check-button-tips.py — find <button> tags with no accessible name.

Convention (2026-06-11): every button should carry a native ``title="..."``
hover tip explaining what it does. Held as-is until 2026-09-06, when measuring
it split the signal in two (audit F7):

  * **1971** buttons with a visible label ("Refresh", "Clear filters"). Their
    label already IS their accessible name; ``title`` only adds a redundant
    hover tooltip. Firing here put the check on **87% of apps**, which is how a
    convention nobody adopted came to look like fleet-wide drift.
  * **85** icon-only buttons (``×``, ``⚙``, ``↑``, ``&times;``) with no name at
    all. A screen reader announces "button" and nothing else. That is a real
    defect, and it was buried under 95% noise.

So the *finding* is now the second class only, and ``aria-label`` counts as a
name — it is the correct one for an icon button, and the old rule flagged 37
buttons that were already labelled properly with it. The broad "no title="
number survives as an advisory stat so nothing is lost.

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


#: A button that opens AND closes on one line, so its label is readable here.
BUTTON_FULL_RE = re.compile(r"<button\b([^>]*)>(.*?)</button>", re.IGNORECASE)


def has_tip(tag: str) -> bool:
    """The original, broad rule — kept for the advisory stat."""
    low = tag.lower()
    return "title=" in low or "data-tip" in low


def has_name(tag: str) -> bool:
    """Does the button carry an accessible name? `aria-label` is the right one
    for an icon button; `title` also supplies a name, weakly."""
    low = tag.lower()
    return "title=" in low or "data-tip" in low or "aria-label=" in low


def visible_label(inner: str) -> str:
    """The text a sighted user reads. Nested tags, HTML entities and emoji are
    stripped: a `<span>&times;</span>` button reads as empty, which is the
    point — it has no name unless an attribute supplies one."""
    t = re.sub(r"<[^>]+>", " ", inner)
    t = re.sub(r"&[a-zA-Z#0-9]+;", " ", t)
    return re.sub(r"[^\w]+", " ", t, flags=re.UNICODE).strip()


def scan_file(path: Path) -> tuple[list[tuple[int, str]], int, int]:
    """Return ([(lineno, tag), ...] unnamed ICON buttons, dynamic, no_title_stat)."""
    findings: list[tuple[int, str]] = []
    dynamic = 0
    no_title = 0
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return findings, dynamic, no_title
    for lineno, line in enumerate(text.splitlines(), 1):
        if "<button" not in line.lower():
            continue
        opens = BUTTON_RE.findall(line)
        if not opens:
            dynamic += 1  # tag opens here but closes on a later line
            continue
        for tag in opens:
            if any(m in tag for m in DYNAMIC_MARKERS):
                dynamic += 1
            elif not has_tip(tag):
                no_title += 1
        for attrs, inner in BUTTON_FULL_RE.findall(line):
            tag = "<button" + attrs + ">"
            if any(m in tag for m in DYNAMIC_MARKERS):
                continue
            # A visible label is already the accessible name.
            if visible_label(inner) or has_name(tag):
                continue
            findings.append((lineno, tag.strip()[:120]))
    return findings, dynamic, no_title


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
    no_title_total = 0
    for f in files:
        findings, dynamic, no_title = scan_file(f)
        dynamic_total += dynamic
        no_title_total += no_title
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
            "message": f"{total} icon-only button(s) with no accessible name across {len(per_file)} files",
            "data": {"files": per_file, "total": total, "dynamic_skipped": dynamic_total,
                     "no_title_advisory": no_title_total},
        }))
    else:
        for rel, rows in per_file.items():
            print(f"{rel}  ({len(rows)})")
            for r in rows:
                print(f"  {r['line']:>5}: {r['tag']}")
        if not clean:
            print("Add aria-label=\"...\" naming the action — an icon-only button has no")
            print("accessible name, so a screen reader announces only \"button\".")
        print(f"\nnote: {no_title_total} button(s) carry no title= hover tip. Advisory only —")
        print("      a button with a visible label already has an accessible name.")
        print(f"\n{total} icon-only button(s) with no accessible name across "
              f"{len(per_file)} files ({dynamic_total} dynamic tags skipped)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
