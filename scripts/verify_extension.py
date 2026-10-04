"""verify_extension.py — load a built MV3 extension in Chromium and assert it works.

The graduated form of the ad-hoc verification hand-run during the
standalone-distribution rounds (.claude/rules/audits.md: an audit only
compounds when it reruns). Checks, under the extension's real CSP:

- the entry page renders and the offline pill mounts (single-app), or the
  chooser cards render and a member opens in group mode (--member)
- eos-csp-bridge is ACTIVE (CSP actually neuters inline handlers and the
  bridge is loaded — inline onclick= UI works through it)
- an IndexedDB write via the export shim survives a page reload
- zero non-CSP-report console errors and zero failed requests
  (Chrome logs a CSP violation *report* per neutered inline attribute
  before the bridge dispatches — expected noise, filtered)

Usage:
    python scripts/verify_extension.py <extension_dir>              # single-app
    python scripts/verify_extension.py <extension_dir> --member boards   # group

Invoked automatically by scripts/build_standalone_release.py after building
an extension target (skippable via its --skip-verify). Exit 0 = pass;
exit 1 = failures (listed). Playwright missing → warning + exit 0
(advisory, mirroring the single-html verify fallback).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path


async def _verify(ext_dir: Path, member: str) -> list[str]:
    from playwright.async_api import async_playwright

    problems: list[str] = []
    async with async_playwright() as pw:
        profile = Path(tempfile.mkdtemp(prefix="eos-extv-"))
        args = [f"--disable-extensions-except={ext_dir}", f"--load-extension={ext_dir}"]
        try:
            # New headless supports extensions via the chromium channel.
            ctx = await pw.chromium.launch_persistent_context(
                str(profile), headless=True, channel="chromium", args=args
            )
        except Exception:
            ctx = await pw.chromium.launch_persistent_context(
                str(profile), headless=False, args=args
            )
        try:
            sw = ctx.service_workers
            if not sw:
                sw = [await ctx.wait_for_event("serviceworker", timeout=10000)]
            ext_id = sw[0].url.split("/")[2]

            page = await ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.on(
                "console",
                lambda m: errors.append(f"console: {m.text}")
                if m.type == "error" and "Content Security Policy" not in m.text
                else None,
            )
            page.on("requestfailed", lambda r: errors.append(f"requestfailed: {r.url}"))

            await page.goto(f"chrome-extension://{ext_id}/app/index.html")
            await page.wait_for_timeout(2000)

            checks: dict[str, bool] = {}
            if member:
                checks["chooser cards render"] = await page.evaluate(
                    "document.querySelectorAll('.card').length >= 1"
                )
                await page.click(f'a[href="{member}/index.html"]')
                await page.wait_for_timeout(2500)
                checks["member page loads"] = member in page.url
                checks["group mode"] = await page.evaluate(
                    "window.EOS_EXPORT_MODE === 'group'"
                )
            else:
                checks["offline pill mounts"] = await page.evaluate(
                    "!!document.getElementById('eos-export-pill')"
                )
            checks["bridge active"] = await page.evaluate(
                "(function(){var t=document.createElement('div');"
                "t.setAttribute('onclick','void 0');"
                "return typeof t.onclick !== 'function' && !!window.EOS_CSP_BRIDGE})()"
            )
            checks["idb write"] = await page.evaluate(
                "(async () => { await window.EOS_EXPORT.set('/verify/key', {a: 1}); return true })()"
            )
            await page.reload()
            await page.wait_for_timeout(1500)
            checks["idb survives reload"] = await page.evaluate(
                "(async () => { const v = await window.EOS_EXPORT.get('/verify/key'); "
                "return !!(v && v.a === 1) })()"
            )
        finally:
            await ctx.close()

    problems += [f"check failed: {k}" for k, v in checks.items() if not v]
    problems += [e for e in errors if "favicon" not in e.lower()]
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("ext_dir", help="built extension directory (manifest.json + app/)")
    ap.add_argument("--member", default="", help="group bundles: member app id to click into")
    ns = ap.parse_args()

    ext_dir = Path(ns.ext_dir).resolve()
    for rel in ("manifest.json", "background.js", "app/index.html"):
        if not (ext_dir / rel).exists():
            print(f"verify_extension: missing {rel} in {ext_dir}")
            return 1
    try:
        import playwright  # noqa: F401
    except ImportError:
        print("verify_extension: playwright not installed — skipping (advisory)")
        return 0

    problems = asyncio.run(_verify(ext_dir, ns.member))
    if problems:
        print(f"verify_extension: {len(problems)} problem(s) in {ext_dir.name}:")
        for p in problems:
            print("  •", p[:160])
        return 1
    print(f"verify_extension: ALL-PASS ({ext_dir.name}{' via ' + ns.member if ns.member else ''})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
