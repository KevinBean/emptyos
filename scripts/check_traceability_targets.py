#!/usr/bin/env python3
"""Resolve every traceability row's cited test and code symbol against the tree.

`check_engineering_assurance.py` enforces that each stated requirement id has a
row in the package's traceability table. It never opens the row. So a row is
satisfied by *existing*, and the thing it points at — the test that proves the
requirement, the function that implements it — is free to be renamed, split or
deleted with the claim left standing.

That is not hypothetical. After the workbench rewrite the package reported
`111/121 ids traced, 20 UX, 0 error(s)` while **17 of its 20 UX rows cited
tests that no longer existed** and six cited page symbols that no longer
existed. The requirement most visibly lost — every result table is named —
regressed in the same changeset that deleted its test, and the deleted test's
own docstring had predicted it: "a seventh table added without one is exactly
the regression".

This is the missing receipt. CLAUDE.md's rule for the trust loop is that every
obligation names the stage that owns it, the artefact that proves it, *and what
runs that artefact*. Assurance owned the first two. This runs the third.

Advisory, not a gate: a row may legitimately point at a helper this resolver
cannot see (a fixture, a page built at runtime), and per `.claude/rules/audits.md`
an ambiguous signal must never gate. Opt a row out at the call site with a
trailing `<!-- traceability: external -->` on the row rather than a central
allowlist.

    python scripts/check_traceability_targets.py [--app prelim-sizing] [--json]

Exit code is non-zero when any cited target fails to resolve: the count in
human mode, 1 under `--json` (the agent-cli envelope owns the signal there).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from scanner_lib import emit_json

ROOT = Path(__file__).resolve().parent.parent

# A row cites tests as `::test_name` and implementation as `file::symbol`.
ROW_RE = re.compile(r"^\|\s*`([A-Z]+-[A-Z]+-\d+)`\s*\|(.+?)\|(.+?)\|\s*$", re.M)
TEST_RE = re.compile(r"::\s*(test_[A-Za-z0-9_]+)")
SYMBOL_RE = re.compile(r"([\w./-]+)::(\w+)")
# The FILE half of a cited test node id. Checked separately from the test name
# because the two fail independently: cable-bonding shipped four rows citing
# `test_sys_cable-bonding.py` (hyphen — not an importable module name) whose
# test *functions* all existed under the real underscore file, so a resolver
# keyed on the function name alone reported OK on a path that cannot be run.
# That is the second instance of this checker passing over a target it could
# not see (docs/TRUST-LOOP.md records the first).
TESTFILE_RE = re.compile(r"([\w./-]+\.py)\s*::")
OPT_OUT = "traceability: external"


def _defined_tests() -> set[str]:
    """Every test function name defined anywhere, at any indentation.

    Indentation matters: this package's UX suite used to be class-based, so a
    resolver keyed on `^def test_` would have reported the whole suite missing
    and been ignored as noise on its first run.
    """
    names: set[str] = set()
    for base in ("tests", "engines", "apps"):
        root = ROOT / base
        if not root.is_dir():
            continue
        for path in root.rglob("test_*.py"):
            names.update(re.findall(r"def\s+(test_[A-Za-z0-9_]+)",
                                    path.read_text(encoding="utf-8", errors="replace")))
    return names


def _defined_test_files() -> set[str]:
    """Basenames of every test module in the tree, for resolving a cited path.

    Basename rather than full path because rows legitimately cite either form
    (`tests/test_sys_x.py::...` or a bare `test_sys_x.py::...`); requiring the
    full path would fire on the bare-name rows, which are correct.
    """
    names: set[str] = set()
    for base in ("tests", "engines", "apps"):
        root = ROOT / base
        if not root.is_dir():
            continue
        names.update(p.name for p in root.rglob("test_*.py"))
    return names


def _page_text(app_dir: Path) -> str:
    pages = app_dir / "pages"
    if not pages.is_dir():
        return ""
    return "".join(p.read_text(encoding="utf-8", errors="replace")
                   for p in pages.rglob("*") if p.is_file())


def audit(app_dir: Path) -> list[dict]:
    doc = app_dir / "IMPLEMENTATION.md"
    if not doc.is_file():
        return []
    text = doc.read_text(encoding="utf-8", errors="replace")
    defined, page = _defined_tests(), _page_text(app_dir)
    defined_files = _defined_test_files()
    findings: list[dict] = []

    for rid, impl, tests in ROW_RE.findall(text):
        if OPT_OUT in impl or OPT_OUT in tests:
            continue
        # Only ever ask "does the cited target exist" — never "was a target
        # cited". Engineering requirements trace through conformance case ids
        # rather than pytest names, so demanding a test of every row fired on
        # 17 healthy VAL-* rows on the first run and would have been dismissed
        # as noise. Whether a row cites enough is the assurance checker's call.
        cited = TEST_RE.findall(tests)
        missing = [t for t in cited if t not in defined]
        if missing:
            findings.append({"id": rid, "kind": "missing-test", "detail": ", ".join(missing)})
        # The cited FILE, independently of the test name. A row naming a module
        # that does not exist cannot be executed even when every function it
        # names is defined under some other file.
        bad_files = [f for f in TESTFILE_RE.findall(tests)
                     if not (ROOT / f).is_file() and Path(f).name not in defined_files]
        if bad_files:
            findings.append({"id": rid, "kind": "missing-test-file",
                             "detail": ", ".join(sorted(set(bad_files)))})
        # Only page symbols are resolvable here; engine symbols are already
        # covered by the assurance checker's own code join.
        for fname, sym in SYMBOL_RE.findall(impl):
            if ("pages/" in fname or fname.endswith((".html", ".js"))) and page:
                if not re.search(rf"\b{re.escape(sym)}\b", page):
                    findings.append({"id": rid, "kind": "missing-symbol",
                                     "detail": f"{fname}::{sym}"})
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--app", default="", help="app id; default = every package carrying an "
                                              "IMPLEMENTATION.md")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    # Keyed on the document rather than manifest [assurance], so a package that
    # writes a traceability table before opting in is still resolved.
    dirs = [p.parent for p in (ROOT / "apps").rglob("*/IMPLEMENTATION.md")
            if not args.app or p.parent.name == args.app]

    results: dict[str, list[dict]] = {}
    for d in sorted(dirs):
        found = audit(d)
        if found:
            results[d.name] = found
    total = sum(len(v) for v in results.values())

    if args.json:
        return emit_json(not total, "unresolved-traceability",
                         f"{total} traceability row(s) point at something that no longer exists",
                         {"apps": results})

    if not total:
        print(f"OK     every traceability row resolves ({len(dirs)} package(s) checked).")
        return 0
    for app, found in results.items():
        print(f"\n{app}  —  {len(found)} unresolved row(s)")
        for f in found:
            print(f"  {f['kind']:<15} {f['id']:<12} {f['detail']}")
    print(f"\n{total} row(s) claim a proof that is not there. Re-point the row at the "
          f"test that covers it now, or restore the test.")
    # Capped: a process exit code is taken mod 256, so returning a raw count
    # would report success on exactly the packages that have rotted worst.
    return min(total, 250)


if __name__ == "__main__":
    sys.exit(main())
