"""Interactive-error UI walk — JS console errors, page errors, and failed
network requests (4xx/5xx) across every app's root page.

Drives Playwright against the local daemon at :9000, opens each app's root in a
*fresh page*, and reports anything that surfaces as a runtime interactive bug:

  - uncaught exceptions (`pageerror`)
  - `console.error` output
  - non-2xx/3xx responses (excluding static assets + web fonts)
  - navigation timeouts / page crashes

This is the frontend sibling of `check-clickable.py` (which catches click
z-order intercepts). It exists because the bug class it catches — a wrong API
path 404 (e.g. an app whose web prefix ≠ its id), a dead feature whose route
was never implemented, a JS error that breaks a button — is invisible to both
the API tests and the click-intercept walk. First run (2026-06-16) caught the
`/api/apps/orgs` 404 (eos.js prefix→id assumption) and meditation's dead
`/api/texts` feature.

CRASH RESILIENCE: a long Playwright run accumulates renderer memory and the
~113th page crashes the whole browser regardless of which app it is (the
false-positive `check-clickable.py` hits at scale). This walker recycles the
browser every `--recycle` pages and respawns on any crash, so one bad page
never aborts the run or gets mis-blamed.

Usage:
    python scripts/check-js-errors.py                 # full walk
    python scripts/check-js-errors.py myapp other      # only these app ids
    python scripts/check-js-errors.py --allow task     # ignore findings on task
    python scripts/check-js-errors.py --recycle 8      # recycle browser every N pages
    python scripts/check-js-errors.py --json           # machine-readable envelope

Exit code:
    0  → no findings (or only on allowlisted apps)
    1  → real findings found
    2  → daemon unreachable / setup error

Only invoked manually, from the `eos-ui-walk` skill, or release-time. Not in
pytest because it expects a *running* daemon with the full app set installed —
and on a large real vault some pages are legitimately slow/heavy (task scans
the whole vault; vault-graph renders every node), which would make a pytest
gate flaky. Triage findings against `.claude/rules/audits.md`: a data-volume
slow-load or OOM on the user's real vault is NOT a code bug.
"""
from __future__ import annotations
import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _eos_browser import BASE, load_auth_token, eos_storage_state

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("ERROR: playwright not installed. Run: pip install playwright && playwright install chromium")
    sys.exit(2)

# Substrings in a request URL that never count as an app bug when they 4xx:
# third-party fonts (offline / rate-limited) and bundled static assets (a
# cache-buster query can transiently 404 mid-deploy).
_IGNORE_URL = ("fonts.googleapis.com", "fonts.gstatic.com", "/static/")


