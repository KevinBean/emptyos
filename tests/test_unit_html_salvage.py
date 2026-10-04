"""Unit tests for salvage_truncated_html — pure, no daemon.

The graceful-degradation half of the viz one-shot-ceiling fix: when a dense
artifact is cut mid-stream, don't discard everything — repair the partial into a
valid, self-contained document with a visible truncation banner so the user sees
what completed (no silent cap).
"""

from __future__ import annotations

from emptyos.sdk.html_artifact import (
    looks_truncated,
    salvage_truncated_html,
)


def test_appends_missing_closers():
    partial = "<!doctype html><html><head><title>x</title></head><body><h1>Hi</h1><p>cut here"
    out = salvage_truncated_html(partial)
    low = out.lower()
    assert low.rstrip().endswith("</html>")
    assert "</body>" in low


def test_salvaged_output_passes_truncation_check():
    partial = "<!doctype html><html><head></head><body><div>content"
    out = salvage_truncated_html(partial)
    truncated, _ = looks_truncated(out)
    assert truncated is False  # the repaired doc is now a saveable artifact


def test_banner_injected_after_body():
    partial = "<!doctype html><html><head></head><body><section>slide 1"
    out = salvage_truncated_html(partial)
    assert "⚠️" in out
    bi = out.lower().find("<body")
    bj = out.find(">", bi)
    # Banner sits immediately after the <body ...> open tag, before content.
    assert "Truncated".lower() in out[bj:bj + 400].lower() or "truncated" in out[bj:bj + 400].lower()
    assert out.index("⚠️") > bj                     # after <body>
    assert out.index("⚠️") < out.lower().index("slide 1")  # before the content


def test_custom_note_used_and_escaped():
    partial = "<!doctype html><html><body><p>x"
    out = salvage_truncated_html(partial, banner_note="cut & <trimmed>")
    assert "cut &amp; &lt;trimmed&gt;" in out
    assert "cut & <trimmed>" not in out


def test_dangling_half_tag_dropped():
    # A doc cut mid-tag must not leave an unterminated element to swallow the
    # banner / closers.
    partial = '<!doctype html><html><body><p>ok</p><div class="foo'
    out = salvage_truncated_html(partial)
    # The half-written `<div class="foo` (no closing >) is trimmed off.
    assert 'class="foo' not in out
    assert out.rstrip().lower().endswith("</html>")


def test_no_body_tag_prepends_banner():
    partial = "<!doctype html><html><h1>orphan"
    out = salvage_truncated_html(partial)
    assert "⚠️" in out
    assert out.rstrip().lower().endswith("</html>")


def test_empty_input_returns_empty():
    assert salvage_truncated_html("") == ""
    assert salvage_truncated_html("   ") == ""


def test_only_html_closer_missing():
    # </body> present, only </html> was cut — append just </html>, don't double.
    partial = "<!doctype html><html><body><p>done</p></body>"
    out = salvage_truncated_html(partial)
    assert out.lower().count("</body>") == 1
    assert out.rstrip().lower().endswith("</html>")
