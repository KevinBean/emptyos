"""UI-walk audit — drives Playwright over every app at :9000 looking for issues pytest can't catch.

Captures per-app: console errors/warnings, failed network requests, missing-element
checks, layout/render heuristics, JS exceptions. Writes JSONL findings + screenshots
to data/ui-walk/.

Run modes:
  python scripts/ui_walk_audit.py probe       — list apps via API, write apps.json
  python scripts/ui_walk_audit.py walk        — walk every app, write findings.jsonl
  python scripts/ui_walk_audit.py walk <id>   — walk one app
  python scripts/ui_walk_audit.py interact    — deep click pass (settings/add/tab)
  python scripts/ui_walk_audit.py report      — grouped markdown report of all findings
  python scripts/ui_walk_audit.py report-html — self-contained HTML report w/ embedded screenshots
  python scripts/ui_walk_audit.py triage      — drop walker-noise, surface real bugs

The `triage` mode is the audits.md discipline in code: heuristics fire on
healthy apps too, so it filters the known-noise categories (FAB stacks,
generic titles, WebGL/iframe browser warnings) and keeps the signal that
matters — JS exceptions, console errors, failed requests, load/render
blockers, interaction-probe errors — then clusters by message shape so a
systemic issue (same shape across many apps) stands out from a one-off.
"""
from __future__ import annotations
import json, sys, time, re
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _eos_browser import BASE, load_auth_token, eos_storage_state, embed_image_data_uri

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "ui-walk"
OUT.mkdir(parents=True, exist_ok=True)
SHOTS = OUT / "screenshots"; SHOTS.mkdir(exist_ok=True)
SNAPS = OUT / "snapshots"; SNAPS.mkdir(exist_ok=True)

TOKEN = load_auth_token()

def http_json(path: str):
    req = Request(BASE + path, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Cookie": f"eos_session={TOKEN}",
    })
    with urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def cmd_probe():
    try:
        data = http_json("/api/apps")
    except HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"); return
    rows = data.get("apps", data) if isinstance(data, dict) else data
    apps = []
    for a in rows:
        prefix = a.get("web_prefix") or ""
        if not prefix:
            continue
        apps.append({
            "id": a.get("id"),
            "name": a.get("name") or a.get("id"),
            "prefix": prefix,
            "description": (a.get("description") or "")[:120],
            "private": a.get("private", False),
        })
    apps.sort(key=lambda x: x["id"])
    (OUT / "apps.json").write_text(json.dumps(apps, indent=2))
    print(f"Wrote {len(apps)} apps to {OUT/'apps.json'}")
    for a in apps:
        print(f"  {a['id']:30s} {a['prefix']}")


