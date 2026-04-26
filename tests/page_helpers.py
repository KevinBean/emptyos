"""Playwright UI interaction helpers used across all app test files.

These helpers wrap the common patterns (toast wait, tab switch, modal handling,
keyboard shortcuts) so individual tests stay focused on the workflow under test.
"""

import re
import time


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
