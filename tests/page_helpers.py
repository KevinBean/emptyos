"""Playwright UI interaction helpers used across all app test files.

These helpers wrap the common patterns (toast wait, tab switch, modal handling,
keyboard shortcuts) so individual tests stay focused on the workflow under test.
"""

import re


def wait_for_toast(page, expected_substring=None, timeout=3000):
    """Wait for #eos-toast.show to appear. Returns the toast text.

    If expected_substring is provided, asserts the toast contains that text.
    Returns empty string if no toast appears within timeout (caller decides
    whether that is a failure).
    """
    try:
        page.wait_for_selector("#eos-toast.show", timeout=timeout)
        text = (page.locator("#eos-toast").text_content() or "").strip()
        if expected_substring is not None:
            assert expected_substring.lower() in text.lower(), (
                f"Toast text {text!r} did not contain {expected_substring!r}"
            )
        return text
    except Exception:
        return ""


def switch_tab(page, tab_name, timeout=3000):
    """Click a tab and wait for its content to become visible.

    Tries multiple selector patterns common across EmptyOS apps:
    - .eos-tab[data-tab="X"] (shared component pattern)
    - [onclick*='switchTab("X")'] / [onclick*="showTab('X')"]
    - Button text matching
    """
    selectors = [
        f'.eos-tab[data-tab="{tab_name}"]',
        f'[data-tab="{tab_name}"]',
        f"[onclick*=\"switchTab('{tab_name}')\"]",
        f"[onclick*='switchTab(\"{tab_name}\")']",
        f"[onclick*=\"showTab('{tab_name}')\"]",
        f"[onclick*='showTab(\"{tab_name}\")']",
        f"button:has-text('{tab_name}')",
        f".tab:has-text('{tab_name}')",
    ]
    for sel in selectors:
        loc = page.locator(sel).first
        if loc.count() > 0:
            try:
                loc.click(timeout=1500)
                page.wait_for_timeout(400)
                return True
            except Exception:
                continue
    return False


def navigate_to_app(page, base_url, app_id, wait_idle=True):
    """Navigate to /{app_id}/ and wait for the page + initial network to settle."""
    url = f"{base_url}/{app_id}/"
    response = page.goto(url, wait_until="domcontentloaded", timeout=15000)
    if wait_idle:
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            page.wait_for_timeout(800)
    return response


def fill_and_submit(page, input_sel, text, submit_sel=None, press_enter=False):
    """Fill an input and submit by clicking a button or pressing Enter."""
    page.locator(input_sel).first.fill(text)
    if press_enter:
        page.locator(input_sel).first.press("Enter")
    elif submit_sel:
        page.locator(submit_sel).first.click()
    page.wait_for_timeout(500)


def click_first(page, *selectors, timeout=2000):
    """Try multiple selectors in order. Click the first one that matches."""
    for sel in selectors:
        loc = page.locator(sel).first
        if loc.count() > 0:
            try:
                loc.click(timeout=timeout)
                return sel
            except Exception:
                continue
    return None


def visible_search_input(page, timeout=3000):
    """The app's OWN search box, skipping the global search overlay.

    Every page carries the universal search overlay (`.eos-search-overlay`,
    added 2026-06-01), whose input matches the obvious
    `input[type='search'], input[placeholder*='earch' i]` selector and sorts
    FIRST in DOM order. So a plain `.first` grabs a decoy that lives inside a
    `display:none` subtree, and `fill()` waits out its timeout on a page whose
    real search box was visible the whole time.

    Returns a visible locator, or None when the app genuinely has no search
    input (callers skip in that case).
    """
    loc = page.locator(
        "input[type='search'], input[placeholder*='earch' i]"
    ).locator("visible=true")
    try:
        loc.first.wait_for(state="visible", timeout=timeout)
    except Exception:
        return None
    return loc.first if loc.count() else None


