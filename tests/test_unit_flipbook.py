"""Unit tests for emptyos/sdk/flipbook.py — pure, no daemon.

Covers the extracted Flipbook generation engine shared by kb (consumer #1) and
condition-map (consumer #2): SVG/image/peek generation via an injected stub
``think_fn``, the predefined-callout + symbol prompt-hint builders (with a
byte-identical regression lock against kb's pre-extraction inline text), the
fallback path, and the pure vision-refine helpers.
"""

from __future__ import annotations

import asyncio

from emptyos.sdk import flipbook as fb


def _run(coro):
    return asyncio.run(coro)


def _canned(reply):
    async def _think(system, user):
        return reply
    return _think


async def _boom_think(system, user):  # must NOT be called on pure-helper paths
    raise AssertionError("think_fn should not be called here")


# ── generate_svg_page ────────────────────────────────────────────────

def test_svg_page_happy():
    reply = (
        '{"title": "Cable", "subtitle": "A wire", '
        '"svg": "<svg data-anchor=\\"0\\"></svg>", '
        '"callouts": [{"label": "Core", "body": "carries current", "x": 50, "y": 50}], '
        '"caption": "one job each"}'
    )
    page = _run(fb.generate_svg_page("XLPE cable", think_fn=_canned(reply)))
    assert page.ok and not page.used_fallback
    assert page.title == "Cable"
    assert page.subtitle == "A wire"
    assert page.svg == '<svg data-anchor="0"></svg>'
    assert page.callouts[0]["label"] == "Core"
    assert page.caption == "one job each"


def test_svg_page_no_svg_uses_fallback():
    page = _run(fb.generate_svg_page(
        "Topic", think_fn=_canned('{"title": "T", "callouts": []}'),
        fallback_svg="<svg>FB</svg>",
    ))
    assert page.ok and page.used_fallback
    assert page.svg == "<svg>FB</svg>"


def test_svg_page_default_fallback_when_none_passed():
    page = _run(fb.generate_svg_page("Topic", think_fn=_canned("not json at all")))
    assert page.ok and page.used_fallback
    assert page.svg == fb.DEFAULT_FALLBACK_SVG


def test_svg_page_title_falls_back_to_topic():
    page = _run(fb.generate_svg_page(
        "MyTopic", think_fn=_canned('{"svg": "<svg/>"}'),
    ))
    assert page.title == "MyTopic"


def test_svg_page_think_error_is_soft():
    async def _boom(system, user):
        raise RuntimeError("provider down")
    page = _run(fb.generate_svg_page("T", think_fn=_boom))
    assert page.ok is False
    assert page.used_fallback and page.svg == fb.DEFAULT_FALLBACK_SVG
    assert "think failed" in page.error


# ── callouts hint — byte-identical regression lock ───────────────────

def test_callouts_hint_labels_only_byte_identical():
    """Reproduce kb's pre-extraction inline callouts_hint exactly (no body)."""
    got = fb._build_callouts_hint(
        [{"label": "foo bar"}, {"label": "baz"}], "Topic",
    )
    expected = (
        "\n\nThe callouts MUST be exactly these concepts (one "
        "callout per item, in this order, using these labels):\n"
        "  1. foo bar\n  2. baz"
        "\n\nDo not invent additional callouts. Do not drop any "
        "of the required ones. The SVG should visually represent "
        "'Topic' with each listed concept marked as a distinct "
        "feature (use data-anchor=\"<idx>\" on the SVG element "
        "depicting each one)."
    )
    assert got == expected


def test_callouts_hint_with_body():
    got = fb._build_callouts_hint(
        [{"label": "Money pressure", "body": "rent is due"}], "Stress",
    )
    assert "  1. Money pressure — rent is due" in got


def test_callouts_hint_empty_when_none():
    assert fb._build_callouts_hint(None, "T") == ""
    assert fb._build_callouts_hint([], "T") == ""
    assert fb._build_callouts_hint([{"label": ""}], "T") == ""


# ── symbol hint — byte-identical regression lock ─────────────────────

