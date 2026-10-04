"""UI-walk round 2 — operations the round-1 walker didn't cover.

Four passes, one Chromium per pass for clean state:
  mobile  — viewport 375x667; catch overflow, modal-too-tall, missing nav collapse
  tabs    — click every eos-tab/role=tab on each app, catch per-tab JS errors
  click   — proper elementFromPoint(center) intercept audit on every visible CTA
  hash    — visit /app/#bogus to confirm hash-route apps don't crash on unknown ids

Findings append to data/ui-walk/findings-round2.jsonl. Per-pass screenshots
under data/ui-walk/screenshots-round2/.
"""
from __future__ import annotations
import json, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root → emptyos.*
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _eos_browser import BASE, load_auth_token, eos_storage_state
from emptyos.sdk.html_anchors import SRC_OF_JS

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "ui-walk"
SHOTS = OUT / "screenshots-round2"; SHOTS.mkdir(exist_ok=True)
FINDINGS = OUT / "findings-round2.jsonl"
TOKEN = load_auth_token()


def _ctx(p, width=1440, height=900):
    return p.chromium.launch(headless=True).new_context(
        viewport={"width": width, "height": height},
        storage_state=eos_storage_state(TOKEN),
    )


def emit(rec):
    with FINDINGS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def _attach(page, errs):
    page.on("pageerror", lambda e: errs.append(("pageerror", str(e)[:300])))
    page.on("console", lambda m: errs.append(("console", m.text[:300])) if m.type == "error" else None)


def pass_mobile(apps):
    """375x667 viewport. Catch horizontal overflow, missing nav collapse, ovesized modals."""
    print("=== MOBILE pass (375x667) ===")
    with sync_playwright() as p:
        ctx = _ctx(p, 375, 667)
        for i, app in enumerate(apps, 1):
            page = ctx.new_page(); errs = []; _attach(page, errs)
            url = BASE + app["prefix"] + "/" if not app["prefix"].endswith("/") else BASE + app["prefix"]
            # Stamp-only locator (data-eos-src attrs, no overlay → clean shots).
            nav_url = url + ("&" if "?" in url else "?") + "debug=locate&overlay=0"
            try:
                r = page.goto(nav_url, wait_until="domcontentloaded", timeout=15000)
                try: page.wait_for_load_state("networkidle", timeout=2500)
                except Exception: pass
                time.sleep(0.3)
                info = page.evaluate("() => {\n" + SRC_OF_JS + """
                    var doc = document.documentElement;
                    var hOverflow = doc.scrollWidth > window.innerWidth + 4;
                    // Wide elements that cause horizontal scroll
                    var wide = [];
                    if (hOverflow) {
                        Array.from(document.querySelectorAll('*')).forEach(el => {
                            var r = el.getBoundingClientRect();
                            if (r.right > window.innerWidth + 8 && r.width > 100 && r.width < window.innerWidth * 2.5) {
                                wide.push({tag: el.tagName, cls: (el.className||'').toString().slice(0,40), w: Math.round(r.width), right: Math.round(r.right), src: srcOf(el)});
                            }
                        });
                    }
                    // Detect missing nav collapse: a fixed/sticky nav with >5 visible direct children at 375w is bad
                    var navs = Array.from(document.querySelectorAll('nav, header, .eos-header, .topbar')).filter(n => {
                        var s = getComputedStyle(n);
                        return s.position === 'fixed' || s.position === 'sticky' || s.position === 'absolute';
                    });
                    var navIssue = null;
                    for (var n of navs) {
                        var kids = Array.from(n.querySelectorAll(':scope > a, :scope > button, :scope > .eos-tab, :scope > .nav-item')).filter(k => k.offsetParent !== null);
                        if (kids.length > 6) {
                            navIssue = {tag: n.tagName, kids: kids.length, cls: (n.className||'').toString().slice(0,40)};
                            break;
                        }
                    }
                    return {hOverflow, wide: wide.slice(0, 5), navIssue, bodyHeight: document.body.scrollHeight};
                }""")
                if info.get("hOverflow"):
                    wide_desc = ", ".join(f"{w['tag']}.{w['cls']}(w={w['w']})" for w in info.get("wide", [])[:3])
                    src = next((w.get("src") for w in info.get("wide", []) if w.get("src")), "")
                    emit({"ts": time.time(), "pass": "mobile", "app": app["id"], "severity": "broken",
                          "category": "mobile-overflow", "message": f"Horizontal overflow at 375w: {wide_desc}",
                          "url": url, "src": src})
                if info.get("navIssue"):
                    n = info["navIssue"]
                    emit({"ts": time.time(), "pass": "mobile", "app": app["id"], "severity": "polish",
                          "category": "mobile-nav", "message": f"Nav doesn't collapse: <{n['tag']}> shows {n['kids']} items at 375w",
                          "url": url, "nav_cls": n.get("cls", "")})
                # Capture per-pass screenshot for spot-checking
                page.screenshot(path=str(SHOTS / f"mobile-{app['id']}.png"), full_page=False)
                for kind, txt in errs[:3]:
                    emit({"ts": time.time(), "pass": "mobile", "app": app["id"], "severity": "broken",
                          "category": f"mobile-{kind}", "message": txt[:250], "url": url})
                print(f"  [{i:3d}/{len(apps)}] {app['id']:25s} overflow={info.get('hOverflow')} nav_issue={bool(info.get('navIssue'))}")
            except Exception as e:
                emit({"ts": time.time(), "pass": "mobile", "app": app["id"], "severity": "broken",
                      "category": "load", "message": f"{type(e).__name__}: {str(e)[:200]}", "url": url})
                print(f"  [{i:3d}/{len(apps)}] {app['id']:25s} EXC: {type(e).__name__}")
            finally:
                page.close()
        ctx.browser.close()