def _app_list(token: str) -> list[tuple[str, str]]:
    hdr = {"Authorization": f"Bearer {token}"} if token else {}
    req = urllib.request.Request(f"{BASE}/api/apps", headers=hdr)
    data = json.load(urllib.request.urlopen(req, timeout=10))
    apps = data.get("apps", data) if isinstance(data, dict) else data
    out = []
    for a in (apps.values() if isinstance(apps, dict) else apps):
        if not isinstance(a, dict):
            continue
        pid = a.get("id") or a.get("app_id")
        pref = a.get("web_prefix") or a.get("prefix") or (f"/{pid}/" if pid else None)
        if pid and pref:
            out.append((pid, pref if pref.endswith("/") else pref + "/"))
    out.sort()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("apps", nargs="*", help="restrict to these app ids")
    ap.add_argument("--allow", action="append", default=[], help="app id to ignore findings on (repeatable)")
    ap.add_argument("--recycle", type=int, default=8, help="recycle the browser every N pages (OOM guard)")
    ap.add_argument("--settle", type=int, default=1200, help="ms to wait after load for late errors")
    ap.add_argument("--json", action="store_true", help="machine-readable envelope on stdout")
    args = ap.parse_args()

    token = load_auth_token()
    try:
        items = _app_list(token)
    except Exception as e:  # noqa: BLE001
        msg = f"daemon unreachable at {BASE}: {e}"
        print(json.dumps({"ok": False, "code": "no_daemon", "message": msg}) if args.json else f"ERROR: {msg}")
        return 2
    if args.apps:
        # Match on app id OR the URL-prefix segment, so `orgs` finds the
        # `company` app (prefix /orgs/) — the same prefix≠id case this walk
        # was built to catch.
        want = set(args.apps)
        items = [it for it in items if it[0] in want or it[1].strip("/").split("/")[0] in want]
    allow = set(args.allow)
    ss = eos_storage_state(token)

    findings: dict[str, list[tuple[str, str]]] = {}

    def human(s):
        if not args.json:
            print(s, flush=True)

    human(f"walking {len(items)} app(s), recycle every {args.recycle}")
    with sync_playwright() as p:
        def fresh():
            b = p.chromium.launch()
            c = b.new_context(storage_state=ss) if ss else b.new_context()
            return b, c

        browser, ctx = fresh()
        n_since = 0
        for i, (pid, pref) in enumerate(items):
            if n_since >= args.recycle:
                try:
                    ctx.close(); browser.close()
                except Exception:  # noqa: BLE001
                    pass
                browser, ctx = fresh(); n_since = 0
            errs: list[tuple[str, str]] = []
            try:
                page = ctx.new_page()
                page.on("pageerror", lambda e: errs.append(("pageerror", str(e))))
                page.on("console", lambda m: errs.append(("console", m.text)) if m.type == "error" else None)
                page.on(
                    "response",
                    lambda r: errs.append(("http", f"{r.status} {r.url}"))
                    if r.status >= 400 and not any(s in r.url for s in _IGNORE_URL)
                    else None,
                )
                try:
                    page.goto(f"{BASE}{pref}", timeout=15000, wait_until="domcontentloaded")
                except Exception as e:  # noqa: BLE001
                    errs.append(("nav", str(e)[:160]))
                try:
                    page.wait_for_timeout(args.settle)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    page.close()
                except Exception:  # noqa: BLE001
                    pass
                n_since += 1
            except Exception as e:  # noqa: BLE001 — page crashed the browser; respawn, don't blame the app
                errs.append(("CRASH", str(e)[:160]))
                try:
                    ctx.close(); browser.close()
                except Exception:  # noqa: BLE001
                    pass
                browser, ctx = fresh(); n_since = 0
            if errs:
                # dedup by (kind, first 120 chars)
                seen = set(); uniq = []
                for k, m in errs:
                    key = (k, m[:120])
                    if key in seen:
                        continue
                    seen.add(key); uniq.append((k, m))
                findings[pid] = uniq
                tag = "ALLOW" if pid in allow else "FAIL"
                human(f"  [{i+1}/{len(items)}] {pid:28} {len(uniq)} {tag}")
                for k, m in uniq:
                    human(f"        [{k}] {m[:220]}")
        try:
            ctx.close(); browser.close()
        except Exception:  # noqa: BLE001
            pass

    real = {pid: e for pid, e in findings.items() if pid not in allow}
    if args.json:
        print(json.dumps({
            "ok": not real,
            "code": "ok" if not real else "findings",
            "message": f"{len(real)} app(s) with interactive errors",
            "data": {"findings": {pid: [{"kind": k, "msg": m} for k, m in e] for pid, e in findings.items()},
                     "allowlisted": sorted(allow)},
        }))
    else:
        if real:
            print(f"\n{len(real)} app(s) with interactive errors "
                  f"({len(findings) - len(real)} allowlisted). "
                  f"Triage data-volume slow-load / OOM as NOT a code bug (see .claude/rules/audits.md).")
        else:
            print(f"\nclean — {len(items)} app(s) walked, no interactive errors"
                  + (f" ({len(findings)} allowlisted)" if findings else ""))
    return 1 if real else 0


if __name__ == "__main__":
    sys.exit(main())