def cmd_walk(only: str | None = None):
    from playwright.sync_api import sync_playwright

    apps = json.loads((OUT / "apps.json").read_text())
    if only:
        apps = [a for a in apps if a["id"] == only or a["prefix"] == only or a["prefix"].strip("/") == only]
        if not apps:
            print(f"No app matched '{only}'"); return

    findings_path = OUT / "findings.jsonl"
    # Append mode; we'll dedupe at report time
    findings_f = findings_path.open("a", encoding="utf-8")

    def emit(app_id: str, category: str, severity: str, message: str, **extra):
        rec = {"ts": time.time(), "app": app_id, "category": category,
               "severity": severity, "message": message, **extra}
        findings_f.write(json.dumps(rec) + "\n"); findings_f.flush()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 900},
            storage_state=eos_storage_state(TOKEN),
        )

        for i, app in enumerate(apps, 1):
            page = ctx.new_page()
            console_msgs = []
            page_errors = []
            failed_requests = []

            def on_console(msg, app=app):
                if msg.type in ("error", "warning"):
                    console_msgs.append({"type": msg.type, "text": msg.text[:500]})
            def on_pageerror(err):
                page_errors.append(str(err)[:500])
            def on_requestfailed(req):
                if req.url.startswith(BASE):
                    failed_requests.append({"url": req.url, "failure": str(req.failure)[:200], "method": req.method})

            page.on("console", on_console)
            page.on("pageerror", on_pageerror)
            page.on("requestfailed", on_requestfailed)

            url = BASE + app["prefix"]
            if not url.endswith("/"): url += "/"
            print(f"[{i}/{len(apps)}] {app['id']:28s} -> {url}")
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=20000)
                if resp is None:
                    emit(app["id"], "load", "blocker", "No response object", url=url)
                elif resp.status >= 400:
                    emit(app["id"], "load", "blocker", f"HTTP {resp.status}", url=url, status=resp.status)
                # Allow async UI to render
                try:
                    page.wait_for_load_state("networkidle", timeout=4000)
                except Exception:
                    pass
                time.sleep(0.4)

                # Heuristic checks
                html_len = page.evaluate("() => document.body ? document.body.innerHTML.length : 0")
                if html_len < 80:
                    emit(app["id"], "render", "broken", f"Body essentially empty ({html_len} chars)", url=url)

                title = page.title()
                if not title or title == "EmptyOS":
                    # Many apps may inherit generic title — note as polish only
                    emit(app["id"], "polish", "polish", f"Generic/no <title> ('{title}')", url=url)

                # Detect server-side error pages
                body_text = page.evaluate("() => (document.body && document.body.innerText) ? document.body.innerText.slice(0, 4000) : ''")
                bt = body_text.lower()
                if "internal server error" in bt or "traceback" in bt or "500 internal" in bt:
                    emit(app["id"], "render", "blocker", "Error page content detected", url=url, snippet=body_text[:300])

                # Detect truly empty page (very low text + no nav)
                if html_len < 800:
                    emit(app["id"], "render", "broken", f"Page nearly empty (html_len={html_len})", url=url, html_len=html_len)

                # Theme bootstrap check (per feedback memory)
                bg = page.evaluate("() => getComputedStyle(document.body).backgroundColor")
                if bg in ("rgba(0, 0, 0, 0)", "rgb(255, 255, 255)"):
                    # White or transparent body usually means tokens didn't resolve
                    emit(app["id"], "theme", "broken", f"Body background not themed (got {bg})", url=url)

                # Check primary fixed-position controls don't stack on top of each other
                fab_count = page.evaluate("""() => Array.from(document.querySelectorAll('*'))
                    .filter(el => {
                        const s = getComputedStyle(el);
                        return s.position === 'fixed' && (parseFloat(s.bottom) >= 0 || parseFloat(s.right) >= 0);
                    }).length""")
                if fab_count > 10:
                    emit(app["id"], "ux", "polish", f"{fab_count} fixed-position elements (likely FAB stack collision)", url=url, fab_count=fab_count)

                # Settings panel presence when manifest declares one — we'd need to know that; for now, just check if there's a settings link/button
                has_settings_btn = page.evaluate("""() => !!document.querySelector('.btn-settings, [onclick*=Setting], [onclick*=settings], [aria-label*=Settings i]')""")

                # Capture screenshot
                shot = SHOTS / f"{app['id']}.png"
                try:
                    page.screenshot(path=str(shot), full_page=False)
                except Exception as e:
                    emit(app["id"], "screenshot", "polish", f"Screenshot failed: {e}", url=url)

                # Snapshot DOM outline for later cross-app analysis
                outline = page.evaluate("""() => {
                    const buttons = Array.from(document.querySelectorAll('button, [role=button], .btn, .eos-btn')).slice(0, 50)
                        .map(b => (b.textContent || b.getAttribute('aria-label') || '').trim().slice(0, 80))
                        .filter(Boolean);
                    const inputs = Array.from(document.querySelectorAll('input, textarea, select')).slice(0, 30)
                        .map(i => ({type: i.type || i.tagName.toLowerCase(), placeholder: i.placeholder || '', name: i.name || ''}));
                    const links = Array.from(document.querySelectorAll('a[href]')).slice(0, 40)
                        .map(a => ({text: (a.textContent||'').trim().slice(0,60), href: a.getAttribute('href') || ''}))
                        .filter(l => l.href && !l.href.startsWith('http'));
                    const headings = Array.from(document.querySelectorAll('h1, h2, h3')).slice(0, 20)
                        .map(h => (h.textContent || '').trim().slice(0, 80)).filter(Boolean);
                    return {buttons, inputs, links, headings};
                }""")
                (SNAPS / f"{app['id']}.json").write_text(json.dumps({
                    "app": app["id"], "url": url, "title": title,
                    "html_len": html_len, "fab_count": fab_count,
                    "has_settings_btn": has_settings_btn,
                    "outline": outline,
                }, indent=2))

                # Report console/page errors as findings
                for m in console_msgs:
                    sev = "broken" if m["type"] == "error" else "polish"
                    # Filter known-noisy chrome warnings
                    txt = m["text"]
                    if "Failed to load resource" in txt:
                        # Will be covered by failed_requests; suppress duplicates
                        continue
                    emit(app["id"], "console", sev, txt[:300], type=m["type"], url=url)
                for e in page_errors:
                    emit(app["id"], "js-exception", "broken", e[:400], url=url)
                for r in failed_requests:
                    sev = "broken"
                    emit(app["id"], "network", sev, f"{r['method']} {r['url']} — {r['failure']}", page_url=url, req_url=r["url"], req_method=r["method"])

            except Exception as e:
                emit(app["id"], "load", "blocker", f"Exception: {type(e).__name__}: {e}", url=url)
            finally:
                page.close()

        ctx.close(); browser.close()

    findings_f.close()
    print(f"\nFindings written to {findings_path}")


