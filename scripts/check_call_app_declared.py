#!/usr/bin/env python3
"""Audit: every literal self.call_app("id", ...) target should resolve.

Two tiers (CLAUDE.md Dev Rule 4 — "Declare every literal call_app target";
.claude/rules/audits.md — graduate one-off scans into a permanent home):

  HARD (exit 1): call_app("X", ...) where X is not a known app id at all —
                 a guaranteed AttributeError/no-op at runtime (typo, renamed
                 app, deleted target). This is the real bug class this gate
                 exists to catch.
  WARN (exit 0): call_app("X", ...) where X exists but is not declared in the
                 caller's manifest [requires] apps / optional_apps — real
                 declaration debt (the topology graph misses the edge) but not
                 a runtime failure. Reported, not fatal.

Pure file I/O (ast + tomllib) — no daemon, safe to run anytime. Dynamic
(non-literal) call_app targets are skipped (can't be checked statically).

Usage:  python scripts/check_call_app_declared.py [--json] [--strict]
        --strict  also exit 1 on WARN-tier undeclared targets.
"""

from __future__ import annotations

import ast
import json
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APPS = REPO / "apps"


def _load_manifests() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for mf in APPS.rglob("manifest.toml"):
        if any(p.startswith("_") for p in mf.parts):
            continue
        try:
            m = tomllib.load(open(mf, "rb"))
        except Exception:
            continue
        aid = (m.get("app") or {}).get("id")
        if not aid:
            continue
        req = m.get("requires") or {}
        out[aid] = {
            "dir": mf.parent,
            "declared": set(req.get("apps") or []) | set(req.get("optional_apps") or []),
        }
    return out


def _literal_call_targets(py: Path):
    try:
        tree = ast.parse(py.read_text(encoding="utf-8"))
    except Exception:
        return
    for n in ast.walk(tree):
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "call_app"
            and n.args
        ):
            a0 = n.args[0]
            if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                yield a0.value, n.lineno


def audit() -> dict:
    manifests = _load_manifests()
    all_ids = set(manifests)
    hard: list[dict] = []
    warn: list[dict] = []
    for aid, info in manifests.items():
        declared = info["declared"] | {aid}
        for py in info["dir"].rglob("*.py"):
            for target, ln in _literal_call_targets(py):
                loc = f"{'/'.join(py.parts[-2:])}:{ln}"
                if target not in all_ids:
                    hard.append({"app": aid, "target": target, "loc": loc})
                elif target not in declared:
                    warn.append({"app": aid, "target": target, "loc": loc})
    return {"hard": hard, "warn": warn}


def main() -> int:
    as_json = "--json" in sys.argv
    strict = "--strict" in sys.argv
    res = audit()
    hard, warn = res["hard"], res["warn"]
    if as_json:
        print(json.dumps({"ok": not hard and (not strict or not warn), **res}))
    else:
        if hard:
            print(f"HARD — call_app to a NON-EXISTENT app id ({len(hard)}):")
            for h in sorted({(x["app"], x["target"], x["loc"]) for x in hard}):
                print(f"  {h[0]} -> '{h[1]}'  ({h[2]})")
        if warn:
            print(f"WARN — undeclared (exists, add to optional_apps) ({len(warn)}):")
            for w in sorted({(x["app"], x["target"], x["loc"]) for x in warn}):
                print(f"  {w[0]} -> '{w[1]}'  ({w[2]})")
        if not hard and not warn:
            print("OK — every literal call_app target resolves and is declared.")
    return 1 if (hard or (strict and warn)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
