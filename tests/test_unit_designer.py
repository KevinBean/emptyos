"""Unit tests for designer's pure HTML-extraction helpers (no daemon).

`_extract_html` is the salvage that fixed the recurring "LLM output does not look
like HTML" failures: larger design-system few-shots make gpt-class models wrap
the page in a prose preamble + ```html fence, which the bare `_strip_fences`
missed. These cases would have caught that regression.

shared.py is pure stdlib (re/secrets/datetime) so it loads standalone.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SHARED = (
    Path(__file__).resolve().parent.parent
    / "apps/public/standard/designer/shared.py"
)


@pytest.fixture(scope="module")
def shared():
    spec = importlib.util.spec_from_file_location("designer_shared", _SHARED)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


DOC = "<!doctype html><html><head></head><body><h1>Hi</h1></body></html>"


def test_bare_html_passes(shared):
    assert shared._looks_like_html(shared._extract_html(DOC))


def test_whole_string_fence(shared):
    assert shared._looks_like_html(shared._extract_html(f"```html\n{DOC}\n```"))


def test_prose_preamble_then_fence(shared):
    reply = f"Sure! Here's a Notion-styled landing page that matches the tokens.\n\n```html\n{DOC}\n```\n\nLet me know if you want tweaks."
    out = shared._extract_html(reply)
    assert shared._looks_like_html(out)
    assert "Sure!" not in out  # prose stripped


def test_long_prose_then_doctype_no_fence(shared):
    reply = ("I will now produce a complete standalone page. " * 8) + "\n" + DOC
    assert shared._looks_like_html(shared._extract_html(reply))


def test_trailing_prose_after_html(shared):
    out = shared._extract_html(DOC + "\n\nHope that helps!")
    assert shared._looks_like_html(out)
    assert out.rstrip().endswith("</html>")


def test_genuine_refusal_still_rejected(shared):
    out = shared._extract_html("I'm sorry, I can't produce that.")
    assert not shared._looks_like_html(out)  # _reject_reason will flag it


def test_truncation_guard(shared):
    truncated = "<!doctype html><html><body><h1>partial"
    bad, why = shared._looks_truncated(truncated)
    assert bad and "html" in why.lower()