def pass_tabs(apps):
    """Click every .eos-tab / [role=tab] in each app, capture per-tab JS errors."""
    print("=== TABS pass ===")
    with sync_playwright() as p:
        ctx = _ctx(p)
        for i, app in enumerate(apps, 1):
            page = ctx.new_page(); errs = []; _attach(page, errs)
            url = BASE + app["prefix"] + "/" if not app["prefix"].endswith("/") else BASE + app["prefix"]
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                try: page.wait_for_load_state("networkidle", timeout=2500)
                except Exception: pass
                time.sleep(0.3)
                tabs = page.query_selector_all(".eos-tab:not(.eos-tab-content), [role='tab'], .tab-button")
                visible = [t for t in tabs if t.is_visible()]
                tab_count = len(visible)
                if tab_count < 2:
                    print(f"  [{i:3d}/{len(apps)}] {app['id']:25s} no tabs")
                    page.close(); continue
                tab_texts = []
                for j, t in enumerate(visible[:8]):  # cap at 8 tabs per app
                    txt = (t.inner_text() or f"tab-{j}").strip()[:30]
                    tab_texts.append(txt)
                    errs_before = len(errs)
                    try:
                        t.click(timeout=2000)
                        time.sleep(0.3)
                        new_errs = errs[errs_before:]
                        for kind, e in new_errs[:2]:
                            emit({"ts": time.time(), "pass": "tabs", "app": app["id"], "severity": "broken",
                                  "category": f"tab-{kind}", "message": f"Tab '{txt}' click → {e[:200]}",
                                  "tab": txt, "url": url})
                    except Exception as e:
                        emit({"ts": time.time(), "pass": "tabs", "app": app["id"], "severity": "polish",
                              "category": "tab-click", "message": f"Tab '{txt}' click failed: {type(e).__name__}",
                              "tab": txt, "url": url})
                print(f"  [{i:3d}/{len(apps)}] {app['id']:25s} {tab_count} tabs cycled")
            except Exception as e:
                emit({"ts": time.time(), "pass": "tabs", "app": app["id"], "severity": "broken",
                      "category": "load", "message": f"{type(e).__name__}: {str(e)[:200]}", "url": url})
            finally:
                page.close()
        ctx.browser.close()


