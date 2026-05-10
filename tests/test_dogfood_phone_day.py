"""
Dogfood phone day — long Playwright walkthrough at iPhone 14 Pro viewport.

Discovers every loaded app at runtime via `/api/apps`, walks them in sequence
at iPhone width (393×852), screenshots each, and accumulates issues that only
emerge during multi-step navigation across the system.

This is a complementary test, not a replacement:

    What this catches               | Owner
    --------------------------------|----------------------------------------
    Layout overflow during nav      | THIS test
    Console errors during a session | THIS test
    Network 5xx during a session    | THIS test
    Uncaught page errors            | THIS test
    The 5 iOS bug families (NHPVT)  | scripts/check-ios-safe-area.py (static)
    Tap-target sizing               | scripts/check-ios-safe-area.py (static)
    Hover-only affordances          | /eos-design-review (per-page)
    Theme bootstrap missing         | /eos-design-review (per-page)
    Single-page mobile rendering    | tests/test_sys_ios_layout.py (per-page)

Run:
    python -m pytest tests/test_dogfood_phone_day.py -v --reruns 2

Or via the dogfood CI workflow (auto-runs nightly + on every push to main):
    pytest -m "dogfood and not llm"

Output:
    tests/_dogfood-phone/<timestamp>/
        report.txt              — issue summary by category, grep-friendly
        <app-id>-load.png       — screenshot after page load
        <app-id>-after.png      — screenshot after primary-action click (when applicable)

The report file is the primary artifact. Each line is one issue. Diff between
runs to spot regressions; counts per category form a regression-tracking metric.

Time budget: ~60-90s for ~60 apps. Long enough that this test should NOT run
on every commit (mark = dogfood, off by default).
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

# iPhone 14 Pro logical viewport — Dynamic Island device, worst-case top inset
IPHONE_VIEWPORT = {"width": 393, "height": 852}

# Per-app primary-action selectors — when we know the canonical first interaction
# for an app, we click it after page load to surface state-transition bugs.
# Apps not listed get load-only treatment (still screenshotted, still asserts).
# Selectors are best-effort; missing/invisible elements are silently skipped
# (the page navigation step still happens).
PRIMARY_ACTIONS: dict[str, str] = {
    "quick-action":   "input[type='text']",
    "search":         "input[type='text']",
    "task":           ".eos-tab[data-tab='list']",
    "radio":          "#btn-play",
    "projects":       "#btn-list",
    # "people":         "#search",  # has search input, similar to others
    # Add more as they prove valuable.
}

# Apps that don't have a meaningful page UI (services, plugins, etc.) — skip them.
# Auto-detected via empty `web_prefix` from /api/apps, so this list is empty
# unless we discover specific apps that crash at iPhone width and are unfixable.
KNOWN_SKIP: set[str] = set()


def _slug(s: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in s).strip("-").lower()


@pytest.mark.dogfood
@pytest.mark.interactive
def test_phone_day_walkthrough(page, base_url, http_client):
    """One long iPhone-viewport Playwright session walking every loaded app.

    Asserts each visited page has zero layout overflow, zero console errors,
    zero 5xx responses, zero uncaught page errors. Captures a screenshot at
    each step and writes a single report.txt summarising every issue found.
    """
    page.set_viewport_size(IPHONE_VIEWPORT)

    # ── Discover loaded apps at test time ─────────────────────────────
    # Self-adapting to whatever's installed in the daemon being tested.
    apps_resp = http_client.get("/api/apps")
    assert apps_resp.status_code == 200, f"GET /api/apps failed: {apps_resp.status_code}"
    apps = apps_resp.json()
    visitable = [
        a for a in apps
        if a.get("web_prefix")
        and a.get("state") in ("loaded", "ready", "running")
        and a["id"] not in KNOWN_SKIP
    ]
    assert visitable, "No visitable apps found — daemon may not have loaded any"

    # Sort for deterministic output ordering — alphabetic by id
    visitable.sort(key=lambda a: a["id"])

    # ── Output dir ────────────────────────────────────────────────────
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(__file__).parent / "_dogfood-phone" / timestamp
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Issue accumulators (mutated by event handlers) ────────────────
    current_app: dict[str, str] = {"id": "init"}
    issues: dict[str, list] = {
        "layout": [],
        "console": [],
        "network": [],
        "page_error": [],
        "nav_failure": [],
    }

    def on_console(msg):
        if msg.type == "error":
            text = msg.text or ""
            # Skip well-known noisy errors that aren't ours:
            if "mce-autosize-textarea" in text:  # browser extensions
                return
            if "favicon.ico" in text:
                return
            issues["console"].append({"app": current_app["id"], "text": text[:200]})

    def on_response(resp):
        if resp.status >= 500:
            issues["network"].append({
                "app": current_app["id"], "url": resp.url, "status": resp.status
            })

    def on_page_error(exc):
        issues["page_error"].append({"app": current_app["id"], "error": str(exc)[:200]})

    page.on("console", on_console)
    page.on("response", on_response)
    page.on("pageerror", on_page_error)

    # ── The walk ──────────────────────────────────────────────────────
    for app in visitable:
        app_id = app["id"]
        prefix = app["web_prefix"]
        path = prefix.rstrip("/") + "/"
        current_app["id"] = app_id

        try:
            page.goto(f"{base_url}{path}", wait_until="domcontentloaded", timeout=15000)
            try:
                page.wait_for_load_state("networkidle", timeout=4000)
            except Exception:
                page.wait_for_timeout(500)

            # Layout assertion — no horizontal overflow at iPhone width.
            # 1px slop allowed for sub-pixel borders / scrollbar artifacts.
            scroll_width = page.evaluate("document.documentElement.scrollWidth")
            client_width = page.evaluate("document.documentElement.clientWidth")
            if scroll_width > client_width + 1:
                issues["layout"].append({
                    "app": app_id,
                    "scroll": scroll_width,
                    "client": client_width,
                    "overflow": scroll_width - client_width,
                })

            page.screenshot(path=str(out_dir / f"{_slug(app_id)}-load.png"), full_page=False)

            # Optional primary-action interaction — surfaces state-transition bugs
            # (e.g. modal opens but its content overflows the viewport).
            click_selector = PRIMARY_ACTIONS.get(app_id)
            if click_selector:
                try:
                    el = page.locator(click_selector).first
                    if el.count() > 0 and el.is_visible(timeout=1000):
                        el.click(timeout=2000)
                        page.wait_for_timeout(500)
                        # Re-check overflow after the interaction
                        sw2 = page.evaluate("document.documentElement.scrollWidth")
                        cw2 = page.evaluate("document.documentElement.clientWidth")
                        if sw2 > cw2 + 1:
                            issues["layout"].append({
                                "app": f"{app_id} (post-action)",
                                "scroll": sw2, "client": cw2,
                                "overflow": sw2 - cw2,
                            })
                        page.screenshot(
                            path=str(out_dir / f"{_slug(app_id)}-after.png"),
                            full_page=False,
                        )
                except Exception as click_err:
                    # Click failure is recorded but doesn't abort the walk
                    issues["nav_failure"].append({
                        "app": app_id, "stage": "primary-action click",
                        "error": str(click_err)[:200],
                    })

        except Exception as nav_err:
            issues["nav_failure"].append({
                "app": app_id, "stage": "navigation", "error": str(nav_err)[:200],
            })

    # ── Report ────────────────────────────────────────────────────────
    lines: list[str] = [
        f"Dogfood phone day walkthrough — {timestamp}",
        f"Viewport: {IPHONE_VIEWPORT['width']}×{IPHONE_VIEWPORT['height']} (iPhone 14 Pro)",
        f"Apps visited: {len(visitable)}",
        f"Apps with primary-action click: "
        f"{sum(1 for a in visitable if a['id'] in PRIMARY_ACTIONS)}",
        "=" * 70,
        "",
    ]
    counts = {k: len(v) for k, v in issues.items()}
    total = sum(counts.values())
    lines.append(f"Total issues: {total}")
    for cat in ("layout", "console", "network", "page_error", "nav_failure"):
        lines.append(f"  {cat}: {counts[cat]}")
    lines.append("")

    for cat in ("layout", "console", "network", "page_error", "nav_failure"):
        if not issues[cat]:
            continue
        lines.append(f"[{cat.upper()}] {counts[cat]}")
        for item in issues[cat]:
            lines.append(f"  {json.dumps(item, ensure_ascii=False)}")
        lines.append("")

    report = out_dir / "report.txt"
    report.write_text("\n".join(lines), encoding="utf-8")

    # ── Single aggregate assert ───────────────────────────────────────
    # The whole walk runs even on first failure — this assertion fires after
    # everything is collected, so the report.txt always reflects the full run.
    if total > 0:
        report_body = report.read_text(encoding="utf-8")
        pytest.fail(
            f"\n{report_body}\n"
            f"Full report + screenshots: {out_dir}"
        )