def cmd_report():
    """Aggregate findings.jsonl into a grouped human-readable markdown report."""
    findings_path = OUT / "findings.jsonl"
    if not findings_path.exists():
        print("no findings file"); return
    findings = [json.loads(l) for l in findings_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    by_app: dict[str, list[dict]] = {}
    for f in findings:
        by_app.setdefault(f["app"], []).append(f)

    # Severity ordering
    sev_rank = {"blocker": 0, "broken": 1, "polish": 2}
    lines = ["# UI walk findings", ""]
    lines.append(f"Total findings: **{len(findings)}** across **{len(by_app)}** apps. Run at {time.strftime('%Y-%m-%d %H:%M')}.")
    lines.append("")

    # Severity buckets
    blocker = [f for f in findings if f["severity"] == "blocker"]
    broken = [f for f in findings if f["severity"] == "broken"]
    polish = [f for f in findings if f["severity"] == "polish"]
    lines.append(f"- 🚨 Blocker: **{len(blocker)}**")
    lines.append(f"- 🔧 Broken: **{len(broken)}**")
    lines.append(f"- ✨ Polish: **{len(polish)}**")
    lines.append("")

    # Category patterns
    from collections import Counter
    cat_count = Counter(f["category"] for f in findings)
    lines.append("## Patterns")
    for cat, n in cat_count.most_common():
        lines.append(f"- `{cat}`: {n}")
    lines.append("")

    # Per-app breakdown
    lines.append("## Per-app findings")
    for app_id in sorted(by_app.keys()):
        items = sorted(by_app[app_id], key=lambda x: sev_rank.get(x["severity"], 9))
        # Skip apps with only polish-level title generic
        only_generic_title = all(
            i["category"] == "polish" and "Generic/no <title>" in i.get("message", "")
            for i in items
        )
        if only_generic_title:
            continue
        lines.append(f"\n### `{app_id}` ({len(items)} finding{'s' if len(items)!=1 else ''})")
        for it in items:
            icon = {"blocker":"🚨","broken":"🔧","polish":"✨"}.get(it["severity"], "•")
            lines.append(f"- {icon} **{it['category']}** — {it['message']}")
    out_path = OUT / "report.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report -> {out_path}  ({len(findings)} findings)")


