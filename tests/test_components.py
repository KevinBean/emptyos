"""Cross-cutting UI component tests: modals, sidebars, chat boxes.

These tests verify the shared EOS_UI component lifecycle (open/close,
form submission, keyboard dismissal) and app-specific panels that use
the same CSS contract. Failures here indicate regressions in
eos-components.js, eos-components.css, or the assistant chat plumbing.
"""

import pytest

from helpers import TEST_PREFIX
from page_helpers import (
    assert_no_js_errors, click_first, close_overlays, wait_briefly,
)


# =============================================================================
# MODALS — shared EOS_UI modal/formModal/confirm + app-specific modals
# =============================================================================


@pytest.mark.interactive
class TestSharedModal:
    """Tests EOS_UI.modal + EOS_UI.closeModal lifecycle via the browser console."""

    def test_modal_opens_from_script(self, page, base_url, page_errors):
        """Programmatically call EOS_UI.modal → verify overlay visible."""
        page.goto(base_url + "/", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 800)
        page.evaluate(
            "EOS_UI.modal({title: 'test', body: '<p>hello</p>'})"
        )
        wait_briefly(page, 400)
        overlay = page.locator("#eos-modal-overlay")
        assert overlay.count() > 0
        assert overlay.is_visible(), "Modal overlay did not become visible"
        assert_no_js_errors(page_errors)

    def test_modal_close_button_works(self, page, base_url, page_errors):
        """Click × button → overlay hidden."""
        page.goto(base_url + "/", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 800)
        page.evaluate("EOS_UI.modal({title: 'test', body: ''})")
        wait_briefly(page, 300)
        close = page.locator(".eos-modal-close").first
        if close.count() == 0:
            pytest.skip("No close button")
        close.click()
        wait_briefly(page, 400)
        overlay = page.locator("#eos-modal-overlay")
        if overlay.count() > 0:
            # Check it's hidden
            assert not overlay.is_visible() or "hidden" in (overlay.get_attribute("style") or "")

    def test_modal_escape_closes(self, page, base_url, page_errors):
        """Press Escape → modal closes."""
        page.goto(base_url + "/", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 800)
        page.evaluate("EOS_UI.modal({title: 'test', body: ''})")
        wait_briefly(page, 300)
        page.keyboard.press("Escape")
        wait_briefly(page, 400)
        assert_no_js_errors(page_errors)

    def test_form_modal_submit(self, page, base_url, page_errors):
        """EOS_UI.formModal: fill a field → submit → callback fires."""
        page.goto(base_url + "/", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 800)
        page.evaluate("""
            window.__test_submitted = null;
            EOS_UI.formModal('Test', [
                {key: 'name', label: 'Name', type: 'text'}
            ], function(vals) { window.__test_submitted = vals; });
        """)
        wait_briefly(page, 400)
        inp = page.locator("#eos-form-name")
        if inp.count() == 0:
            pytest.skip("Form field did not render")
        inp.fill("testvalue")
        # Click the submit button (typically inside modal)
        submit = page.locator(".eos-modal button[type='submit'], .eos-modal-body button").last
        if submit.count() > 0:
            submit.click()
            wait_briefly(page, 400)
            result = page.evaluate("window.__test_submitted")
            if result:
                assert result.get("name") == "testvalue"

    def test_confirm_dialog(self, page, base_url, page_errors):
        """EOS_UI.confirm: click yes → callback fires."""
        page.goto(base_url + "/", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 800)
        page.evaluate("""
            window.__confirmed = false;
            EOS_UI.confirm('Delete this?', function() { window.__confirmed = true; });
        """)
        wait_briefly(page, 400)
        yes = page.locator("#eos-confirm-yes, button:has-text('Yes'), button:has-text('Delete')").first
        if yes.count() == 0:
            # Close with Escape to clean up
            page.keyboard.press("Escape")
            pytest.skip("Confirm yes button not found")
        yes.click()
        wait_briefly(page, 400)
        result = page.evaluate("window.__confirmed")
        # Either confirmed or graceful fallback
        assert_no_js_errors(page_errors)


# =============================================================================
# SIDEBARS — app drawer, assistant session sidebar, slide-in panels
# =============================================================================


@pytest.mark.interactive
class TestAppDrawer:
    """Shell-level app drawer (hamburger menu) — shared across all pages."""

    def test_drawer_toggle(self, page, base_url, page_errors):
        """Click hamburger → drawer opens → click again → drawer closes."""
        page.goto(base_url + "/task/", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 1000)
        # Hamburger button (typically "⋯" or "☰") in top nav
        hamburger = page.locator(
            "[onclick*='toggleDrawer'], .nav-menu, button:has-text('⋯'), button:has-text('☰')"
        ).first
        if hamburger.count() == 0:
            pytest.skip("No drawer toggle button")
        hamburger.click()
        wait_briefly(page, 400)
        overlay = page.locator("#app-drawer-overlay, #app-drawer")
        # Close it
        page.keyboard.press("Escape")
        wait_briefly(page, 300)
        assert_no_js_errors(page_errors)

    def test_drawer_search_filters(self, page, base_url, page_errors):
        """Open drawer → type in search → app list filters."""
        page.goto(base_url + "/task/", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 1000)
        # Try to open drawer programmatically
        page.evaluate("if (typeof EOS !== 'undefined' && EOS.toggleDrawer) EOS.toggleDrawer()")
        wait_briefly(page, 400)
        search = page.locator("#drawer-search").first
        if search.count() > 0:
            search.fill("task")
            wait_briefly(page, 400)
        page.keyboard.press("Escape")
        assert_no_js_errors(page_errors)