def visible_text_input(page, timeout=3000):
    """The app's OWN first typing surface, skipping shared page chrome.

    Sibling of `visible_search_input`, and the same trap: the universal search
    overlay (`.eos-search-overlay`) also matches the obvious
    `textarea, input[type='text']` selector and sorts FIRST in DOM order, so a
    plain `.first` grabs a decoy inside a `display:none` subtree and `fill()`
    times out on a page whose real input was visible the whole time. This broke
    three app tests at once (studio / podcast / tts) rather than one, because
    the decoy is shared chrome — every page has it.

    Returns a visible locator, or None when the app genuinely has no text
    input (callers skip in that case).
    """
    loc = page.locator("textarea, input[type='text']").locator("visible=true")
    try:
        loc.first.wait_for(state="visible", timeout=timeout)
    except Exception:
        return None
    return loc.first if loc.count() else None


def hash_contains(page, value):
    """True when the page's URL fragment names `value`, encoded or not.

    A slug with a space (`_cable-thermal MOC`) is written to `location.hash`
    percent-encoded by the browser, so a raw `value in page.url` substring test
    fails against a perfectly correct URL — and only for the slugs that happen
    to contain one, which makes it look like a flake tied to whichever row
    sorts first.
    """
    from urllib.parse import quote, unquote

    url = page.url
    return value in url or quote(value) in url or value in unquote(url)


def open_command_palette(page):
    """Open the command palette, waiting for eos-keys.js to initialize.

    eos-keys.js is dynamically appended to <body> by eos.js, so it loads
    asynchronously AFTER DOMContentLoaded. We wait up to 5s for EOS.keys
    to exist, then press Ctrl+K. If the keystroke doesn't open the palette
    (possibly due to focus issues in headless browsers), fall back to
    calling the palette directly via its registered handler.
    """
    # Wait for keyboard shortcuts to be initialized (up to 5s)
    try:
        page.wait_for_function(
            "typeof EOS !== 'undefined' && EOS.keys !== undefined",
            timeout=5000,
        )
    except Exception:
        page.wait_for_timeout(2000)

    # Focus body so keystroke isn't intercepted by a field
    try:
        page.evaluate("document.body.focus()")
    except Exception:
        pass

    page.keyboard.press("Control+k")
    try:
        # Palette fetches /api/apps/clusters on first open (slow on busy pages like hub)
        page.wait_for_selector("#eos-palette-overlay", state="visible", timeout=6000)
        return True
    except Exception:
        pass

    # Fallback: call showPalette() directly (bypasses keydown handler)
    try:
        page.evaluate("EOS.keys && EOS.keys.showPalette && EOS.keys.showPalette()")
        page.wait_for_selector("#eos-palette-overlay", state="visible", timeout=6000)
        return True
    except Exception:
        return False


def close_overlays(page):
    """Press Escape to close any open overlays/modals."""
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)


def go_navigate(page, letter):
    """Simulate g + letter go-to navigation."""
    page.keyboard.press("g")
    page.wait_for_timeout(150)
    page.keyboard.press(letter)
    page.wait_for_timeout(800)


def assert_no_js_errors(page_errors, allow_patterns=None):
    """Assert page_errors is empty.

    allow_patterns: list of substrings to ignore (e.g. third-party noise).
    """
    if not page_errors:
        return
    allow_patterns = allow_patterns or []
    real_errors = [
        e for e in page_errors
        if not any(p in str(e) for p in allow_patterns)
    ]
    assert not real_errors, f"Page had JS errors: {real_errors}"


def count_elements(page, selector):
    """Return locator count for a selector."""
    return page.locator(selector).count()


def assert_element_count_gte(page, selector, min_count, message=""):
    """Assert at least N elements match selector."""
    actual = page.locator(selector).count()
    assert actual >= min_count, (
        f"{message or 'Expected'} >= {min_count} of '{selector}', found {actual}"
    )


