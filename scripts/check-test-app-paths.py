#!/usr/bin/env python
"""Flag test files that hardcode an app's track-tree location and no longer resolve.

App ids are independent of folder location (CLAUDE.md § Architecture): the
loaders scan the whole `apps/` tree, so an app can be promoted `labs -> standard`
or re-grouped `engineering -> others` without touching a single app id. Tests
that hardcode `apps/<track>/<group>/<id>` silently break on those moves, and
because the break is at *import* time it surfaces as a pytest **collection
error** — which reds the whole `test-collection` CI gate, not just one file.

That has now happened four times (explore, daily-brief, publish, devices). The
fix each time is the same: resolve through `tests/helpers.py::app_path`, which
uses the depth-agnostic scanner in `emptyos/sdk/app_layout.py`.

This checker is deliberately narrow. It does NOT flag every hardcoded path —
~135 of them exist and are harmless while the app sits still. It looks only at
paths that no longer point at anything on disk, and splits them by confidence:

  moved   — the app id exists somewhere else under apps/. Proof of a move, and
            the only class that gates. The message names the destination.
  unknown — the id is nowhere. Indistinguishable from a test fixture id
            (`apps/public/core/foo`), so it is reported as a note and never
            fails. Gating on it would break every time someone writes a
            fixture, and a gate that cries wolf gets disabled
            (`.claude/rules/audits.md`).

Exit code = number of *moved* paths (see `scripts/preflight.py`).

Usage:
    python scripts/check-test-app-paths.py
    python scripts/check-test-app-paths.py --json
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# `python scripts/x.py` puts scripts/ on sys.path, but tests load this module by
# file path — where it is not. Anchor the sibling import either way.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from scanner_lib import emit_json  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

# `apps/public/standard/worklog` — slash form. The lookbehind rejects
# `data/apps/...` (runtime state, not the track tree) and dotted module paths
# like `apps.personal.jobs`.
_SLASH = re.compile(r"(?<![\w./])apps/(public|extension)/([\w.-]+)/([\w.-]+)")
_SLASH_PERSONAL = re.compile(r"(?<![\w./])apps/personal/([\w.-]+)")

# `"apps" / "public" / "standard" / "worklog"` — pathlib segment form.
_SEGS = re.compile(r'"apps"\s*/\s*"(public|extension)"\s*/\s*"([\w.-]+)"\s*/\s*"([\w.-]+)"')
_SEGS_PERSONAL = re.compile(r'"apps"\s*/\s*"personal"\s*/\s*"([\w.-]+)"')

# Fixture ids that are *meant* not to exist. Mostly cosmetic — it keeps them out
# of the advisory note — with one case where listing is load-bearing: a fixture
# id that COLLIDES with a real app id somewhere else in the tree is classified
# `moved`, and `moved` gates. So "unlisted fixture ids never gate" holds only
# while the id is unique. `demo` proved otherwise: a tmp_path fixture in
# test_unit_check_field_authors.py builds `apps/public/standard/demo`, a real
# `demo` lives under apps/personal/asset-register/, and the checker read the
# pair as proof of a move — reddening a hard gate over a directory that is
# created by the test itself and never read from the repo.
SYNTHETIC_IDS = {
    "foo",           # test_sys_store.py, test_unit_codex_hooks.py — install fixtures
    "bar",           # test_sys_pattern_harvester.py — fixture id
    "plain",         # test_unit_check_settings_panel_drift.py — schema-less app fixture
    "held",          # test_unit_release_filter.py — held-path fixture
    "soft-client",   # test_unit_app_builder_inspect.py — scaffolded into tmp_path
    "cable-network", # test_unit_kb_butler.py — fabricated symbol reference string
    "demo",          # test_unit_check_field_authors.py — app tree built under tmp_path
}

# A file carrying this marker is skipped wholesale. The escape hatch exists for
# files whose *subject* is stale paths — this checker's own test, which must
# spell out the broken literals verbatim to prove the scanner catches them.
IGNORE_MARKER = "check-test-app-paths: ignore-file"


def _candidates(text: str) -> set[str]:
    """Every repo-relative app dir this text claims to reference."""
    out: set[str] = set()
    for m in _SLASH.finditer(text):
        out.add(f"apps/{m.group(1)}/{m.group(2)}/{m.group(3)}")
    for m in _SEGS.finditer(text):
        out.add(f"apps/{m.group(1)}/{m.group(2)}/{m.group(3)}")
    for m in _SLASH_PERSONAL.finditer(text):
        out.add(f"apps/personal/{m.group(1)}")
    for m in _SEGS_PERSONAL.finditer(text):
        out.add(f"apps/personal/{m.group(1)}")
    return out


def scan(repo: Path = REPO) -> list[dict]:
    """Return [{file, path, app_id, found_at}] for each unresolved app path.

    `found_at` is the app's real location when the id still exists elsewhere in
    the tree — i.e. the app moved. An empty `found_at` means the id is nowhere,
    which callers must treat as advisory (see the module docstring).
    Pure: no I/O beyond reading the test files and stat-ing the tree.
    """
    findings: list[dict] = []
    tests_dir = repo / "tests"
    if not tests_dir.is_dir():
        return findings

    for f in sorted(tests_dir.rglob("*.py")):
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if IGNORE_MARKER in text:
            continue
        for rel in sorted(_candidates(text)):
            app_id = rel.rsplit("/", 1)[-1]
            if app_id in SYNTHETIC_IDS or (repo / rel).exists():
                continue
            # The id may still exist somewhere else — that is a move, not a typo.
            found_at = ""
            for cand in repo.glob(f"apps/*/*/{app_id}"):
                if cand.is_dir():
                    found_at = cand.relative_to(repo).as_posix()
                    break
            if not found_at:
                for cand in repo.glob(f"apps/personal/{app_id}"):
                    if cand.is_dir():
                        found_at = cand.relative_to(repo).as_posix()
                        break
            findings.append({
                "file": f.relative_to(repo).as_posix(),
                "path": rel,
                "app_id": app_id,
                "found_at": found_at,
            })
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="machine-readable envelope")
    args = ap.parse_args()

    findings = scan()
    moved = [f for f in findings if f["found_at"]]
    unknown = [f for f in findings if not f["found_at"]]

    if args.json:
        return emit_json(not moved, "stale_app_path",
                         f"{len(moved)} test(s) reference a moved app",
                         {"moved": moved, "unknown": unknown})

    if not moved:
        print("check-test-app-paths: OK — no test references a moved app")
    else:
        print(f"check-test-app-paths: {len(moved)} test(s) reference a moved app\n")
        for f in moved:
            print(f"  {f['file']}")
            print(f"    references:   {f['path']}")
            print(f"    app moved to: {f['found_at']}")
            print(f"    fix: from helpers import app_path  ->  app_path({f['app_id']!r})")
            print()
        print("Hardcoded track paths break on every promote/regroup and surface as")
        print("pytest COLLECTION errors, reddening the whole CI gate. Use app_path().")

    if unknown:
        # Never gates: a nonexistent id is most often a deliberate fixture.
        print(f"\nnote: {len(unknown)} path(s) name an app id that exists nowhere "
              f"(likely test fixtures): "
              + ", ".join(sorted({f["app_id"] for f in unknown})))

    return len(moved)


if __name__ == "__main__":
    sys.exit(main())