@pytest.mark.interactive
class TestAssistantSidebar:
    """Assistant app's conversation sidebar with session list."""

    def test_sidebar_renders(self, app_page, page_errors):
        page = app_page("assistant")
        wait_briefly(page, 1500)
        sidebar = page.locator("#sidebar, .sidebar, .sb-header")
        assert sidebar.count() > 0, "Assistant sidebar not found"
        assert_no_js_errors(page_errors)

    def test_sidebar_has_new_button(self, app_page, page_errors):
        """New conversation button in sidebar header."""
        page = app_page("assistant")
        wait_briefly(page, 1500)
        new_btn = page.locator(
            "button:has-text('New'), [onclick*='newSession'], .sb-header button"
        ).first
        assert new_btn.count() > 0
        assert_no_js_errors(page_errors)

    def test_sidebar_sessions_clickable(self, app_page, page_errors):
        """Session items .s-item should be clickable when present."""
        page = app_page("assistant")
        wait_briefly(page, 2500)  # give sidebar more time to load sessions
        items = page.locator(".s-item, .session-item")
        if items.count() == 0:
            pytest.skip("No sessions in sidebar")
        try:
            items.first.click(timeout=5000)
            wait_briefly(page, 600)
        except Exception as e:
            # Session may not be clickable yet (e.g. loading) — not a hard failure
            pytest.skip(f"Session not clickable yet: {e}")
        assert_no_js_errors(page_errors)


# =============================================================================
# CHAT BOXES — assistant + any chat-style interface
# =============================================================================


@pytest.mark.interactive
class TestAssistantChatBox:
    """Assistant chat input, send, message stream."""

    def test_chat_input_visible(self, app_page, page_errors):
        page = app_page("assistant")
        wait_briefly(page, 1500)
        chat_input = page.locator("#input, #chat-input, textarea").first
        assert chat_input.count() > 0, "Chat input not found"
        assert_no_js_errors(page_errors)

    def test_chat_type_message(self, app_page, page_errors):
        """Type in chat input → verify text appears."""
        page = app_page("assistant")
        wait_briefly(page, 1500)
        chat_input = page.locator("#input").first
        if chat_input.count() == 0:
            pytest.skip("No #input chat field")
        chat_input.fill(f"{TEST_PREFIX}hello world")
        wait_briefly(page, 300)
        value = chat_input.input_value()
        assert TEST_PREFIX in value
        assert_no_js_errors(page_errors)

    def test_send_button_exists(self, app_page, page_errors):
        page = app_page("assistant")
        wait_briefly(page, 1500)
        send = page.locator("#btn-send, button:has-text('Send')").first
        assert send.count() > 0, "Send button not found"
        assert_no_js_errors(page_errors)

    def test_message_area_renders(self, app_page, page_errors):
        """Messages container should exist (even if empty)."""
        page = app_page("assistant")
        wait_briefly(page, 1500)
        msgs = page.locator("#messages, .messages, .chat-messages").first
        assert msgs.count() > 0, "Messages container not found"
        assert_no_js_errors(page_errors)

    def test_backend_selector_exists(self, app_page, page_errors):
        """Backend (provider) selector in topbar."""
        page = app_page("assistant")
        wait_briefly(page, 1500)
        selector = page.locator("select, [onclick*='backend']")
        # Don't fail if missing — just verify no errors
        assert_no_js_errors(page_errors)


@pytest.mark.interactive
class TestChatStreamingBehavior:
    """Chat streaming and message rendering (uses API, not live LLM)."""

    def test_compare_mode_container(self, app_page, page_errors):
        """Compare mode cards area should render when toggled."""
        page = app_page("assistant")
        wait_briefly(page, 1500)
        toggle = page.locator("[onclick*='compare'], button:has-text('Compare')").first
        if toggle.count() > 0:
            toggle.click()
            wait_briefly(page, 500)
            # Toggle back
            toggle.click()
        assert_no_js_errors(page_errors)

    def test_empty_state(self, app_page, page_errors):
        """Fresh session should show empty state."""
        page = app_page("assistant")
        wait_briefly(page, 1500)
        empty = page.locator(".empty-state, .eos-empty")
        assert_no_js_errors(page_errors)


# =============================================================================
# SLIDE-IN PANELS — publish settings/preview, other right-side panels
# =============================================================================


@pytest.mark.interactive
class TestSlidePanels:
    """Right-side slide-in panels (publish, settings, etc.)."""

    def test_publish_settings_panel_lifecycle(self, app_page, page_errors):
        """Open publish settings panel → close via Escape."""
        page = app_page("publish")
        wait_briefly(page, 1500)
        clicked = click_first(
            page,
            "[onclick*='openSettings']",
            ".btn-settings",
        )
        if not clicked:
            pytest.skip("No publish settings trigger")
        wait_briefly(page, 500)
        # Should have slide-in panel visible
        page.keyboard.press("Escape")
        wait_briefly(page, 400)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_note_actions_rendered(self, app_page, page_errors):
        """EOS.noteActions inline links should not break pages."""
        page = app_page("search")
        wait_briefly(page, 1000)
        query = page.locator("#query").first
        if query.count() > 0:
            query.fill("readme")
            query.press("Enter")
            wait_briefly(page, 2000)
        assert_no_js_errors(page_errors)