def pass_click(apps):
    """Real click-intercept audit. For each visible primary button, check elementFromPoint(center).
    Reports only true intercepts (button center resolves to an unrelated element)."""
    print("=== CLICK-INTERCEPT pass ===")
    with sync_playwright() as p:
        ctx = _ctx(p)
        for i, app in enumerate(apps, 1):
            page = ctx.new_page(); errs = []; _attach(page, errs)
            url = BASE + app["prefix"] + "/" if not app["prefix"].endswith("/") else BASE + app["prefix"]
            # Stamp-only locator (no overlay — the overlay badge is itself a
            # fixed-position interceptor that would be a false positive here).
            nav_url = url + ("&" if "?" in url else "?") + "debug=locate&overlay=0"
            try:
                page.goto(nav_url, wait_until="domcontentloaded", timeout=15000)
                try: page.wait_for_load_state("networkidle", timeout=2500)
                except Exception: pass
                time.sleep(0.3)
                report = page.evaluate("() => {\n" + SRC_OF_JS + """
                    var primary = Array.from(document.querySelectorAll('button.btn.primary, button.eos-btn.primary, button[class*="primary"], .btn-primary, .eos-btn:not(.eos-btn-ghost):not(.eos-btn-sm)')).filter(b => b.offsetParent !== null);
                    // Also grab obvious CTAs by text
                    var ctaSelectors = ['+ New', '+ Add', 'Add ', 'Create', 'Save', 'Submit'];
                    Array.from(document.querySelectorAll('button')).forEach(b => {
                        var t = (b.textContent||'').trim();
                        if (b.offsetParent !== null && ctaSelectors.some(s => t.indexOf(s) === 0)) {
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
                        if (!top) continue;
                        if (top === b) continue;
                        if (b.contains(top) || top.contains(b)) continue;
                        // Real intercept
                        issues.push({
                            text: (b.textContent||'').trim().slice(0,40),
                            cx: Math.round(cx), cy: Math.round(cy),
                            topTag: top.tagName,
                            topId: top.id || '',
                            topCls: (top.className||'').toString().slice(0,60),
                            src: srcOf(b),
                        });
                    }
                    return issues.slice(0, 5);
                }""")
                for it in report:
                    loc = f" @ {it['src']}" if it.get("src") else ""
                    emit({"ts": time.time(), "pass": "click", "app": app["id"], "severity": "broken",
                          "category": "click-intercept", "message": f"Button '{it['text']}' at ({it['cx']},{it['cy']}) covered by <{it['topTag']}#{it['topId']} class='{it['topCls']}'>{loc}",
                          "url": url, **it})
                print(f"  [{i:3d}/{len(apps)}] {app['id']:25s} {len(report)} intercept(s)")
            except Exception as e:
                emit({"ts": time.time(), "pass": "click", "app": app["id"], "severity": "broken",
                      "category": "load", "message": f"{type(e).__name__}: {str(e)[:200]}", "url": url})
            finally:
                page.close()
        ctx.browser.close()


def pass_hash(apps):
    """Visit /app/#bogus for hash-route apps. Confirm graceful behavior."""
    print("=== HASH-ROUTE pass ===")
    # Only test apps that we can tell use hashRoute by sniffing the page source for it
    with sync_playwright() as p:
        ctx = _ctx(p)
        for i, app in enumerate(apps, 1):
            page = ctx.new_page(); errs = []; _attach(page, errs)
            base_url = BASE + app["prefix"] + "/" if not app["prefix"].endswith("/") else BASE + app["prefix"]
            try:
                page.goto(base_url, wait_until="domcontentloaded", timeout=15000)
                html = page.content()
                if "hashRoute" not in html and "EOS_UI.hashRoute" not in html:
                    page.close(); continue  # not a hash-route app
                # Navigate to bogus hash
                bogus_url = base_url + "#bogus-id-zz" + str(int(time.time()))
                page.goto(bogus_url, wait_until="domcontentloaded", timeout=10000)
                try: page.wait_for_load_state("networkidle", timeout=2500)
                except Exception: pass
                time.sleep(0.6)
                # Check for pageerror or app crash
                for kind, e in errs[:3]:
                    emit({"ts": time.time(), "pass": "hash", "app": app["id"], "severity": "broken",
                          "category": f"hash-{kind}", "message": f"Bogus hash → {e[:200]}", "url": bogus_url})
                # If body is empty after hash nav, that's a crash
                body_len = page.evaluate("() => document.body ? document.body.innerHTML.length : 0")
                if body_len < 1000:
                    emit({"ts": time.time(), "pass": "hash", "app": app["id"], "severity": "broken",
                          "category": "hash-empty-body", "message": f"Body collapsed to {body_len} chars on bogus hash",
                          "url": bogus_url})
                print(f"  [{i:3d}/{len(apps)}] {app['id']:25s} hash-route OK")
            except Exception as e:
                emit({"ts": time.time(), "pass": "hash", "app": app["id"], "severity": "broken",
                      "category": "load", "message": f"{type(e).__name__}: {str(e)[:200]}", "url": base_url})
            finally:
                page.close()
        ctx.browser.close()


if __name__ == "__main__":
    apps = json.loads((OUT / "apps.json").read_text())
    only = sys.argv[2] if len(sys.argv) > 2 else None
    if only:
        apps = [a for a in apps if a["id"] == only]
    pass_name = sys.argv[1] if len(sys.argv) > 1 else "all"
    runners = {"mobile": pass_mobile, "tabs": pass_tabs, "click": pass_click, "hash": pass_hash}
    if pass_name == "all":
        for name, fn in runners.items():
            fn(apps)
    elif pass_name in runners:
        runners[pass_name](apps)
    else:
        print(f"Usage: {sys.argv[0]} {{mobile|tabs|click|hash|all}} [app_id]"); sys.exit(1)
