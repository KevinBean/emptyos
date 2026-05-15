"""System tests: iOS Safari notch + safe-area + tap-target invariants.

Two layers of coverage, complementary to scripts/check-ios-safe-area.py:

1. **Served-CSS assertions** (api): pull stylesheets from the running daemon
   and grep for the safe-area-inset env() formulas on critical surfaces.
   The static scanner checks the source; this checks what the browser
   actually receives — guards against a future build step that strips env().

2. **iPhone-viewport rendering** (interactive): re-render key pages at iPhone
   14 Pro dimensions (393×852) under the existing pytest-playwright session
   and assert no horizontal overflow, key buttons in viewport, primary fixed
   bars positioned correctly.

Limitation: Playwright doesn't natively simulate iOS safe-area-inset-* env()
values — they resolve to 0 even with viewport-fit=cover. So the rendered
tests catch overflow + viewport-clipping but not "is this hidden behind the
notch on a real iPhone" — that's what the CSS-source assertions are for.
A real device matrix (.claude/rules/testing.md) remains the final word.
"""

from __future__ import annotations

import re

import pytest

# iPhone 14 Pro logical dimensions (Dynamic Island device — worst case for
# top inset; insets ~59pt). Most other notched iPhones have inset 47pt or less.
IPHONE_VIEWPORT = {"width": 393, "height": 852}


# ── Layer 1: served-CSS invariants ─────────────────────────────────────────────


@pytest.mark.api
class TestServedCssHasSafeArea:
    """Hits the live daemon's static endpoint and verifies the env() formulas
    survive whatever the daemon serves (raw, minified, bundled). Mirrors what
    a real iPhone would download."""

    def _get_rule(self, css: str, selector_substr: str) -> str | None:
        # Find the first {...} block whose selector contains the substring.
        for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
            if selector_substr in m.group(1):
                return m.group(2)
        return None

    def test_eos_toast_safe_area_top(self, http_client):
        css = http_client.get("/static/eos-components.css").text
        body = self._get_rule(css, ".eos-toast")
        assert body, ".eos-toast rule not found in served CSS"
        assert "safe-area-inset-top" in body, (
            ".eos-toast must include env(safe-area-inset-top) — toasts hide under notch otherwise"
        )

    def test_eos_modal_safe_area_bottom(self, http_client):
        # The bottom-sheet modal has actions at the foot — must clear the home indicator.
        css = http_client.get("/static/eos-components.css").text
        body = self._get_rule(css, ".eos-modal ")
        assert body, ".eos-modal rule not found in served CSS"
        assert "safe-area-inset-bottom" in body, (
            ".eos-modal must include env(safe-area-inset-bottom) — action buttons sit under home indicator"
        )

    @pytest.mark.parametrize(
        "selector",
        [
            ".eos-modal-bg",
            ".eos-modal-overlay",
            ".eos-note-overlay",
            ".eos-slide-panel-backdrop",
        ],
    )
    def test_backdrop_has_pointer_cursor(self, http_client, selector):
        # iOS Safari only fires onclick on plain divs that have cursor:pointer.
        # Every shared backdrop with tap-to-close needs this.
        css = http_client.get("/static/eos-components.css").text
        body = self._get_rule(css, selector)
        assert body, f"{selector} rule not found in served CSS"
        assert re.search(r"cursor\s*:\s*pointer", body), (
            f"{selector} must declare cursor:pointer or iOS Safari drops the tap"
        )

    def test_app_drawer_overlay_has_pointer_cursor(self, http_client):
        css = http_client.get("/static/theme.css").text
        body = self._get_rule(css, ".app-drawer-overlay")
        assert body, ".app-drawer-overlay rule not found in theme.css"
        assert re.search(r"cursor\s*:\s*pointer", body), (
            ".app-drawer-overlay (the global launcher scrim) must declare cursor:pointer"
        )


# ── Layer 2: iPhone-viewport rendering ─────────────────────────────────────────


