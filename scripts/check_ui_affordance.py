#!/usr/bin/env python3
"""Interaction-affordance audit — rendered-page overflow + tab-semantics.

Drives Playwright against the local daemon, loads every app's root page, and
runs the shared walk from emptyos/web/static/eos-ui-affordance.js (the same
code the `?debug=affordance` overlay uses). Two detectors:

  1. overflow-clip   — content taller than its box, clipped by overflow:hidden
                       with no scroll affordance (unreachable). `fail` only for
                       the confident "a declared scroll region inside is clamped
                       off" signature (broken height chain); otherwise `warn`,
                       because a collapsed accordion legitimately clips.
  2. buttons-as-tabs — a horizontal row of buttons with exactly one active
                       (mutually exclusive => tab-like) but no role="tablist"/
                       "tab". Always `warn` — an a11y-semantic improvement, never
                       a gate ("ambiguous signal must never gate", audits.md).

Exit code counts ONLY `fail` findings, so the noisy detector ships advisory and
the confident one can gate. Opt-out: `data-affordance-ignore` on any element
exempts its subtree (inline marker beats a central allowlist).

This closes the deterministic half of the perceptual-audit gap that let the CAD
workspace issues ship. The judgment half — "does this UI have the affordances it
needs", visual hierarchy, panel separation — stays in the eos-ui-walk skill and
cannot be automated; see .claude/rules/audits.md.

Usage:
    python scripts/check_ui_affordance.py                       # all apps
    python scripts/check_ui_affordance.py --app hub --app task  # subset (FP check)
    python scripts/check_ui_affordance.py --url /cad/?host=layouts&layout=part-edit
    python scripts/check_ui_affordance.py --json

Exit code:
    0  → no confident FAILs
    N  → N fail-severity finding groups (capped at 99)
    2  → daemon unreachable / setup error

Like check_readability.py / check-clickable.py this needs a RUNNING daemon, so
it is deliberately NOT in the static scripts/preflight.py registry (that runner
must stay daemon-free). Its CI coverage is tests/test_sys_ui_affordance.py,
which pins the walk against fixture pages. Invoke it manually, from the
eos-design-system-audit skill, or at release time.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _eos_browser import BASE, load_auth_token, eos_storage_state
from audit_driver import (
    audit_page, audit_sources, build_report, exit_code, fetch_apps, select_apps, write_report,
)

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("ERROR: playwright not installed. Run: pip install playwright && playwright install chromium")
    sys.exit(2)

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "data" / "affordance"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--app", action="append", default=[], help="only these app ids (repeatable)")
    ap.add_argument("--url", action="append", default=[],
                    help="audit an explicit path instead of app roots (repeatable)")
    ap.add_argument("--json", action="store_true", help="print the summary JSON to stdout")
    ap.add_argument("--max-apps", type=int, default=0, help="cap the number of apps (quick sample)")
    args = ap.parse_args()

    token = load_auth_token()

    # Targets: explicit --url paths, else every app root.
    if args.url:
        targets = [{"id": u, "path": u} for u in args.url]
    else:
        apps = fetch_apps(BASE, token)
        if apps is None:
            return 2
        apps = select_apps(apps, args.app, args.max_apps)
        if apps is None:
            return 2
        targets = []
        for a in apps:
            p = a["web_prefix"]
            if not p.endswith("/"):
                p += "/"
            targets.append({"id": a["id"], "path": p})

    sources = audit_sources("eos-ui-affordance.js")   # [harness, audit] — order matters
    all_findings: list[dict] = []
    errors: list[dict] = []
    t0 = time.time()

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context(
            viewport={"width": 1440, "height": 900},
            storage_state=eos_storage_state(token),
        )
        for i, t in enumerate(targets, 1):
            page = None
            try:
                page = ctx.new_page()
                result = audit_page(page, BASE + t["path"], sources,
                                    "() => window.__eosAffordance.audit()", settle_ms=600)
                fails = warns = 0
                for f in result["findings"]:
                    f["app"] = t["id"]
                    all_findings.append(f)
                    if f["severity"] == "fail":
                        fails += 1
                    else:
                        warns += 1
                mark = "❌" if fails else ("·" if warns else "✓")
                print(f"  [{i:>3}/{len(targets)}] {t['id']:32s} {mark} {fails} fail / {warns} warn "
                      f"({result['stats']['scanned']} scanned)", flush=True)
            except Exception as e:
                errors.append({"app": t["id"], "error": f"{type(e).__name__}: {str(e)[:120]}"})
                print(f"  [{i:>3}/{len(targets)}] {t['id']:32s} EXC: {type(e).__name__}", flush=True)
            finally:
                if page is not None:
                    try:
                        page.close()
                    except Exception:
                        pass
        ctx.close()
        b.close()

    fails = [f for f in all_findings if f["severity"] == "fail"]
    warns = [f for f in all_findings if f["severity"] == "warn"]

    report = build_report(BASE, all_findings, errors, t0, targets=len(targets))
    out_path = write_report(OUT_DIR, report)

    if args.json:
        print(json.dumps(report["summary"] | {"report": str(out_path)}, ensure_ascii=False))
        return exit_code(fails)

    print(f"\n{len(targets)} pages in {report['duration_s']}s — "
          f"{len(fails)} FAIL / {len(warns)} warn → {out_path.relative_to(REPO)}")

    for label, group in (("FAIL (confident)", fails), ("warn (advisory)", warns)):
        if not group:
            continue
        print(f"\n{label}:")
        for f in group[:15]:
            print(f"  {'✗' if f['severity'] == 'fail' else '·'} {f['app']:22s} {f['type']:16s} "
                  f"×{f['count']}  {f['sel']}\n      {f.get('detail', '')}")

    if fails or warns:
        print("\nFix the container (bound the height so the scroll region can actually scroll; add "
              "role=\"tablist\"/\"tab\" or role=\"radiogroup\"/\"radio\" to a one-of-N button row), "
              "or mark a deliberate case with data-affordance-ignore.")

    return exit_code(fails)


if __name__ == "__main__":
    sys.exit(main())