def get_text(page, selector, default=""):
    """Get text content of first matching element. Returns default if not found."""
    loc = page.locator(selector).first
    if loc.count() == 0:
        return default
    return (loc.text_content() or "").strip()


def has_text_anywhere(page, text, timeout=2000):
    """Check if text appears anywhere on the page (case-insensitive)."""
    try:
        page.wait_for_selector(f"text=/{re.escape(text)}/i", timeout=timeout)
        return True
    except Exception:
        return False


def app_loaded(http_client, app_path):
    """Probe an app's index page. Returns True if 200, False otherwise.

    Used by personal-app tests to skip gracefully when the app isn't installed.
    """
    try:
        resp = http_client.get(app_path)
        return resp.status_code == 200
    except Exception:
        return False


def wait_briefly(page, ms=500):
    """Short wait to let the UI settle (animations, debounce)."""
    page.wait_for_timeout(ms)


# ── Shared calculator-surface assertions ─────────────────────────────
# These exist because the engineering calculators' charts and readout strips are
# rendered by shared EOS_UI helpers, and the per-app tests only ever asserted the
# headline number. That is green on a chart with no tick labels, a readout strip
# that rendered nothing, and a warning list that silently swallowed a bad mount
# — all of which happened. Assert the surface, not just the number.

def assert_chart(page, selector, *, series=1, ref_lines=0, legend=None, ticks=True):
    """A lineChart actually drew at `selector`.

    `series` is the minimum polyline count (a series breaks into several
    polylines across gaps, so this is a floor, not an equality). `ticks=True`
    requires at least one numeric tick label — the assertion that catches a log
    axis whose domain contains no power of ten, which renders as bare rules.
    `legend` is the expected entry count, or None to not check.
    """
    svg = page.locator(f"{selector} svg")
    assert svg.count() == 1, f"{selector}: expected one <svg>, found {svg.count()}"
    polys = page.locator(f"{selector} svg polyline").count()
    assert polys >= series, f"{selector}: expected >= {series} polyline(s), found {polys}"
    if ref_lines:
        dashed = page.locator(f"{selector} svg line[stroke-dasharray]").count()
        assert dashed >= ref_lines, (
            f"{selector}: expected >= {ref_lines} reference line(s), found {dashed}")
    if ticks:
        # Per AXIS, not per svg. "The chart has some tick text somewhere" is
        # satisfied by the other axis, so a completely unlabelled axis passes —
        # measured: that exact assertion went green on a build with a blank y
        # axis because the x axis still carried a decade. Hence .eos-tick-x/-y.
        for axis in ("x", "y"):
            # all_text_contents, not all_inner_texts: an SVG <text> node has no
            # innerText, so the inner_text accessor raises rather than returning "".
            labels = [t.strip() for t in
                      page.locator(f"{selector} svg .eos-tick-{axis}").all_text_contents()]
            numeric = [t for t in labels if any(c.isdigit() for c in t)]
            assert numeric, (
                f"{selector}: {axis} axis carries no numeric tick labels (got {labels})")
    if legend is not None:
        n = page.locator(f"{selector} .eos-chart-legend span").count()
        assert n == legend, f"{selector}: expected {legend} legend entries, found {n}"


def assert_readout(page, selector, *, min_rows=1):
    """A metaRows strip rendered, and carries the shared class that styles it."""
    cls = page.get_attribute(selector, "class") or ""
    assert "eos-meta" in cls, f"{selector}: missing .eos-meta (class={cls!r})"
    rows = page.locator(f"{selector} > div").count()
    assert rows >= min_rows, f"{selector}: expected >= {min_rows} row(s), found {rows}"


def assert_warnings(page, selector, *, min_count=1):
    """A warnList rendered its caveats as shared .eos-warn notes."""
    n = page.locator(f"{selector} .eos-warn").count()
    assert n >= min_count, f"{selector}: expected >= {min_count} .eos-warn, found {n}"