def cmd_interact(only: str | None = None):
    """Deep interaction: try clicking common affordances, opening modals, etc."""
    from playwright.sync_api import sync_playwright
    apps = json.loads((OUT / "apps.json").read_text())
    if only:
        apps = [a for a in apps if a["id"] == only]

    findings_f = (OUT / "findings.jsonl").open("a", encoding="utf-8")
    def emit(app_id, category, severity, message, **extra):
        findings_f.write(json.dumps({"ts": time.time(), "app": app_id, "category": category,
            "severity": severity, "message": message, "phase": "interact", **extra}) + "\n"); findings_f.flush()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 900},
            storage_state=eos_storage_state(TOKEN),
        )
        for i, app in enumerate(apps, 1):
            page = ctx.new_page()
            errs = []
            page.on("pageerror", lambda e: errs.append(str(e)[:300]))
            console = []
            def _con(msg, lst=console):
                if msg.type == "error": lst.append(msg.text[:300])
            page.on("console", _con)

            url = BASE + app["prefix"]
            if not url.endswith("/"): url += "/"
            print(f"[{i}/{len(apps)}] interact {app['id']:28s}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                try: page.wait_for_load_state("networkidle", timeout=3000)
                except Exception: pass
                time.sleep(0.3)

                # 1. Try clicking settings button if present
                try:
                    settings_btn = page.query_selector(".btn-settings, [onclick*='Setting'], [onclick*='setting'], button:has-text('Settings'), button:has-text('⚙')")
                    if settings_btn:
                        errs_before = len(errs) + len(console)
                        settings_btn.click(timeout=2000)
                        time.sleep(0.5)
                        # Check if a panel opened
                        panel = page.query_selector(".eos-settings-panel, .eos-modal, [class*='settings-panel'], [class*='modal']")
                        if not panel:
                            emit(app["id"], "interact-settings", "broken", "Clicked settings button but no panel/modal opened")
                        # close modal
                        try: page.keyboard.press("Escape")
                        except Exception: pass
                        time.sleep(0.2)
                        new_errs = errs[errs_before:] + console[errs_before:]
                        for e in new_errs:
                            emit(app["id"], "interact-settings", "broken", f"Settings click error: {e[:200]}")
                except Exception as e:
                    emit(app["id"], "interact-settings", "polish", f"Settings probe exception: {type(e).__name__}: {str(e)[:150]}")

                # 2. Try Add/Create button
                try:
                    add_btn = page.query_selector("button:has-text('+ Add'), button:has-text('Add'), button:has-text('+ New'), button:has-text('New'), button:has-text('Create'), button.btn-add, button.eos-fab")
                    if add_btn and add_btn.is_visible():
                        errs_before = len(errs) + len(console)
                        add_btn.click(timeout=2000)
                        time.sleep(0.5)
                        modal = page.query_selector(".eos-modal, [role='dialog'], .modal-backdrop, .modal")
                        # not all add buttons open modals (some inline-add) — only warn if nothing changed
                        if modal:
                            # Try Escape to dismiss
                            page.keyboard.press("Escape")
                            time.sleep(0.2)
                            modal2 = page.query_selector(".eos-modal[style*='display: block'], [role='dialog']:not([hidden])")
                            if modal2 and modal2.is_visible():
                                emit(app["id"], "interact-add", "polish", "Modal didn't close on Escape")
                        new_errs = errs[errs_before:] + console[errs_before:]
                        for e in new_errs:
                            emit(app["id"], "interact-add", "broken", f"Add click error: {e[:200]}")
                except Exception as e:
                    emit(app["id"], "interact-add", "polish", f"Add probe exception: {type(e).__name__}: {str(e)[:150]}")

                # 3. Try first tab if app has them
                try:
                    tabs = page.query_selector_all(".eos-tab, [role='tab'], .tab-button")
                    if len(tabs) >= 2:
                        errs_before = len(errs) + len(console)
                        tabs[1].click(timeout=2000)
                        time.sleep(0.4)
                        new_errs = errs[errs_before:] + console[errs_before:]
                        for e in new_errs:
                            emit(app["id"], "interact-tab", "broken", f"Tab switch error: {e[:200]}")
                except Exception as e:
                    pass  # tabs are very app-specific; skip exception noise

            except Exception as e:
                emit(app["id"], "interact", "blocker", f"Interaction exception: {type(e).__name__}: {str(e)[:200]}")
            finally:
                page.close()
        ctx.close(); browser.close()
    findings_f.close()


# Categories that are real interactive/backend signal (not heuristic noise).
_TRIAGE_REAL_CATEGORIES = {
    "js-exception", "console", "network", "load",
    "interact-settings", "interact-add", "interact-tab", "interact",
}
# Message fragments that are architecture/browser-chrome, never a per-app bug.
_TRIAGE_NOISE_MESSAGES = (
    "Generic/no <title>",
    "fixed-position elements",   # FAB stack — every app has ~10, by design
)


def _triage_is_real(f: dict) -> bool:
    msg = f.get("message", "")
    if any(n in msg for n in _TRIAGE_NOISE_MESSAGES):
        return False
    cat = f.get("category", "")
    if cat in _TRIAGE_REAL_CATEGORIES:
        return True
    # `render` is real only as a blocker (error page / empty body), not the
    # noisy "nearly empty html_len" double-fire on dashboards.
    return cat == "render" and f.get("severity") == "blocker"


def cmd_triage():
    """Filter findings.jsonl to real bugs and cluster by message shape.

    Console-warning lines (WebGL perf hints, iframe-sandbox notices) are kept
    as `console` findings but are low severity — eyeball them; the actionable
    signal is js-exception / network / load-blocker / interact errors. A shape
    hitting >=3 apps is flagged systemic (likely a platform issue, not per-app).
    """
    from collections import Counter, defaultdict
    findings_path = OUT / "findings.jsonl"
    if not findings_path.exists():
        print("no findings file — run `walk` first"); return
    findings = [json.loads(l) for l in findings_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    real = [f for f in findings if _triage_is_real(f)]
    print(f"TOTAL findings: {len(findings)}   REAL (post-filter): {len(real)}\n")

    by_shape = defaultdict(set)
    for f in real:
        by_shape[f["category"] + " | " + f["message"][:90]].add(f["app"])
    print("=== CROSS-APP PATTERNS (msg shape -> #apps) ===")
    for key, apps in sorted(by_shape.items(), key=lambda x: -len(x[1])):
        flag = "  <== SYSTEMIC" if len(apps) >= 3 else ""
        print(f"[{len(apps):3d}] {key}{flag}")
        if len(apps) <= 6:
            print(f"       apps: {', '.join(sorted(apps))}")

    by_app = defaultdict(list)
    for f in real:
        by_app[f["app"]].append(f)
    print(f"\n=== PER-APP REAL FINDINGS ({len(by_app)} apps) ===")
    for app in sorted(by_app):
        print(f"\n## {app} ({len(by_app[app])})")
        for it in by_app[app]:
            print(f"  [{it.get('severity','?'):7s}] {it['category']}: {it['message'][:160]}")

    print("\n=== CATEGORY COUNTS (real) ===")
    for cat, n in Counter(f["category"] for f in real).most_common():
        print(f"  {cat}: {n}")


def cmd_report_html():
    """Render findings.jsonl + screenshots/ into a self-contained HTML report.

    Unlike `report` (markdown, text-only) this embeds each app's screenshot as
    base64 next to its findings, so it's a shareable visual artifact — open it in
    a browser, no server or loose image files needed. Apps are ordered worst-first
    (blocker > broken > polish > clean); clean apps collapse into a thumbnail strip.
    Reuses `_triage_is_real` so heuristic noise is de-emphasised, not hidden.
    """
    findings_path = OUT / "findings.jsonl"
    if not findings_path.exists():
        print("no findings file — run `walk` first"); return
    findings = [json.loads(l) for l in findings_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    apps = json.loads((OUT / "apps.json").read_text()) if (OUT / "apps.json").exists() else []
    app_name = {a["id"]: a.get("name", a["id"]) for a in apps}
    walked_ids = [a["id"] for a in apps] or sorted({f["app"] for f in findings})

    by_app: dict[str, list[dict]] = {}
    for f in findings:
        by_app.setdefault(f["app"], []).append(f)

    def emb(app_id):
        return embed_image_data_uri(SHOTS / f"{app_id}.png")

    sev_rank = {"blocker": 0, "broken": 1, "polish": 2}
    sev_color = {"blocker": "#e5484d", "broken": "#f5a623", "polish": "#3b82f6"}
    sev_icon = {"blocker": "🚨", "broken": "🔧", "polish": "✨"}

    def worst(app_id):
        items = by_app.get(app_id, [])
        real = [i for i in items if _triage_is_real(i)]
        if not real:
            return 9
        return min(sev_rank.get(i["severity"], 9) for i in real)

    totals = {"blocker": 0, "broken": 0, "polish": 0}
    for f in findings:
        if _triage_is_real(f) and f["severity"] in totals:
            totals[f["severity"]] += 1

    ordered = sorted(walked_ids, key=lambda a: (worst(a), a))
    flagged = [a for a in ordered if worst(a) < 9]
    clean = [a for a in ordered if worst(a) == 9]
    ts = time.strftime("%Y-%m-%d %H:%M")

    P = [f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>EmptyOS UI Walk</title>
<style>
:root{{--bg:#0e1117;--card:#161b22;--bd:#30363d;--fg:#e6edf3;--mut:#8b949e}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,'Segoe UI',sans-serif}}
.wrap{{max-width:1080px;margin:0 auto;padding:40px 24px 80px}}h1{{margin:0 0 4px}}
h2{{font-size:20px;margin:38px 0 14px;border-bottom:1px solid var(--bd);padding-bottom:8px}}
.sub{{color:var(--mut);margin:0 0 22px}}.bar{{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0}}
.pill{{background:var(--card);border:1px solid var(--bd);border-radius:8px;padding:12px 18px;text-align:center;flex:1;min-width:110px}}
.pill .n{{font-size:24px;font-weight:700}}.pill .l{{color:var(--mut);font-size:11px;text-transform:uppercase}}
.f{{background:var(--card);border:1px solid var(--bd);border-radius:10px;margin:16px 0;overflow:hidden}}
.fh{{display:flex;align-items:center;gap:10px;padding:13px 18px;border-bottom:1px solid var(--bd)}}
.badge{{font-size:11px;font-weight:700;padding:3px 9px;border-radius:20px;color:#fff}}
.fb{{display:grid;grid-template-columns:1fr 1fr}}.ft{{padding:14px 18px}}.fi{{padding:12px;background:#0d1117;border-left:1px solid var(--bd)}}
.fi img{{width:100%;border:1px solid var(--bd);border-radius:6px;display:block}}.fl{{margin:7px 0;font-size:14px}}
.cat{{color:var(--mut);font-size:12px}}
.gal{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}}
.gal .c{{background:var(--card);border:1px solid var(--bd);border-radius:8px;overflow:hidden}}
.gal .c img{{width:100%;display:block;border-bottom:1px solid var(--bd)}}.gal .c .cap{{padding:6px 10px;font-size:12px;color:var(--mut)}}
@media(max-width:720px){{.fb{{grid-template-columns:1fr}}}}
</style></head><body><div class="wrap">
<h1>EmptyOS — UI Walk</h1><p class="sub">{BASE} · {len(walked_ids)} apps walked · {ts}</p>
<div class="bar">
<div class="pill"><div class="n">{len(walked_ids)}</div><div class="l">Apps</div></div>
<div class="pill"><div class="n" style="color:#e5484d">{totals['blocker']}</div><div class="l">Blocker</div></div>
<div class="pill"><div class="n" style="color:#f5a623">{totals['broken']}</div><div class="l">Broken</div></div>
<div class="pill"><div class="n" style="color:#3b82f6">{totals['polish']}</div><div class="l">Polish</div></div>
<div class="pill"><div class="n" style="color:#2ea043">{len(clean)}</div><div class="l">Clean</div></div>
</div><h2>Flagged apps ({len(flagged)})</h2>"""]

    if not flagged:
        P.append('<p class="sub">No real findings after triage. Eyeball the gallery below.</p>')
    for app_id in flagged:
        items = sorted(by_app.get(app_id, []), key=lambda x: sev_rank.get(x["severity"], 9))
        top = "broken" if worst(app_id) == 1 else ("blocker" if worst(app_id) == 0 else "polish")
        src = emb(app_id)
        imgb = f'<img src="{src}">' if src else '<div style="color:var(--mut);padding:30px;text-align:center">no screenshot</div>'
        rows = "".join(
            f'<div class="fl"><span class="badge" style="background:{sev_color.get(i["severity"],"#666")}">'
            f'{sev_icon.get(i["severity"],"•")} {i["severity"].upper()}</span> '
            f'<span class="cat">{i["category"]}</span> — {i["message"]}</div>'
            for i in items if _triage_is_real(i)
        )
        P.append(f"""<div class="f"><div class="fh">
<span class="badge" style="background:{sev_color[top]}">{top.upper()}</span>
<b>{app_name.get(app_id, app_id)}</b><span class="cat">{app_id}</span></div>
<div class="fb"><div class="ft">{rows}</div><div class="fi">{imgb}</div></div></div>""")

    if clean:
        P.append(f'<h2>Clean apps ({len(clean)})</h2><div class="gal">')
        for app_id in clean:
            src = emb(app_id)
            if not src:
                continue
            P.append(f'<div class="c"><img src="{src}"><div class="cap">{app_name.get(app_id, app_id)}</div></div>')
        P.append('</div>')

    P.append(f'<p class="sub" style="margin-top:40px">Generated by <code>ui_walk_audit.py report-html</code> · {ts}</p></div></body></html>')
    out_path = OUT / "report.html"
    out_path.write_text("".join(P), encoding="utf-8")
    print(f"HTML report -> {out_path}  ({len(flagged)} flagged, {len(clean)} clean, {len(findings)} raw findings)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "probe": cmd_probe()
    elif cmd == "walk": cmd_walk(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "interact": cmd_interact(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "report": cmd_report()
    elif cmd == "report-html": cmd_report_html()
    elif cmd == "triage": cmd_triage()
    else: print(f"unknown: {cmd}"); sys.exit(1)
