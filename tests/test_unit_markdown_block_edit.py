"""Unit tests for emptyos/sdk/markdown_block_edit.py — pure, no daemon.

Covers the scoped markdown-block edit core: resolving an ``eN`` anchor to its
block, the instruction path via an injected stub ``think_fn``, the splice, and
every rejection branch (bad anchor, missing instruction, unresolvable/non-editable
block, whole-doc / multi-block reply, frontmatter-delimiter injection, changed
block type).
"""

from __future__ import annotations

import asyncio

from emptyos.sdk.markdown_block_edit import (
    propose_markdown_block_edit,
    validate_block_replacement,
)


def _run(coro):
    return asyncio.run(coro)


def _canned(reply):
    async def _think(system, user):
        return reply
    return _think


async def _boom(system, user):
    raise AssertionError("think_fn should not be called")


BODY = (
    "# Title\n\n"
    "First paragraph to edit.\n\n"
    "- one\n- two\n\n"
    "```\ncode block\n```\n"
)
# editable ordinals: heading=e0, paragraph=e1, list=e2 (code is non-editable)


def test_happy_path_paragraph_splice():
    r = _run(propose_markdown_block_edit(
        BODY, "e1", instruction="tighten it",
        think_fn=_canned("A tighter first paragraph."),
    ))
    assert r.ok
    assert "A tighter first paragraph." in r.new_body
    assert "First paragraph to edit." not in r.new_body
    # only that block changed — heading + list + code intact
    assert r.new_body.startswith("# Title")
    assert "- one\n- two" in r.new_body
    assert "```\ncode block\n```" in r.new_body


def test_heading_replacement_strips_fences():
    r = _run(propose_markdown_block_edit(
        BODY, "e0", instruction="rename",
        think_fn=_canned("```md\n# A Better Title\n```"),
    ))
    assert r.ok
    assert "# A Better Title" in r.new_body


def test_bad_anchor_rejected():
    r = _run(propose_markdown_block_edit(BODY, "xoxo", instruction="x", think_fn=_boom))
    assert not r.ok and r.error == "bad block anchor"


def test_missing_instruction_rejected():
    r = _run(propose_markdown_block_edit(BODY, "e1", instruction="   ", think_fn=_boom))
    assert not r.ok and "instruction" in r.error


def test_nonexistent_block_falls_back_to_iterate():
    r = _run(propose_markdown_block_edit(BODY, "e99", instruction="x", think_fn=_boom))
    assert not r.ok and r.fallback == "iterate"


def test_multi_block_reply_rejected():
    r = _run(propose_markdown_block_edit(
        BODY, "e1", instruction="expand",
        think_fn=_canned("First new para.\n\nA second sneaky paragraph."),
    ))
    assert not r.ok and "blocks" in r.error


def test_frontmatter_injection_rejected():
    r = _run(propose_markdown_block_edit(
        BODY, "e1", instruction="x",
        think_fn=_canned("Some text\n---\ntitle: injected"),
    ))
    assert not r.ok and "frontmatter" in r.error


def test_changed_block_type_rejected():
    # asked to edit a paragraph, model returns a heading
    r = _run(propose_markdown_block_edit(
        BODY, "e1", instruction="x",
        think_fn=_canned("## Now I am a heading"),
    ))
    assert not r.ok and "block type" in r.error


def test_validate_helper_direct():
    assert validate_block_replacement("A clean paragraph.", "paragraph") == ""
    assert "empty" in validate_block_replacement("   ", "paragraph")
    assert "frontmatter" in validate_block_replacement("x\n---\ny", "paragraph")
