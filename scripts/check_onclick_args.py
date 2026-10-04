#!/usr/bin/env python3
"""Find JS values interpolated into an inline handler attribute without escaping.

The defect this catches, in its exact shipped form::

    '<button onclick="open(' + JSON.stringify(id) + ')">'

``JSON.stringify`` produces a correct JS *literal*, and that literal contains
real double quotes. Inside ``onclick="..."`` those quotes close the attribute
early, so the handler is a truncated syntax error and never binds. The control
renders perfectly, has no console error, and does nothing when clicked. Four
such dead handlers shipped across three apps (boards group-collapse and
select-cell editing, video-digest "+ transcript", providers "Edit") and none of
them tripped a scanner, a test, or review.

The one correct spelling is ``EOS_UI.jsArg(v)`` — stringify, then escape.

Two shapes are reported:

  ``raw-stringify``   JSON.stringify inside a handler attribute with no escape.
                      Always broken for a string, array or object value.
  ``hand-rolled``     stringify + an inline ``.replace(/"/g,'&quot;')``. Correct
                      but a second spelling; migrate to ``jsArg`` so there is
                      one. Advisory only — this code works.

Deliberately NOT reported: single-quoted interpolation (``'\\''+id+'\\''``). It
is a real hazard for values containing an apostrophe, but apostrophes are rare
in ids and the shape is far too common to separate signal from noise here — a
report nobody can act on trains people to ignore the check (see
.claude/rules/audits.md).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scanner_lib import emit_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# An inline handler attribute opening a double-quoted value, then anything up to
# the concatenation that injects the value. Handlers are onclick/oninput/etc.
_HANDLER = r'on[a-z]+="[^"]*'
RAW = re.compile(_HANDLER + r"'\s*\+\s*[^+]*JSON\.stringify\s*\(")
HAND = re.compile(r"JSON\.stringify\s*\([^;]*?\)\s*\.replace\s*\(\s*/\"/g")
IGNORE = re.compile(r"onclick-args:\s*ignore", re.I)

# `escAttr(JSON.stringify(v))` is correct — it is exactly what jsArg does, and
# it was already the dominant idiom before jsArg existed (~90 call sites). A
# first cut of this scanner did not know that and reported every one of them as
# a dead handler, which is the failure mode audits.md exists to prevent: on a
# healthy tree the signal must be silent. Neutralise the safe spellings before
# looking for a bare one.
SAFE = re.compile(r"(?:EOS_UI\.)?(?:escAttr|jsArg)\s*\(\s*(?:JSON\.stringify\s*\()?")


def scan(text: str) -> list[tuple[int, str, str]]:
    out: list[tuple[int, str, str]] = []
    for i, line in enumerate(text.splitlines(), 1):
        if IGNORE.search(line):
            continue
        # Neutralise every safe spelling — including the hand-rolled one, which
        # works — so RAW only ever fires on a value with no escape at all. Order
        # matters: tested the other way round, a correct hand-rolled escape is
        # reported as a dead handler.
        bare = HAND.sub("SAFE(", SAFE.sub("SAFE(", line))
        if RAW.search(bare):
            out.append((i, "raw-stringify", line.strip()[:150]))
        elif HAND.search(line) and re.search(_HANDLER, line):
            out.append((i, "hand-rolled", line.strip()[:150]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--include-advisory", action="store_true",
                    help="also fail on hand-rolled escapes (default: report only)")
    args = ap.parse_args()

    from scanner_lib import page_files

    findings: list[dict] = []
    files = 0
    for path in page_files(ROOT):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        files += 1
        for line, kind, snippet in scan(text):
            findings.append({
                "file": str(path.relative_to(ROOT)).replace("\\", "/"),
                "line": line, "kind": kind, "snippet": snippet,
            })

    broken = [f for f in findings if f["kind"] == "raw-stringify"]
    advisory = [f for f in findings if f["kind"] == "hand-rolled"]
    ok = not broken and (not advisory or not args.include_advisory)
    msg = (f"{len(broken)} dead handler(s), {len(advisory)} hand-rolled "
           f"escape(s) across {files} page file(s)")

    if args.json:
        emit_json(ok, "ok" if ok else "error", msg,
                  {"broken": broken, "advisory": advisory})
        return 0 if ok else 1

    for f in broken:
        print(f"  DEAD  {f['file']}:{f['line']}\n        {f['snippet']}")
    for f in advisory:
        print(f"  note  {f['file']}:{f['line']} — hand-rolled escape, use EOS_UI.jsArg")
    if not findings:
        print(f"clean — scanned {files} page file(s)")
    else:
        print(f"\n{msg}")
        if broken:
            print("Fix: EOS_UI.jsArg(value) — stringify then escape, in that order.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
