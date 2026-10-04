"""Pins for ``emptyos.sdk.pdf.pdf_print_options`` — the keyword set handed to
Playwright's ``page.pdf``.

The function exists so the two navigation aids a long PDF wants (a bookmark
sidebar built from the headings, and ``n / total`` page numbers) can be pinned
without launching Chromium. The load-bearing claim is the OFF state: with both
flags off the options are exactly what every pre-existing caller printed with,
so turning the aids on for one consumer cannot change another's output.
"""
from __future__ import annotations

from emptyos.sdk.pdf import PAGE_NUMBER_FOOTER, pdf_print_options


def test_defaults_are_the_pre_existing_print_call():
    """Byte-for-byte the old ``page.pdf(format=…, print_background=True,
    margin=…)`` call — no header/footer keys, no outline key at all."""
    m = {"top": "1mm", "bottom": "1mm", "left": "1mm", "right": "1mm"}
    assert pdf_print_options(page_size="A4", margin=m) == {
        "format": "A4", "print_background": True, "margin": m,
    }
    assert "outline" not in pdf_print_options()
    assert "display_header_footer" not in pdf_print_options()


def test_outline_asks_chromium_for_bookmarks_and_the_tagged_tree_they_need():
    """`outline` on its own produces a PDF with no bookmarks: Chromium builds
    the outline from the tagged structure tree. Measured, not assumed — a
    three-heading probe gave 0 bookmarks with outline alone and 3 with both."""
    opts = pdf_print_options(outline=True)
    assert opts["outline"] is True
    assert opts["tagged"] is True


def test_page_numbers_turn_the_footer_on_with_both_counters():
    opts = pdf_print_options(page_numbers=True)
    assert opts["display_header_footer"] is True
    assert opts["footer_template"] == PAGE_NUMBER_FOOTER
    # Chromium substitutes these class names; both must be present or the
    # footer prints a bare number with no total.
    assert 'class="pageNumber"' in opts["footer_template"]
    assert 'class="totalPages"' in opts["footer_template"]
    # The footer renders in its own document: an unset font-size prints
    # nothing visible, so the template has to carry one.
    assert "font-size" in opts["footer_template"]
    # Turning the footer on turns the header on too; it must be blank, not
    # Chromium's default (date + title).
    assert opts["header_template"].strip() and "pageNumber" not in opts["header_template"]


def test_the_two_aids_are_independent():
    only_outline = pdf_print_options(outline=True)
    assert "display_header_footer" not in only_outline
    only_numbers = pdf_print_options(page_numbers=True)
    assert "outline" not in only_numbers and "tagged" not in only_numbers
