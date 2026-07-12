#!/usr/bin/env python3
"""Perceptual readability audit — rendered-page contrast / font-size / opacity.

Drives Playwright against the local daemon, loads every app's root page in
every theme, and runs the shared walk from emptyos/web/static/eos-readability.js
(the same code the `?debug=readability` overlay uses): WCAG 2.1 contrast on
EFFECTIVE colors (ancestor rgba layers alpha-composited), tiny-font and
opacity-faded text checks. Catches the class check-contrast.py can't see —
hardcoded per-page hex that doesn't shift with theme, text sitting on rgba
surfaces, and anything else that renders "technically fine" but unreadable.

Findings carry `data-eos-src` file:line locations (via ?debug=locate) so each
one points at the offending source. Full report → data/readability/report.json;
console shows the worst offenders. Opt-out: `data-readability-ignore` on any
element exempts its subtree (inline marker beats a central allowlist).

Usage:
    python scripts/check_readability.py                       # all apps x all 6 themes
    python scripts/check_readability.py --app task --app hub  # subset of apps
    python scripts/check_readability.py --theme void-dark     # one theme
    python scripts/check_readability.py --json                # machine output
    python scripts/check_readability.py --max-apps 10         # quick sample

Exit code:
    0  → no confident FAILs
    N  → N fail-severity finding groups (capped at 99)
    2  → daemon unreachable / setup error

Only invoked manually, from skills (eos-design-system-audit), or at release
time. Not in preflight — it needs a *running* daemon (same posture as
check-clickable.py). Read-only page loads against :9000 are allowed probing.
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
OUT_DIR = REPO / "data" / "readability"

THEMES = ["eos", "digital-garden", "soft-light", "warm-dark", "void-dark", "nord"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--app", action="append", default=[], help="only these app ids (repeatable)")
    ap.add_argument("--theme", action="append", default=[], choices=THEMES,
                    help="only these themes (repeatable; default all 6)")
    ap.add_argument("--json", action="store_true", help="print the report JSON to stdout")
    ap.add_argument("--max-apps", type=int, default=0, help="cap the number of apps (quick sample)")
    args = ap.parse_args()

    themes = args.theme or THEMES
    token = load_auth_token()

    apps = fetch_apps(BASE, token)
    if apps is None:
        return 2
    apps = select_apps(apps, args.app, args.max_apps)
    if apps is None:
        return 2

    sources = audit_sources("eos-readability.js")   # [harness, audit] — order matters
    all_findings: list[dict] = []
    errors: list[dict] = []
    t0 = time.time()

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        for theme in themes:
            ctx = b.new_context(
                viewport={"width": 1440, "height": 900},
                storage_state=eos_storage_state(token),
            )
            # Theme is client-local: set it before any page script runs.
            ctx.add_init_script(f"try {{ localStorage.setItem('eos-theme', '{theme}'); }} catch (e) {{}}")
            for i, app in enumerate(apps, 1):
                page = None
                url = BASE + app["web_prefix"]
                if not url.endswith("/"):
                    url += "/"
                url += "?debug=locate&overlay=0"  # stamp data-eos-src for file:line findings
                try:
                    page = ctx.new_page()
                    result = audit_page(page, url, sources,
                                        "() => window.__eosReadability.audit()", settle_ms=400)
                    fails = warns = 0
                    for f in result["findings"]:
                        f["app"] = app["id"]
                        f["theme"] = theme
                        if not f.get("detail"):
                            f.pop("detail", None)
                        all_findings.append(f)
                        if f["severity"] == "fail":
                            fails += 1
                        elif f["severity"] == "warn":
                            warns += 1
                    mark = "❌" if fails else ("·" if warns else "✓")
                    print(f"  [{theme:>14}] [{i:>3}/{len(apps)}] {app['id']:25s} "
                          f"{mark} {fails} fail / {warns} warn "
                          f"({result['stats']['scanned']} scanned)", flush=True)
                except Exception as e:
                    errors.append({"app": app["id"], "theme": theme,
                                   "error": f"{type(e).__name__}: {str(e)[:120]}"})
                    print(f"  [{theme:>14}] [{i:>3}/{len(apps)}] {app['id']:25s} EXC: {type(e).__name__}",
                          flush=True)
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

    report = build_report(BASE, all_findings, errors, t0, apps=len(apps), themes=themes)
    out_path = write_report(OUT_DIR, report)

    if args.json:
        print(json.dumps(report["summary"] | {"report": str(out_path)}, ensure_ascii=False))
        return exit_code(fails)

    print(f"\n{len(apps)} apps × {len(themes)} themes in {report['duration_s']}s — "
          f"{len(fails)} FAIL / {len(warns)} warn → {out_path.relative_to(REPO)}")

    if fails:
        print("\nWorst offenders (fail severity):")
        for f in sorted(fails, key=lambda x: x.get("ratio") or 0)[:15]:
            loc = f.get("src") or f.get("sel") or ""
            ratio = f"{f['ratio']}:1" if f.get("ratio") else f["type"]
            print(f"  ✗ {f['app']:20s} [{f['theme']:>13}] {ratio:>8}  "
                  f"{f['fg']} on {f['bg']}  ×{f['count']}  "
                  f"'{(f.get('sample') or '')[:32]}'  @ {loc}")
        print("\nFix the color (use theme tokens — var(--text)/var(--bg-card) — instead of "
              "hardcoded hex), or mark a deliberate case with data-readability-ignore.")

    return exit_code(fails)


if __name__ == "__main__":
    sys.exit(main())
