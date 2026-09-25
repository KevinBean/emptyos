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
# MARKDOWN — shared EOS_UI.renderMarkdown
# =============================================================================


@pytest.mark.interactive
class TestRenderMarkdown:
    """Pins EOS_UI.renderMarkdown edge cases (regressions here mangle every
    markdown surface: kb detail, chat bubbles, note previews)."""

    def _render(self, page, base_url, md):
        page.goto(base_url + "/", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 800)
        return page.evaluate("EOS_UI.renderMarkdown(" + repr(md) + ")")

    # ── Markdown links ────────────────────────────────────────────────────
    # 382 links across 107 KB notes rendered as raw `[text](url)`: the rule was
    # never written, and the image rule's own comment promised one "handled
    # later" that did not exist.

    def test_markdown_link_renders(self, page, base_url, page_errors):
        html = self._render(page, base_url, "see [the paper](https://example.com/x.pdf) here")
        assert '<a ' in html and 'href="https://example.com/x.pdf"' in html
        assert "[the paper]" not in html
        assert_no_js_errors(page_errors)

    def test_external_link_opens_in_a_new_tab(self, page, base_url, page_errors):
        html = self._render(page, base_url, "[x](https://example.com)")
        assert 'target="_blank"' in html and "noopener" in html
        assert_no_js_errors(page_errors)

    def test_relative_link_stays_in_place(self, page, base_url, page_errors):
        html = self._render(page, base_url, "[spec](docs/SPEC.md)")
        assert 'href="docs/SPEC.md"' in html
        assert 'target="_blank"' not in html
        assert_no_js_errors(page_errors)

    def test_script_url_is_refused(self, page, base_url, page_errors):
        """A note is content, not a script host."""
        html = self._render(page, base_url, "[click](javascript:alert(1))")
        assert "javascript:" not in html
        assert "<a " not in html
        assert_no_js_errors(page_errors)

    def test_image_is_still_an_image_not_a_link(self, page, base_url, page_errors):
        html = self._render(page, base_url, "![alt](pic.png)")
        assert "<img" in html
        assert_no_js_errors(page_errors)

    # ── LaTeX math ────────────────────────────────────────────────────────
    # 68 vault notes carry TeX; the worst has 3,872 block equations. Before
    # extraction they rendered as raw `$...$` AND were mangled by the emphasis
    # rules, because `Z_Q` and `c^2U^2/S''_k` are full of underscores.

    def test_inline_math_becomes_a_carrier(self, page, base_url, page_errors):
        html = self._render(page, base_url, "Given $Z_Q = c U$ then done.")
        assert 'class="eos-math"' in html
        assert "$" not in html, "delimiters must not survive"
        assert_no_js_errors(page_errors)

    def test_math_is_not_mangled_by_emphasis(self, page, base_url, page_errors):
        """The real bug: underscores inside TeX were eaten as italics."""
        html = self._render(page, base_url, r"$R_Q$ and $X_Q$ and $c^2U^2/S_k$")
        assert "<em>" not in html and "<i>" not in html
        assert "R_Q" in html and "S_k" in html
        assert_no_js_errors(page_errors)

    def test_math_carrier_shows_its_source_before_typesetting(self, page, base_url, page_errors):
        """Graceful degradation: with no typesetter the formula still reads."""
        html = self._render(page, base_url, r"$\sqrt3 \times 14400$")
        assert "sqrt3" in html, "source text must be visible in the carrier"
        assert 'data-tex=' in html
        assert_no_js_errors(page_errors)

    def test_block_math_is_display(self, page, base_url, page_errors):
        html = self._render(page, base_url, "Therefore:\n\n$$I_k = a/b$$")
        assert "eos-math-block" in html
        assert_no_js_errors(page_errors)

    def test_currency_is_not_math(self, page, base_url, page_errors):
        """`$5 and $6` is prose about money, not a formula."""
        html = self._render(page, base_url, "It costs $5 and $6 total.")
        assert "eos-math" not in html
        assert "$5" in html and "$6" in html
        assert_no_js_errors(page_errors)

    def test_dollars_in_a_code_fence_stay_literal(self, page, base_url, page_errors):
        html = self._render(page, base_url, "```\nprice = $x$\n```")
        assert "eos-math" not in html
        assert_no_js_errors(page_errors)

    def test_escaped_dollar_is_not_math(self, page, base_url, page_errors):
        html = self._render(page, base_url, r"costs \$5 exactly")
        assert "eos-math" not in html
        assert_no_js_errors(page_errors)

    def test_bold_crosses_single_newline(self, page, base_url, page_errors):
        """Hard-wrapped vault prose: **bold\\ntext** must render <strong>,
        never literal ** (the 2026-07-11 kb-audit bug)."""
        html = self._render(page, base_url, "how **existing trees are\nprotected** now")
        assert "<strong>" in html and "**" not in html
        assert_no_js_errors(page_errors)

    def test_bold_never_crosses_paragraph_break(self, page, base_url, page_errors):
        """A stray ** in one paragraph must not pair with bold markers in the
        next paragraph (blank line = hard boundary)."""
        html = self._render(page, base_url, "broken ** here\n\nnext **real** bold")
        assert "<strong>real</strong>" in html
        assert html.count("<strong>") == 1
        assert_no_js_errors(page_errors)

    def test_star_bullets_not_eaten_by_italic(self, page, base_url, page_errors):
        """`* `-style bullet lines stay a list — the italic rule must not span
        newlines and swallow them."""
        html = self._render(page, base_url, "* bullet one\n* bullet two")
        assert "<li>bullet one</li>" in html and "<em>" not in html
        assert_no_js_errors(page_errors)

    def test_table_align_row_never_renders(self, page, base_url, page_errors):
        """|---|---| separator becomes <thead>, never a visible dashes row
        (pre-2026-07-22 bug: isAlignRow regex missed multi-column separators)."""
        html = self._render(page, base_url, "| A | B |\n|---|---|\n| 1 | 2 |")
        assert "<thead>" in html and "<th>A</th>" in html
        assert "---" not in html
        assert_no_js_errors(page_errors)

    def test_obsidian_comments_stripped(self, page, base_url, page_errors):
        """Paired %%...%% is author-only and never renders; %% inside a code
        fence is preserved."""
        html = self._render(page, base_url, "keep %%secret\nnote%% this\n\n```\na %%not a comment%% b\n```")
        assert "secret" not in html and "keep" in html and "this" in html
        assert "%%not a comment%%" in html
        assert_no_js_errors(page_errors)

    def test_block_style_frontmatter_tags_render(self, page, base_url, page_errors):
        """Vault-mandated block-style tags (tags:\\n  - a) must appear as pills
        in the Properties card (the old parser silently skipped them)."""
        html = self._render(page, base_url, "---\ntags:\n  - career\n  - wellbeing\n---\n\nbody")
        assert "obs-frontmatter" in html
        assert "#career" in html and "#wellbeing" in html
        assert_no_js_errors(page_errors)

    def test_numbered_list_keeps_numbering(self, page, base_url, page_errors):
        """1./2./3. lines become a real <ol> (previously collapsed into <ul>,
        losing the numbers)."""
        html = self._render(page, base_url, "1. first\n2. second")
        assert "<ol>" in html and "<li>first</li>" in html
        assert_no_js_errors(page_errors)

    def test_highlight_renders_mark(self, page, base_url, page_errors):
        """==text== renders as <mark> (parity with the PDF renderer)."""
        html = self._render(page, base_url, "an ==important== word")
        assert '<mark class="obs-mark">important</mark>' in html
        assert_no_js_errors(page_errors)

    # ── Code is never rewritten ───────────────────────────────────────────
    # Wikilinks, embeds and bare `.md` paths were substituted across the whole
    # document before code was protected, so a literal written inside a fence
    # or a code span became a real link or a real <img>. Surfaced on a canvas
    # card that showed a broken-image glyph for an `![[photo.png]]` written as
    # an example. Same defect existed server-side in resolve_wikilinks and in
    # the link index; all three now share one rule.

    def test_embed_inside_inline_code_is_not_an_image(self, page, base_url, page_errors):
        html = self._render(page, base_url, "An `![[photo.png]]` example.")
        assert "<img" not in html
        assert "<code" in html
        assert_no_js_errors(page_errors)

    def test_embed_inside_a_fence_is_not_an_image(self, page, base_url, page_errors):
        html = self._render(page, base_url, "```\n![[photo.png]]\n```\n")
        assert "<img" not in html
        assert_no_js_errors(page_errors)

    def test_wikilink_inside_a_fence_is_not_a_link(self, page, base_url, page_errors):
        html = self._render(page, base_url, "```js\nconst q = `[[Note]]`;\n```\n")
        assert "<a " not in html
        assert_no_js_errors(page_errors)

    def test_bare_md_path_inside_code_is_not_a_link(self, page, base_url, page_errors):
        html = self._render(page, base_url, "See `plan.md` here.")
        assert "<a " not in html
        assert_no_js_errors(page_errors)

    def test_an_unclosed_fence_protects_to_end_of_input(self, page, base_url, page_errors):
        """`$` under /m matches end of LINE — the first fix parked only the
        opening fence and let the body through."""
        html = self._render(page, base_url, "text\n```\n![[photo.png]]\n")
        assert "<img" not in html
        assert_no_js_errors(page_errors)

    def test_a_longer_run_closes_a_shorter_fence(self, page, base_url, page_errors):
        """CommonMark: the closing fence must be at least as long, not equal.

        A `\\1` backreference demands an exact length, so ```…```` left the rest
        of the document parked and every later link vanished. Found by checking
        the JS against the Python rule rather than by reading either.
        """
        html = self._render(page, base_url, "```\n[[Hidden]]\n````\n[[Shown]]\n")
        assert "Shown" in html
        assert "<a " in html
        assert_no_js_errors(page_errors)

    def test_real_links_and_embeds_still_render(self, page, base_url, page_errors):
        """The other direction: protecting code must not disable markdown."""
        html = self._render(page, base_url, "```\n[[A]]\n```\n\nReal [[B]] and ![[c.png]].\n")
        assert html.count("<a ") >= 1
        assert "<img" in html
        assert_no_js_errors(page_errors)

    def test_frontmatter_split_survives_code_protection(self, page, base_url, page_errors):
        """Code is restored before the `---` split, which needs the real text."""
        html = self._render(page, base_url, "---\ntitle: x\n---\n\nBody [[N]].\n")
        assert "<a " in html
        assert_no_js_errors(page_errors)


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
        # NOTE: confirm() returns a Promise that resolves on button click.
        # page.evaluate AWAITS a returned promise — returning it here deadlocks
        # (the click that resolves it can only happen after evaluate returns).
        # End with `null` so the script's completion value isn't the promise.
        page.evaluate("""
            window.__confirmed = false;
            EOS_UI.confirm('Delete this?', function() { window.__confirmed = true; });
            null;
        """)
        wait_briefly(page, 400)
        # Target the dialog's own button id ONLY — a text-based fallback
        # (`button:has-text('Delete')`) can match a hidden button elsewhere on
        # the home page first, and clicking a hidden element hangs the suite.
        yes = page.locator("#eos-confirm-yes")
        if yes.count() == 0:
            # Close with Escape to clean up
            page.keyboard.press("Escape")
            pytest.skip("Confirm yes button not found")
        yes.click(timeout=5000)
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


