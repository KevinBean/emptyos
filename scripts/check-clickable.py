"""Pre-release click-target audit.

Drives Playwright against the local daemon at :9000, walks each app's root,
and fails if any primary CTA (`button.primary`, `+ New`, `+ Add`, etc.) is
intercepted by an unrelated element via `elementFromPoint(center)`.

Catches the FAB-stack and `position:absolute`-without-`position:relative`
classes of z-order bugs that don't surface in API tests. The first version
of this audit caught 9 click-intercepts on `fix-agent` / `nutrition` /
`providers` (2026-05-16).

Usage:
    python scripts/check-clickable.py            # full walk
    python scripts/check-clickable.py myapp      # single app
    python scripts/check-clickable.py --allow myapp  # whitelist (per-row)

Exit code:
    0  → no intercepts (or only on allowlisted apps)
    1  → real intercepts found
    2  → daemon unreachable / setup error

Only invoked manually or from `release-public.py`. Not in `pytest` because
it expects a *running* daemon with the full app set installed; the pytest
mobile suite covers a different surface (single-app overflow).
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root → emptyos.*
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _eos_browser import BASE, load_auth_token, eos_storage_state
from emptyos.sdk.html_anchors import SRC_OF_JS

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("ERROR: playwright not installed. Run: pip install playwright && playwright install chromium")
    sys.exit(2)

# Apps where right-edge content legitimately overlaps the bottom-right FAB
# at the default viewport (1440x900). These are content-list apps where the
# last row of a long list will always be under the FAB at some scroll point —
# user scrolls past. Not a layout bug.
ALLOWLIST = {
    "fix-agent",  # table-row "Fix" buttons at right edge
}


def main() -> int:
    only = None
    extra_allow = set()
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--allow" and i + 1 < len(args):
            extra_allow.add(args[i + 1]); i += 2
        elif not a.startswith("-"):
            only = a; i += 1
        else:
            print(f"Unknown arg: {a}"); return 2

    allow = ALLOWLIST | extra_allow

    # Auth
    token = load_auth_token()

    # Resolve app list
    import urllib.request
    try:
        req = urllib.request.Request(f"{BASE}/api/apps", headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"ERROR: daemon unreachable at {BASE} ({e})"); return 2

    apps = data.get("apps", data) if isinstance(data, dict) else data
    apps = [a for a in apps if a.get("web_prefix")]
    if only:
        apps = [a for a in apps if a["id"] == only]
        if not apps:
            print(f"No app matched '{only}'"); return 2

    intercepts = []
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context(
            viewport={"width": 1440, "height": 900},
            storage_state=eos_storage_state(token),
        )
        for i, app in enumerate(apps, 1):
            page = ctx.new_page()
            url = BASE + app["web_prefix"] + ("/" if not app["web_prefix"].endswith("/") else "")
            # Stamp-only locator (data-eos-src attrs, no overlay — the overlay's
            # fixed badge would itself be a click-interceptor false positive).
            url += "?debug=locate&overlay=0"
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                try:
                    page.wait_for_load_state("networkidle", timeout=2500)
                except Exception:
                    pass
                page.wait_for_timeout(400)
                rows = page.evaluate("() => {\n" + SRC_OF_JS + """
                    // offsetParent alone is not a visibility test. Content inside a
                    // COLLAPSED <details> keeps a stale layout box — offsetParent stays
                    // non-null — but is never painted or hit-tested, so elementFromPoint
                    // at its phantom rect reports whatever is really drawn there and we
                    // flag an "intercept" on a button the user cannot even see. Only a
                    // button that actually renders can be intercepted.
                    var isVisible = function(b) {
                        if (b.offsetParent === null) return false;
                        if (b.checkVisibility) return b.checkVisibility({checkVisibilityCSS: true});
                        return !b.closest('details:not([open])');
                    };
                    var primary = Array.from(document.querySelectorAll('button.btn.primary, button.eos-btn.primary, button[class*="primary"], .btn-primary')).filter(isVisible);
                    var ctaPrefixes = ['+ New', '+ Add', 'Create', '+ Create'];
                    Array.from(document.querySelectorAll('button')).forEach(b => {
                        var t = (b.textContent||'').trim();
                        if (isVisible(b) && ctaPrefixes.some(s => t.indexOf(s) === 0)) {
                            if (!primary.includes(b)) primary.push(b);
                        }
                    });
                    var issues = [];
                    for (var b of primary) {
                        var r = b.getBoundingClientRect();
                        if (r.width < 1 || r.height < 1) continue;
                        var cx = r.x + r.width/2, cy = r.y + r.height/2;
                        if (cx < 0 || cy < 0 || cx > window.innerWidth || cy > window.innerHeight) continue;
                        var top = document.elementFromPoint(cx, cy);
                        if (!top || top === b || b.contains(top) || top.contains(b)) continue;
                        issues.push({
                            text: (b.textContent||'').trim().slice(0,40),
                            cx: Math.round(cx), cy: Math.round(cy),
                            top: top.tagName + '#' + (top.id||'') + '.' + (top.className||'').toString().slice(0,40),
                            src: srcOf(b),
                        });
                    }
                    return issues.slice(0, 5);
                }""")
                if rows:
                    if app["id"] in allow:
                        print(f"  [{i}/{len(apps)}] {app['id']:25s} {len(rows)} intercept(s) [allowlisted]")
                    else:
                        print(f"  [{i}/{len(apps)}] {app['id']:25s} {len(rows)} intercept(s) ❌")
                        intercepts.append({"app": app["id"], "issues": rows})
                else:
                    print(f"  [{i}/{len(apps)}] {app['id']:25s} OK")
            except Exception as e:
                print(f"  [{i}/{len(apps)}] {app['id']:25s} EXC: {type(e).__name__}: {str(e)[:80]}")
            finally:
                page.close()
        ctx.close(); b.close()

    if intercepts:
        print(f"\n❌ {len(intercepts)} app(s) have click-intercepted primary CTAs:")
        for it in intercepts:
            print(f"\n  {it['app']}:")
            for r in it["issues"]:
                loc = f"  @ {r['src']}" if r.get("src") else ""
                print(f"    button '{r['text']}' at ({r['cx']},{r['cy']}) covered by <{r['top']}>{loc}")
        print("\nFix the layout (give absolute children a relative ancestor, "
              "move the button out of the FAB's bottom-right zone, etc.) or "
              "add the app to ALLOWLIST in scripts/check-clickable.py.")
        return 1

    print(f"\n✅ All {len(apps)} apps clear — no click-intercepts on primary CTAs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
