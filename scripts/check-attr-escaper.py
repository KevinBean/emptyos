#!/usr/bin/env python3
"""check-attr-escaper — flag (and optionally fix) wrong-escaper-for-attribute XSS.

EmptyOS frontend builds HTML by string concatenation and exposes two global
escapers (``emptyos/web/static/eos.js``):

  * ``esc(s)``      — HTML *text* escaper (textContent→innerHTML). Encodes
                      ``< > &`` but **NOT** ``"`` or ``'``.
  * ``escAttr(s)``  — HTML *attribute* escaper. Encodes ``& " ' <``.

Using ``esc()`` inside a double-quoted attribute value —
``'<a data-x="'+esc(v)+'">'`` — is an XSS hole: a ``v`` containing ``"`` breaks
out of the attribute (``" onmouseover=…``). The fix is ``escAttr(v)``. This was
flagged in kb (2026-06) and turned out to be systemic (74 files / ~380 sites).

This tool detects attribute-context ``esc()`` and classifies each:

  * **fixable**  — a normal attribute (``data-*``, ``title``, ``class``, ``id``,
                   ``value``, ``href`` fragments…). ``--fix`` rewrites
                   ``esc(`` → ``escAttr(`` in place (only the function name
                   changes — same arity, can't break JS/HTML syntax).
  * **js-context** — an inline event handler (``onclick="fn('+esc(x)+')"``).
                   **escAttr is NOT sufficient** here: the value also sits in a
                   JS string literal, so it needs JS-string handling (e.g.
                   ``escAttr(JSON.stringify(x))``) or — better — moving to
                   ``addEventListener`` + ``data-*``. These are REPORTED, never
                   auto-fixed, because a naive escAttr would look fixed while
                   staying exploitable.

Detection is deliberately precise over exhaustive: it only matches an ``esc(``
whose nearest preceding unclosed quote is an attribute-opening ``"`` (no
intervening ``" < >``), so text-context ``'>'+esc(x)`` never matches. It may
miss single-quoted HTML attributes or values containing ``< >`` (recall loss,
not false positives) — acceptable for a safe codemod.

Usage
-----
    python scripts/check-attr-escaper.py                 # report, exit = findings
    python scripts/check-attr-escaper.py --json
    python scripts/check-attr-escaper.py --fix           # rewrite fixable sites
    python scripts/check-attr-escaper.py --root apps/public/standard/kb

Graduation (.claude/rules/audits.md): static scan, no daemon. Exit code = count
of UNRESOLVED sites (fixable-not-yet-fixed + js-context). After ``--fix`` only
js-context remain, so a non-zero exit forces the manual remediation those need —
wire into release-public.py / a pre-commit hook once the tree is clean.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# attr name = "  <inner JS-literal text, no " < >>  ' (close JS literal)  + esc(
# non-greedy inner so it stops at the first `'+esc(`; backtracks past inner
# concatenations like  class="a '+b+' kind-'+esc(x)  to the real boundary.
ATTR_ESC = re.compile(
    r"""(?P<attr>[A-Za-z_][\w:-]*)\s*=\s*"   # attribute name + ="
        (?P<inner>[^"<>]*?)                   # JS-literal contents (no HTML quote/lt/gt)
        '\s*\+\s*                             # close JS literal, concat
        (?:EOS_UI\.)?esc\(                    # the wrong escaper (bare or EOS_UI.esc)
    """,
    re.VERBOSE,
)

SCAN_SUFFIXES = (".html", ".js")
SKIP_PARTS = ("_retired", "_catalog", "_example", "node_modules")


@dataclass
class Site:
    file: str
    line: int
    attr: str
    js_context: bool   # inline event handler — escAttr alone insufficient
    snippet: str


def scan_text(text: str, relpath: str) -> list[Site]:
    sites: list[Site] = []
    for m in ATTR_ESC.finditer(text):
        attr = m.group("attr")
        line = text.count("\n", 0, m.start()) + 1
        ls = text.rfind("\n", 0, m.start()) + 1
        le = text.find("\n", m.end())
        snippet = text[ls: le if le != -1 else len(text)].strip()[:160]
        sites.append(Site(relpath, line, attr, attr.lower().startswith("on"), snippet))
    return sites


def iter_files(root: Path):
    for f in root.rglob("*"):
        if f.suffix not in SCAN_SUFFIXES:
            continue
        if any(p in SKIP_PARTS or p.startswith("_") for p in f.relative_to(root).parts):
            continue
        yield f


def fix_text(text: str) -> tuple[str, int, int]:
    """Rewrite fixable (non-event-handler) sites esc(→escAttr(. Return
    (new_text, fixed_count, skipped_js_context_count)."""
    fixed = skipped = 0

    def repl(m: re.Match) -> str:
        nonlocal fixed, skipped
        if m.group("attr").lower().startswith("on"):
            skipped += 1
            return m.group(0)            # js-context — leave for manual fix
        fixed += 1
        return m.group(0)[:-4] + "escAttr("   # match ends with 'esc(' (4 chars)

    return ATTR_ESC.sub(repl, text), fixed, skipped


# The js-context safe unit:  \'' + (EOS_UI.|EOS.)?esc( <balanced> ) [.replace(/'/g,…)]? + '\'
# Each onclick arg is INDEPENDENTLY wrapped in \'…\', so transforming each unit in
# isolation is correct even for multi-arg handlers (fn(\''+esc(a)+'\', \''+esc(b)+'\'))
# and trailing literal args (fn(\''+esc(id)+'\', true)). We rewrite ONLY this exact
# shape and skip anything else (reported for hand-fix) — never mangle.
# Optional literal prefix between the \' and the +esc(  — e.g.  \'org/' + esc(id)
# (the prefix is part of the JS-string arg; we fold it into JSON.stringify).
_JS_OPEN = re.compile(r"\\'(?P<prefix>[^'<>\\]*)'\s*\+\s*(?:EOS_UI\.|EOS\.)?esc\(")
# Any single-quote-targeting .replace after esc() is a redundant JS-quote escaper
# (several quoting styles in the wild); JSON.stringify subsumes it, so we drop it.
_JS_REPLACE = re.compile(r"\.replace\(/\\?'/g")


def _scan_balanced(text: str, start: int) -> int:
    """Return index just past the ) that closes the ( at start-1 (depth already 1)."""
    depth, k, n = 1, start, len(text)
    while k < n and depth:
        c = text[k]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        k += 1
    return k if depth == 0 else -1


def transform_js_context(text: str) -> tuple[str, int]:
    """Rewrite safe js-context units to escAttr(JSON.stringify(arg)).

    escAttr alone is insufficient in an event handler (the value also lives in a
    JS string literal); JSON.stringify makes a valid JS literal and escAttr makes
    it attribute-safe. We drop the now-redundant \\'…\\' wrapper + any trailing
    .replace(/'/g,…) JS-quote escaper. Returns (new_text, count).
    """
    out, i, count = [], 0, 0
    while True:
        m = _JS_OPEN.search(text, i)
        if not m:
            out.append(text[i:])
            break
        arg_end = _scan_balanced(text, m.end())          # past esc(...)'s )
        if arg_end < 0:
            out.append(text[i:m.end()]); i = m.end(); continue
        prefix = m.group("prefix")
        arg = text[m.end():arg_end - 1]
        rest = text[arg_end:]
        # optional trailing single-quote-escaping .replace(…)  (any quoting style)
        consumed = 0
        if _JS_REPLACE.match(rest):
            rep_end = _scan_balanced(rest, rest.index("(") + 1)
            if rep_end > 0:
                consumed = rep_end
                rest = rest[rep_end:]
        # must close with  + '\'
        cm = re.match(r"\s*\+\s*'\\'", rest)
        if not cm:
            out.append(text[i:m.end()]); i = m.end(); continue   # not the safe shape
        inner = arg if not prefix else ("'" + prefix + "' + " + arg)
        out.append(text[i:m.start()])
        out.append("' + escAttr(JSON.stringify(" + inner + ")) + '")
        i = arg_end + consumed + cm.end()
        count += 1
    return "".join(out), count


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fix", action="store_true",
                    help="rewrite fixable plain-attribute sites (event handlers untouched)")
    ap.add_argument("--fix-js", action="store_true",
                    help="rewrite SAFE-shape js-context handlers to escAttr(JSON.stringify(x)); "
                         "non-matching shapes are left + reported for hand-fix")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--root", default=None,
                    help="scan this dir instead of apps/ + emptyos/web/static")
    args = ap.parse_args()

    roots = ([Path(args.root)] if args.root
             else [REPO / "apps", REPO / "emptyos" / "web" / "static"])

    all_sites: list[Site] = []
    total_fixed = 0
    for root in roots:
        if not root.exists():
            continue
        for f in iter_files(root):
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            rel = f.relative_to(REPO).as_posix() if REPO in f.parents else f.as_posix()
            if args.fix or args.fix_js:
                fixed = 0
                if args.fix:
                    text, n, _ = fix_text(text); fixed += n
                if args.fix_js:
                    text, n = transform_js_context(text); fixed += n
                if fixed:
                    f.write_text(text, encoding="utf-8")
                    total_fixed += fixed
                all_sites.extend(scan_text(text, rel))   # re-scan post-fix
            else:
                all_sites.extend(scan_text(text, rel))

    fixable = [s for s in all_sites if not s.js_context]
    js_ctx = [s for s in all_sites if s.js_context]

    if args.json:
        print(json.dumps({
            "fixed": total_fixed,
            "remaining_fixable": [vars(s) for s in fixable],
            "js_context": [vars(s) for s in js_ctx],
        }, indent=2))
        return len(fixable) + len(js_ctx)   # gate on the full attribute-esc class

    if args.fix:
        print(f"\ncheck-attr-escaper --fix: rewrote {total_fixed} fixable site(s) "
              f"esc(→escAttr(\n" + "─" * 60)
    else:
        print(f"\ncheck-attr-escaper — {len(all_sites)} attribute-context esc() site(s)\n"
              + "─" * 60)

    if fixable:
        print(f"\n## FIXABLE — wrong escaper, run --fix ({len(fixable)})")
        by_file: dict[str, int] = {}
        for s in fixable:
            by_file[s.file] = by_file.get(s.file, 0) + 1
        for f in sorted(by_file, key=lambda k: -by_file[k]):
            print(f"  {by_file[f]:3}  {f}")

    if js_ctx:
        print(f"\n## JS-CONTEXT — inline event handlers; escAttr alone insufficient. "
              f"Run --fix-js (auto-rewrites safe shapes) or hand-fix ({len(js_ctx)})")
        for s in js_ctx:
            print(f"  {s.file}:{s.line}  {s.attr}=  →  {s.snippet}")

    print("\n" + "─" * 60)
    print(f"  fixable: {len(fixable)}   js-context: {len(js_ctx)}   (both gated)")
    if not fixable and not js_ctx:
        print("  ✓ no attribute-context esc() found.")
    # Exit = every attribute-context esc() (fixable + js-context). Both reach 0 via
    # the --fix / --fix-js codemods, so the gate passes; any regression — a new
    # plain-attribute esc() or a novel js-context shape --fix-js can't rewrite —
    # fails the gate until fixed.
    return len(fixable) + len(js_ctx)


if __name__ == "__main__":
    sys.exit(main())