@pytest.mark.interactive
class TestIPhoneViewport:
    """Render a handful of high-traffic pages at iPhone 14 Pro dimensions and
    assert the layout doesn't break in obvious ways. Doesn't simulate the
    notch — see module docstring."""

    PAGES = [
        ("/", "home"),
        ("/task/", "task"),
        ("/journal/", "journal"),
        ("/boards/", "boards"),
        ("/agent/", "agent"),
    ]

    @pytest.fixture(autouse=True)
    def _iphone(self, page):
        page.set_viewport_size(IPHONE_VIEWPORT)
        yield

    @pytest.mark.parametrize("path,name", PAGES, ids=[p[1] for p in PAGES])
    def test_no_horizontal_overflow(self, page, base_url, path, name):
        page.goto(f"{base_url}{path}", wait_until="domcontentloaded", timeout=15000)
        try:
            page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            page.wait_for_timeout(500)
        scroll_width = page.evaluate("document.documentElement.scrollWidth")
        client_width = page.evaluate("document.documentElement.clientWidth")
        # Allow 1px of slop for sub-pixel borders/scrollbars.
        assert scroll_width <= client_width + 1, (
            f"{name}: horizontal overflow at iPhone width — scrollWidth={scroll_width}, "
            f"clientWidth={client_width}. Mobile users will see a sideways scrollbar."
        )

    def test_global_app_drawer_dismisses_via_overlay_tap(self, page, base_url):
        page.goto(f"{base_url}/", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(500)
        # Open the global "All Apps" drawer programmatically — the trigger
        # button isn't always rendered the same way across pages.
        drawer_exists = page.evaluate(
            "() => typeof EOS !== 'undefined' && typeof EOS.toggleDrawer === 'function'"
        )
        if not drawer_exists:
            pytest.skip("EOS.toggleDrawer not available on this build")
        page.evaluate("EOS.toggleDrawer(true)")
        page.wait_for_selector("#app-drawer-overlay.open", timeout=2000)
        # Click the overlay; verify drawer closes. This exercises the iOS
        # cursor:pointer requirement on the overlay class.
        page.click("#app-drawer-overlay")
        page.wait_for_selector("#app-drawer-overlay.open", state="detached", timeout=2000)

    def test_eos_toast_renders_below_simulated_notch(self, page, base_url):
        # We can't make Playwright resolve env(safe-area-inset-top) to a real
        # number, but we *can* assert the toast's CSS rule includes safe-area-inset-top
        # at runtime. Keeps this test relevant if the served CSS ever drifts
        # from what Layer 1 saw.
        page.goto(f"{base_url}/", wait_until="domcontentloaded", timeout=15000)
        rule = page.evaluate("""() => {
            for (const sheet of document.styleSheets) {
                try {
                    for (const rule of sheet.cssRules || []) {
                        if (rule.selectorText && rule.selectorText.includes('.eos-toast')
                            && !rule.selectorText.includes('.eos-toast-')
                            && !rule.selectorText.includes('.eos-toast.')) {
                            return rule.cssText;
                        }
                    }
                } catch (e) { /* CORS-blocked external sheet */ }
            }
            return null;
        }""")
        assert rule, ".eos-toast rule not reachable from runtime CSSOM"
        assert "safe-area-inset-top" in rule, (
            "Runtime .eos-toast missing env(safe-area-inset-top) — daemon may be serving stale CSS"
        )


# ── Layer 3: viewport meta enforcement ─────────────────────────────────────────


@pytest.mark.api
class TestViewportMetaInjection:
    """The server's ViewportMiddleware injects/upgrades viewport-fit=cover into
    every HTML response. Without it, iOS resolves env(safe-area-inset-*) to 0
    and every safe-area rule downstream is dead code. These tests verify a
    cross-section of routes ship the upgraded meta so individual apps can't
    regress the invariant by hand-writing a viewport tag."""

    @pytest.mark.parametrize(
        "path",
        [
            "/",
            "/task/",
            "/journal/",
            "/boards/",
            "/expense/",
            "/rooms/",
            "/assistant/",
            "/tiers/",
        ],
    )
    def test_response_has_viewport_fit_cover(self, http_client, path):
        r = http_client.get(path)
        assert r.status_code == 200, f"{path} returned {r.status_code}"
        ctype = r.headers.get("content-type", "")
        if "text/html" not in ctype.lower():
            pytest.skip(f"{path} is not HTML ({ctype})")
        body = r.text
        m = re.search(
            r'<meta[^>]*\bname=["\']viewport["\'][^>]*\bcontent=["\']([^"\']*)["\']',
            body,
            re.IGNORECASE,
        )
        assert m, f"{path} response has no <meta name='viewport'> — middleware should have injected one"
        assert "viewport-fit=cover" in m.group(1).lower(), (
            f"{path} viewport meta missing viewport-fit=cover: {m.group(1)!r}"
        )