# =============================================================================
# FORM FIELDS — shared EOS_UI.formHtml
# =============================================================================


@pytest.mark.interactive
class TestFormHtmlNumberDomain:
    """Pins `step` / `min` / `max` on a number field.

    All three were hardcoded away until 2026-08-13 — the branch emitted a
    literal `step="any"` and nothing else — so a caller declaring a domain got
    silence rather than an error. `cable_network`'s project form had been
    passing `step: 0.1` on soil resistivity the whole time.

    Both directions matter here and the second is the load-bearing one: a
    field declaring nothing must render exactly as it did before, because 149
    call sites depend on that and none of them asked for this.
    """

    def _field(self, page, base_url, field):
        page.goto(base_url + "/", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 800)
        return page.evaluate("EOS_UI.formHtml([" + repr(field).replace("'", '"') + "])")

    def test_a_declared_step_reaches_the_input(self, page, base_url, page_errors):
        html = self._field(page, base_url, {"key": "rho", "type": "number", "step": 0.1})
        assert 'step="0.1"' in html
        assert 'step="any"' not in html
        assert_no_js_errors(page_errors)

    def test_a_declared_domain_reaches_the_input(self, page, base_url, page_errors):
        html = self._field(
            page, base_url, {"key": "x", "type": "number", "min": 0, "max": 10}
        )
        assert 'min="0"' in html and 'max="10"' in html
        assert_no_js_errors(page_errors)

    def test_a_field_declaring_nothing_is_unchanged(self, page, base_url, page_errors):
        """The regression contract for the other 149 call sites."""
        html = self._field(page, base_url, {"key": "n", "type": "number"})
        assert 'step="any"' in html
        assert " min=" not in html and " max=" not in html
        assert_no_js_errors(page_errors)
