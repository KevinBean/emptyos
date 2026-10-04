#!/usr/bin/env python3
"""Advisory scan of `[storage]` manifest declarations (docs/CLOUD-ARCHITECTURE.md).

Apps MAY declare which storage domains they touch via a `[storage]` table in
`manifest.toml` (values are domain names: vault / data / commons / live /
index; `control` is never app-addressable). Absence asserts nothing — this
scanner therefore reports the undeclared population as ONE summary count and
details ONLY invalid declarations (unknown domain value, `control` used,
non-string values), per .claude/rules/audits.md false-positive discipline.

Exit code = number of apps with an INVALID declaration (0 = healthy tree,
even with zero adopters). Registered gate=False in scripts/preflight.py, so
findings warn rather than block.

Pure file I/O — does NOT import emptyos.kernel or the emptyos.sdk package
(`emptyos/sdk/__init__.py` pulls heavy deps; app_layout is loaded by path via
`check_common.load_by_path`). Safe while the daemon is up.

Usage::

    python scripts/check_storage_decl.py            # human summary
    python scripts/check_storage_decl.py --json     # agent-cli envelope
    python scripts/check_storage_decl.py --list     # also list declared apps
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

from check_common import load_by_path

ROOT = Path(__file__).resolve().parent.parent
APPS = ROOT / "apps"

# Domain vocabulary — keep in sync with docs/CLOUD-ARCHITECTURE.md.
VALID_DOMAINS = {"vault", "data", "commons", "live", "index"}
NEVER_APP_ADDRESSABLE = {"control"}


def scan() -> dict:
    al = load_by_path("app_layout_storage", "emptyos/sdk/app_layout.py")
    declared: list[dict] = []
    invalid: list[dict] = []
    undeclared = 0
    total = 0
    for app_id, app_dir in al.iter_app_dirs(APPS, include_personal=True):
        total += 1
        try:
            with open(app_dir / "manifest.toml", "rb") as f:
                manifest = tomllib.load(f)
        except Exception as e:  # unparseable manifest is another check's job
            invalid.append({"app": app_id, "problems": [f"manifest unreadable: {e}"]})
            continue
        storage = manifest.get("storage")
        if storage is None:
            undeclared += 1
            continue
        problems: list[str] = []
        if not isinstance(storage, dict) or not storage:
            problems.append("[storage] must be a non-empty table of key = \"domain\"")
            domains: dict = {}
        else:
            domains = storage
        for key, val in domains.items():
            if not isinstance(val, str):
                problems.append(f"{key}: value must be a string domain name, got {type(val).__name__}")
            elif val in NEVER_APP_ADDRESSABLE:
                problems.append(f"{key} = \"{val}\": `control` is never app-addressable")
            elif val not in VALID_DOMAINS:
                problems.append(
                    f"{key} = \"{val}\": unknown domain (valid: {', '.join(sorted(VALID_DOMAINS))})"
                )
        rel = str(app_dir.relative_to(APPS)).replace("\\", "/")
        if problems:
            invalid.append({"app": app_id, "dir": rel, "problems": problems})
        else:
            declared.append({"app": app_id, "dir": rel, "storage": domains})
    return {
        "total": total,
        "declared": declared,
        "undeclared": undeclared,
        "invalid": invalid,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="agent-cli JSON envelope")
    ap.add_argument("--list", action="store_true", help="also list valid declarations")
    args = ap.parse_args()

    r = scan()
    n_invalid = len(r["invalid"])
    msg = (
        f"{len(r['declared'])}/{r['total']} apps declare [storage]; "
        f"{r['undeclared']} undeclared (fine — adopt on touch); "
        f"{n_invalid} invalid"
    )

    if args.json:
        print(json.dumps({
            "ok": n_invalid == 0,
            "code": "ok" if n_invalid == 0 else "invalid_decl",
            "message": msg,
            "data": {**r, "declared": r["declared"] if args.list else len(r["declared"])},
        }))
        return n_invalid

    print(f"[storage] declarations: {msg}")
    if args.list:
        for d in r["declared"]:
            doms = ", ".join(f"{k}={v}" for k, v in d["storage"].items())
            print(f"  ok      {d['app']:<24} {doms}")
    for bad in r["invalid"]:
        print(f"  INVALID {bad['app']} ({bad.get('dir', '?')})")
        for p in bad["problems"]:
            print(f"          - {p}")
    return n_invalid


if __name__ == "__main__":
    sys.exit(main())
