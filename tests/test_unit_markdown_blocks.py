"""Unit tests for emptyos/sdk/markdown_blocks.py — pure, no daemon.

Covers the deterministic markdown block splitter (paragraphs/headings/lists/
blockquotes/callouts/fences/tables/images/hr/footnote-defs) and the top-level
anchor stamper's fingerprint-verified zip, including the ``aligned_ok=False``
safety valve on a desync. Rendering uses the real ``render_markdown`` so the
zip is exercised against genuine python-markdown output.
"""

from __future__ import annotations

import pytest

from emptyos.sdk.markdown_blocks import (
    EDITABLE_BLOCK_KINDS,
    split_markdown_blocks,
    stamp_markdown_blocks,
)
from emptyos.sdk.markdown_render import HAS_MARKDOWN, render_markdown


def _kinds(body):
    return [(b.kind, b.idx) for b in split_markdown_blocks(body)]


def test_split_basic_kinds_and_editable_ordinals():
    body = (
        "# Title\n\n"
        "First paragraph here.\n\n"
        "- one\n- two\n\n"
        "> a quote\n\n"
        "```python\ncode = 1\n\nstill_code = 2\n```\n\n"
        "Second paragraph.\n"
    )
    blocks = split_markdown_blocks(body)
    kinds = [b.kind for b in blocks]
    assert kinds == ["heading", "paragraph", "list", "blockquote", "code", "paragraph"]
    # Editable ordinals are sequential over editable blocks; code is -1.
    idxs = {b.kind: b.idx for b in blocks if b.kind != "paragraph"}
    assert idxs["heading"] == 0
    assert idxs["list"] == 2
    assert idxs["blockquote"] == 3
    assert idxs["code"] == -1
    # spans slice back to the source text
    for b in blocks:
        assert body[b.start:b.end] == b.text


def test_fenced_code_blank_lines_do_not_split():
    body = "```\nline1\n\nline2\n```\n\nAfter.\n"
    blocks = split_markdown_blocks(body)
    assert [b.kind for b in blocks] == ["code", "paragraph"]


def test_callout_vs_blockquote():
    body = "> [!note] Heads up\n> body line\n\n> plain quote\n"
    blocks = split_markdown_blocks(body)
    assert [b.kind for b in blocks] == ["callout", "blockquote"]
    assert all(b.idx >= 0 for b in blocks)


def test_image_only_paragraph_classified_image():
    body = "![alt](assets/x.png)\n\nWords.\n"
    blocks = split_markdown_blocks(body)
    assert blocks[0].kind == "image"
    assert blocks[1].kind == "paragraph"


def test_table_and_hr_and_footnote_def_non_editable():
    body = (
        "| a | b |\n| - | - |\n| 1 | 2 |\n\n"
        "---\n\n"
        "[^1]: a footnote definition\n"
    )
    blocks = split_markdown_blocks(body)
    kinds = [b.kind for b in blocks]
    assert kinds == ["table", "hr", "footnote_def"]
    assert all(b.idx == -1 for b in blocks)


def test_editable_kinds_membership():
    assert "paragraph" in EDITABLE_BLOCK_KINDS
    assert "code" not in EDITABLE_BLOCK_KINDS
    assert "table" not in EDITABLE_BLOCK_KINDS


def test_cjk_char_offsets():
    body = "第一段中文。\n\nSecond paragraph.\n"
    blocks = split_markdown_blocks(body)
    assert blocks[0].text == "第一段中文。"
    assert body[blocks[0].start:blocks[0].end] == "第一段中文。"


@pytest.mark.skipif(not HAS_MARKDOWN, reason="markdown package not installed")
def test_stamp_aligns_and_anchors_editable_blocks():
    body = (
        "# Hello World\n\n"
        "A first paragraph with a [[Wiki Note|display text]] link.\n\n"
        "- one\n- two\n\n"
        "```\ncode\n```\n\n"
        "Closing paragraph.\n"
    )
    html, _ = render_markdown(body)
    anchored, index, aligned = stamp_markdown_blocks(body, html)
    assert aligned is True
    # 4 editable blocks: heading, paragraph, list, paragraph (code excluded)
    assert set(index.keys()) == {"e0", "e1", "e2", "e3"}
    assert index["e0"]["kind"] == "heading"
    assert index["e2"]["kind"] == "list"
    # anchors stamped onto the rendered top-level elements
    assert 'data-eos-el="e0"' in anchored
    assert 'data-eos-el="e3"' in anchored
    # the excluded code block gets no anchor
    assert anchored.count("data-eos-el=") == 4
    # md-span attribute present and slices back to the source block
    assert 'data-eos-md=' in anchored
    s, e = index["e0"]["start"], index["e0"]["end"]
    assert body[s:e] == "# Hello World"


@pytest.mark.skipif(not HAS_MARKDOWN, reason="markdown package not installed")
def test_stamp_disables_on_desync():
    # HTML that does not correspond to the body (extra editable element) → the
    # fingerprint/count check must refuse rather than mis-anchor.
    body = "Only one paragraph.\n"
    html, _ = render_markdown(body)
    bad_html = html + "<p>an extra unrelated paragraph</p>"
    anchored, index, aligned = stamp_markdown_blocks(body, bad_html)
    assert aligned is False
    assert index == {}
    assert anchored == bad_html  # returned unchanged


@pytest.mark.skipif(not HAS_MARKDOWN, reason="markdown package not installed")
def test_stamp_headings_with_permalink_still_match():
    # render_markdown enables toc permalinks (¶); the fingerprint must ignore it.
    body = "## A Heading\n\nBody text.\n"
    html, _ = render_markdown(body)
    _, index, aligned = stamp_markdown_blocks(body, html)
    assert aligned is True
    assert index["e0"]["kind"] == "heading"
