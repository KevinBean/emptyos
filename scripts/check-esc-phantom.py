#!/usr/bin/env python3
"""check-esc-phantom — flag calls to undefined escaper helpers on the frontend.

EmptyOS exposes two GLOBAL escapers from ``emptyos/web/static/eos.js``:
``esc(s)`` (HTML text) and ``escAttr(s)`` (HTML attribute). There is **no**
``EOS.esc`` and **no** ``EOS.escapeHtml`` — those names are defined nowhere.
A page that calls one throws ``TypeError: EOS.esc is not a function`` at render
time, which **aborts the render mid-function** and silently blanks whole views
(the lightning detail view — terminal table + rolling-sphere SVG + EGM +
summaries — shipped completely broken this way; commons had 13 sites).

This class is invisible to ``node --check`` and pytest (it's a runtime phantom,
syntactically valid), so it needs a static guard. Detection is a plain literal
scan — ``EOS.esc(`` / ``EOS.escapeHtml(`` are NEVER valid, so the check has zero
false positives by construction (unlike ``esc``-vs-``escAttr`` context, which is
``check-attr-escaper.py``'s job).

The fix is always mechanical: ``EOS.esc(`` → ``esc(`` (or ``escAttr(`` in an
attribute context — run check-attr-escaper afterward).

Usage
-----
    python scripts/check-esc-phantom.py            # report, exit = findings
    python scripts/check-esc-phantom.py --json

Graduation (.claude/rules/audits.md): static scan, no daemon, gate-safe (0 FP).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scanner_lib import emit_json  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

# The phantom escapers. The correct globals are esc() / escAttr().
PHANTOM = re.compile(r"\bEOS\.(esc|escapeHtml)\s*\(")
# A bare reference to the same name (NOT a call) is a defensive guard, e.g.
# ``(window.EOS && EOS.escapeHtml) ? EOS.escapeHtml(s) : <inline fallback>`` —
# the call only runs when the function exists, so it never throws. A line
# carrying such a guard is skipped (this is a safe, deliberate idiom).
GUARD = re.compile(r"\bEOS\.(?:esc|escapeHtml)\b(?!\s*\()")

# Scan the frontend surface only.
GLOBS = ["apps/**/pages/*.html", "apps/**/pages/*.js", "emptyos/web/static/*.js"]


def scan(root: Path) -> list[dict]:
    findings: list[dict] = []
    seen: set[Path] = set()
    for pattern in GLOBS:
        for path in root.glob(pattern):
            if path in seen or "_retired" in path.parts:
                continue
            seen.add(path)
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if GUARD.search(line):
                    continue  # guarded fallback idiom — the call can't throw
                for m in PHANTOM.finditer(line):
                    findings.append({
                        "file": str(path.relative_to(root)).replace("\\", "/"),
                        "line": i,
                        "call": f"EOS.{m.group(1)}(",
                    })
    return findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--root", default=str(REPO))
    args = ap.parse_args()
    root = Path(args.root).resolve()

    findings = scan(root)
    ok = not findings
    msg = (
        "no phantom escaper calls"
        if ok
        else f"{len(findings)} phantom escaper call(s) — use the global esc()/escAttr()"
    )
    if args.json:
        return emit_json(ok, "ok" if ok else "phantom_escaper", msg, {"findings": findings})

    if ok:
        print("check-esc-phantom: clean — no EOS.esc/EOS.escapeHtml calls")
    else:
        print(f"check-esc-phantom: {len(findings)} phantom escaper call(s):")
        for f in findings:
            print(f"  {f['file']}:{f['line']}  {f['call']}  -> use esc( or escAttr(")
    return len(findings)


if __name__ == "__main__":
    sys.exit(main())
