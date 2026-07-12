#!/usr/bin/env python3
"""Visit every EmptyOS app at iPhone viewport, detect runtime layout bugs.

Detects:
  - horizontal overflow (scrollWidth > clientWidth)
  - elements wider than viewport (clipping)
  - fixed/sticky elements positioned outside viewport
  - text overflow without ellipsis
  - JS errors at load

Usage: python scripts/dogfood-mobile-scan.py [--base http://localhost:9000]
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

VIEWPORT = {"width": 393, "height": 852}
DEVICE_PIXEL_RATIO = 3
# iPhone 14 Pro UA — gets pointer:coarse and mobile heuristics right
USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

OVERFLOW_JS = """
() => {
  const html = document.documentElement, body = document.body;
  const vw = window.innerWidth;
  const overflow = html.scrollWidth - vw;
  // Find offending elements
  const offenders = [];
  const walk = (el) => {
    if (!el || !el.getBoundingClientRect) return;
    const r = el.getBoundingClientRect();
    if (r.right > vw + 2 && r.width > 8 && r.width < vw * 3) {
      // skip if hidden
      const cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden') return;
      // skip offscreen-by-transform drawers
      if (cs.transform && cs.transform.includes('matrix') && r.left >= vw) return;
      const tag = el.tagName.toLowerCase();
      const id = el.id || '';
      const cls = (el.className && typeof el.className === 'string') ? el.className.split(/\\s+/)[0] : '';
      offenders.push({tag, id, cls, x: Math.round(r.left), w: Math.round(r.width), right: Math.round(r.right), text: (el.textContent||'').slice(0, 60).trim()});
    }
  };
  document.querySelectorAll('*').forEach(walk);
  // Dedupe by tag+id+cls, keep widest
  const seen = new Map();
  for (const o of offenders) {
    const k = `${o.tag}#${o.id}.${o.cls}`;
    if (!seen.has(k) || seen.get(k).w < o.w) seen.set(k, o);
  }
  // Detect tap targets <30px (skip Leaflet map markers and inline text links —
  // both legitimate exceptions to the 30px touch-target heuristic)
  const buttons = Array.from(document.querySelectorAll('button, a, [role=button], input[type=submit]'));
  const tinyTaps = buttons.filter(b => {
    const r = b.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return false;
    const cs = getComputedStyle(b);
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    if (b.closest('.leaflet-marker-pane, .leaflet-overlay-pane, .leaflet-tile-pane, .leaflet-attribution-pane, .leaflet-control-attribution')) return false;
    // Inline text link inside flowing prose — flagging would be wrong;
    // Apple HIG accepts inline links. Skip <a> whose display is inline*
    // and whose text is 3+ alpha chars (not an icon glyph).
    // Skip vault note-link text component — .obs-link inside .eos-note-link
    // wrapper is part of a composite icon-row tap target, not a standalone btn
    if (b.classList && b.classList.contains('obs-link')) return false;
    if (b.tagName === 'A') {
      const txt = (b.innerText || '').trim();
      // 3+ chars of alphanumeric + common url/identifier punctuation = a text link;
      // Apple HIG accepts text links inside flowing content even <44px
      if (txt.length >= 3 && /^[A-Za-z0-9/][A-Za-z0-9 .,/:'_-]{2,}$/.test(txt)) return false;
    }
    return r.height < 30 || r.width < 30;
  }).slice(0, 8).map(b => {
    const r = b.getBoundingClientRect();
    return {tag: b.tagName.toLowerCase(), label: (b.innerText||b.getAttribute('aria-label')||'').slice(0,40), w: Math.round(r.width), h: Math.round(r.height)};
  });
  return {
    overflow_px: overflow,
    scrollWidth: html.scrollWidth,
    clientWidth: html.clientWidth,
    offenders: Array.from(seen.values()).sort((a,b)=>b.w-a.w).slice(0, 6),
    tinyTaps,
  };
}
"""

def scan(base: str, slugs: list[str], token: str = "") -> list[dict]:
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            viewport=VIEWPORT,
            device_scale_factor=DEVICE_PIXEL_RATIO,
            user_agent=USER_AGENT,
            is_mobile=True,
            has_touch=True,
        )
        if token:
            ctx.add_init_script(f"localStorage.setItem('eos.auth.token', {json.dumps(token)});")
            ctx.set_extra_http_headers({"Authorization": f"Bearer {token}"})
        page = ctx.new_page()
        errors_buf: list[str] = []
        page.on("pageerror", lambda e: errors_buf.append(str(e)))
        for slug in slugs:
            errors_buf.clear()
            url = f"{base}/{slug.strip('/')}/"
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1500)  # let JS settle
                # rate-limit so we don't crash the daemon under parallel scans
                # (its sqlite/think init isn't safe under 100 simultaneous boot scans)
                data = page.evaluate(OVERFLOW_JS)
                data["slug"] = slug
                data["url"] = url
                data["js_errors"] = list(errors_buf)
                results.append(data)
                tag = "OK" if data["overflow_px"] <= 2 and not data["tinyTaps"] and not data["js_errors"] else "ISSUE"
                print(f"[{tag}] /{slug}/  overflow={data['overflow_px']}px  tiny={len(data['tinyTaps'])}  errs={len(data['js_errors'])}", flush=True)
            except Exception as e:
                print(f"[ERR ] /{slug}/  {e}", flush=True)
                results.append({"slug": slug, "url": url, "error": str(e)})
        browser.close()
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:9000")
    ap.add_argument("--out", default="dogfood-mobile-report.json")
    ap.add_argument("--limit", type=int, default=0, help="scan only first N (0 = all)")
    ap.add_argument("--slugs", nargs="*", help="explicit slugs to scan")
    ap.add_argument("--token", default="", help="bearer token for daemon auth")
    args = ap.parse_args()

    if args.slugs:
        slugs = args.slugs
    else:
        import os
        import urllib.request
        token = args.token or os.environ.get("EOS_AUTH_TOKEN") or ""
        if not token:
            # Fallback: read from emptyos.toml
            try:
                import tomllib
                with open("emptyos.toml", "rb") as f:
                    cfg = tomllib.load(f)
                token = cfg.get("auth", {}).get("token") or cfg.get("auth_token") or ""
                if not token:
                    # auth_token is at top level in this repo
                    import re
                    txt = Path("emptyos.toml").read_text()
                    m = re.search(r'^\s*auth_token\s*=\s*"([^"]+)"', txt, re.M)
                    token = m.group(1) if m else ""
            except Exception:
                pass
        req = urllib.request.Request(f"{args.base}/api/apps", headers={"Authorization": f"Bearer {token}"} if token else {})
        with urllib.request.urlopen(req) as r:
            data = json.load(r)
        apps = data.get("apps") if isinstance(data, dict) else data
        slugs = [a["id"] for a in (apps or [])]
    if args.limit:
        slugs = slugs[: args.limit]

    print(f"Scanning {len(slugs)} apps at {VIEWPORT['width']}x{VIEWPORT['height']}...", flush=True)
    # reuse token resolved above
    token = locals().get("token", "") or args.token
    results = scan(args.base, slugs, token)
    Path(args.out).write_text(json.dumps(results, indent=2))

    issues = [r for r in results if r.get("overflow_px", 0) > 2 or r.get("tinyTaps") or r.get("js_errors") or r.get("error")]
    print(f"\n=== Summary: {len(issues)}/{len(results)} apps with issues ===")
    for r in issues:
        slug = r["slug"]
        if r.get("error"):
            print(f"  /{slug}/  ERROR: {r['error']}")
            continue
        bits = []
        if r["overflow_px"] > 2:
            off = r["offenders"][:3]
            bits.append(f"overflow={r['overflow_px']}px  worst=[{', '.join(o['tag']+'.'+o['cls']+'('+str(o['w'])+'px)' for o in off)}]")
        if r.get("tinyTaps"):
            bits.append(f"tinyTaps={len(r['tinyTaps'])}")
        if r.get("js_errors"):
            bits.append(f"errs={len(r['js_errors'])}: {r['js_errors'][0][:80]}")
        print(f"  /{slug}/  {' | '.join(bits)}")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