def test_symbol_hint_byte_identical():
    got = fb._build_symbol_hint([
        {"id": "transformer", "name": "transformer", "description": "A pad-mount"},
        {"id": "tower", "name": "tower", "description": ""},
    ])
    expected = (
        "\n\nA symbol library is available. Reference any symbol by "
        "id with: `<use href='#<id>' x='..' y='..' width='..' "
        "height='..' data-anchor='<callout-idx>'/>`. "
        "Prefer symbols for shapes you'd otherwise draw from scratch. "
        "Available symbols:\n"
        "- id='transformer'  (transformer: A pad-mount)\n"
        "- id='tower'  (tower)"
    )
    assert got == expected


def test_symbol_hint_empty_when_none():
    assert fb._build_symbol_hint(None) == ""
    assert fb._build_symbol_hint([]) == ""


# ── prompt assembly order matches kb (PROMPT_TEMPLATE + callouts + symbols) ─

def test_svg_prompt_assembly_order():
    captured = {}

    async def _capture(system, user):
        captured["system"] = system
        captured["user"] = user
        return '{"svg": "<svg/>"}'

    _run(fb.generate_svg_page(
        "Topic", context="ctx",
        callouts_in=[{"label": "A"}],
        symbol_catalog=[{"id": "x", "name": "x", "description": ""}],
        think_fn=_capture,
    ))
    expected = (
        fb.PROMPT_TEMPLATE.format(topic="Topic", context="ctx")
        + fb._build_callouts_hint([{"label": "A"}], "Topic")
        + fb._build_symbol_hint([{"id": "x", "name": "x", "description": ""}])
    )
    assert captured["user"] == expected
    assert captured["system"] == fb.SYSTEM_PROMPT


# ── generate_image_meta ──────────────────────────────────────────────

def test_image_meta_happy():
    reply = '{"title": "T", "image_prompt": "a guitar, no text", "callouts": []}'
    meta = _run(fb.generate_image_meta("Guitar", think_fn=_canned(reply)))
    assert meta["ok"] and meta["image_prompt"] == "a guitar, no text"


def test_image_meta_prompt_falls_back_to_topic():
    meta = _run(fb.generate_image_meta("Topic", think_fn=_canned("{}")))
    assert meta["image_prompt"] == "Topic"


def test_image_meta_think_error_is_soft():
    async def _boom(system, user):
        raise RuntimeError("x")
    meta = _run(fb.generate_image_meta("T", think_fn=_boom))
    assert meta["ok"] is False and meta["image_prompt"] == "T"


# ── generate_peek ────────────────────────────────────────────────────

def test_peek_happy():
    reply = '{"summary": "It does X", "facts": ["a", "b"]}'
    peek = _run(fb.generate_peek("Thing", parent="Page", think_fn=_canned(reply)))
    assert peek == {"summary": "It does X", "facts": ["a", "b"]}


def test_peek_garbage_is_empty():
    peek = _run(fb.generate_peek("Thing", think_fn=_canned("sorry, here you go")))
    assert peek == {"summary": "", "facts": []}


def test_peek_think_error_is_empty():
    async def _boom(system, user):
        raise RuntimeError("x")
    assert _run(fb.generate_peek("Thing", think_fn=_boom)) == {"summary": "", "facts": []}


# ── build_refine_messages ────────────────────────────────────────────

def test_build_refine_messages_shape():
    msgs = fb.build_refine_messages(
        [{"label": "Core"}, {"label": "Sheath"}], "QUJD",
    )
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == fb.REFINE_ANCHOR_SYSTEM_PROMPT
    user = msgs[1]
    assert user["role"] == "user"
    text = user["content"][0]["text"]
    assert "0. Core" in text and "1. Sheath" in text
    assert user["content"][1]["image_url"]["url"] == "data:image/png;base64,QUJD"


# ── parse_refine_anchors ─────────────────────────────────────────────

def test_parse_refine_anchors_clamps_rounds_drops():
    raw = [
        {"idx": 0, "x": 33.333, "y": 66.666},   # kept, rounded
        {"idx": 5, "x": 10, "y": 10},            # idx out of range → dropped
        {"idx": 1, "x": 150, "y": 10},           # x out of range → dropped
        {"idx": 1, "x": "nope", "y": 10},        # non-numeric → dropped
        "garbage",                               # non-dict → dropped
    ]
    out = fb.parse_refine_anchors(raw, callout_count=2)
    assert out == [{"idx": 0, "x": 33.3, "y": 66.7}]


def test_parse_refine_anchors_empty():
    assert fb.parse_refine_anchors([], 3) == []
    assert fb.parse_refine_anchors(None, 3) == []
