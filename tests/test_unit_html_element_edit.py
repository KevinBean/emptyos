"""Unit tests for emptyos/sdk/html_element_edit.py — pure, no daemon.

Covers the scoped element-edit core shared by designer + viz: the deterministic
knob path (no LLM), the instruction path via an injected stub `think_fn`, and
every rejection branch (whole-document reply, dropped anchor, changed tag,
unresolvable anchor, srcdoc/iframe target, bad anchor, missing change).
"""

from __future__ import annotations

import asyncio

from emptyos.sdk.html_element_edit import (
    apply_knob,
    knob_value_ok,
    propose_element_edit,
    validate_replacement,
)


def _run(coro):
    return asyncio.run(coro)


async def _boom_think(system, user):  # think_fn that must NOT be called on knob paths
    raise AssertionError("think_fn should not be called for a knob edit")


def _canned(reply):
    async def _think(system, user):
        return reply
    return _think


DOC = (
    '<!doctype html><html><body>'
    '<p data-eos-el="e0">Hello world</p>'
    '<div data-eos-el="e1"><iframe srcdoc="&lt;p&gt;x&lt;/p&gt;"></iframe></div>'
    '<h2 data-eos-el="e2">Title</h2>'
    '</body></html>'
)


# ── knob path (deterministic, no LLM) ────────────────────────────────

def test_knob_merges_inline_style_without_think():
    r = _run(propose_element_edit(
        DOC, "e0", knob={"prop": "color", "value": "#ff0000"}, think_fn=_boom_think,
    ))
    assert r.ok
    assert r.el == "e0"
    assert 'style="color: #ff0000"' in r.new_html
    assert "Hello world" in r.new_html          # child content preserved
    assert "<h2 data-eos-el=\"e2\">Title</h2>" in r.new_html  # rest untouched


def test_knob_invalid_value_rejected():
    r = _run(propose_element_edit(
        DOC, "e0", knob={"prop": "color", "value": "redish"}, think_fn=_boom_think,
    ))
    assert not r.ok and "invalid value" in r.error


def test_knob_value_ok_matrix():
    assert knob_value_ok("color", "#abc")
    assert not knob_value_ok("color", "blue")
    assert knob_value_ok("font-size", "16px")
    assert not knob_value_ok("font-size", "16")
    assert knob_value_ok("text-align", "center")
    assert not knob_value_ok("text-align", "middle")


def test_apply_knob_unsupported_prop():
    repl, err = apply_knob('<p data-eos-el="e0">x</p>', {"prop": "margin", "value": "8px"})
    assert repl == "" and "unsupported" in err


# ── instruction path (stub think_fn) ─────────────────────────────────

def test_instruction_valid_replacement():
    r = _run(propose_element_edit(
        DOC, "e0", instruction="make it shout",
        think_fn=_canned('<p data-eos-el="e0">HELLO WORLD</p>'),
    ))
    assert r.ok
    assert '<p data-eos-el="e0">HELLO WORLD</p>' in r.new_html
    assert "Hello world" not in r.new_html


def test_instruction_strips_code_fence():
    r = _run(propose_element_edit(
        DOC, "e0", instruction="x",
        think_fn=_canned('```html\n<p data-eos-el="e0">Hi</p>\n```'),
    ))
    assert r.ok and '<p data-eos-el="e0">Hi</p>' in r.new_html


def test_instruction_whole_document_rejected():
    r = _run(propose_element_edit(
        DOC, "e0", instruction="x",
        think_fn=_canned('<!doctype html><html><body><p data-eos-el="e0">no</p></body></html>'),
    ))
    assert not r.ok and "whole document" in r.error


def test_instruction_dropped_anchor_rejected():
    r = _run(propose_element_edit(
        DOC, "e0", instruction="x", think_fn=_canned('<p>orphan</p>'),
    ))
    assert not r.ok and "anchor" in r.error


def test_instruction_changed_tag_rejected():
    r = _run(propose_element_edit(
        DOC, "e0", instruction="x", think_fn=_canned('<div data-eos-el="e0">x</div>'),
    ))
    assert not r.ok and "outer tag" in r.error


# ── resolution / guard branches ──────────────────────────────────────

def test_unresolvable_anchor_falls_back_to_iterate():
    r = _run(propose_element_edit(
        DOC, "e9", instruction="x", think_fn=_boom_think,
    ))
    assert not r.ok and r.fallback == "iterate"


def test_iframe_target_rejected():
    r = _run(propose_element_edit(
        DOC, "e1", instruction="x", think_fn=_boom_think,
    ))
    assert not r.ok and r.fallback == "" and "embedded" in r.error


def test_bad_anchor_rejected():
    r = _run(propose_element_edit(
        DOC, "not-an-anchor", instruction="x", think_fn=_boom_think,
    ))
    assert not r.ok and "bad element anchor" in r.error


def test_no_change_requested():
    r = _run(propose_element_edit(DOC, "e0", think_fn=_boom_think))
    assert not r.ok and "instruction or knob" in r.error


# ── validate_replacement direct ──────────────────────────────────────

def test_validate_replacement_ok():
    assert validate_replacement('<p data-eos-el="e0">x</p>', "p", "e0") == ""


def test_validate_replacement_empty():
    assert "empty" in validate_replacement("", "p", "e0")
