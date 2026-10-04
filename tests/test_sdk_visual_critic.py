"""Tests for emptyos.sdk.visual_critic — score a 3D-model render against intent.

Pure SDK tests; no daemon. Extracted from
apps/personal/robot-modeller/critic.py per CLAUDE.md rule 9 (2nd consumer:
apps/extension/engineering/cad). Covers the three functions: message
building, critique parsing (incl. the failure shapes), and signals
formatting.
"""

from __future__ import annotations

from emptyos.sdk.visual_critic import (
    build_critic_messages,
    format_visual_signals,
    parse_critique,
)


def test_build_critic_messages_shape():
    msgs = build_critic_messages(b"\x89PNGfakebytes", "a small red box", system="SYSTEM PROMPT")
    assert msgs[0] == {"role": "system", "content": "SYSTEM PROMPT"}
    user = msgs[1]
    assert user["role"] == "user"
    assert user["content"][0]["text"].startswith("INTENT: a small red box")
    url = user["content"][1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")


def test_build_critic_messages_custom_mime():
    msgs = build_critic_messages(b"jpegbytes", "x", system="S", mime="image/jpeg")
    assert msgs[1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_parse_critique_fenced_json():
    reply = (
        "Here is my assessment.\n\n```json\n"
        '{"score": 8, "matches": ["base present"], "mismatches": [], '
        '"suggestions": ["add fillets"]}\n```\n'
    )
    crit, err = parse_critique(reply)
    assert err == ""
    assert crit == {
        "score": 8, "matches": ["base present"],
        "mismatches": [], "suggestions": ["add fillets"],
    }


def test_parse_critique_bare_json_no_fence():
    crit, err = parse_critique('{"score": 3}')
    assert err == ""
    assert crit == {"score": 3, "matches": [], "mismatches": [], "suggestions": []}


def test_parse_critique_drops_non_string_list_items():
    reply = '{"score": 5, "matches": ["ok", 42, null], "mismatches": [], "suggestions": []}'
    crit, err = parse_critique(reply)
    assert err == ""
    assert crit["matches"] == ["ok"]


def test_parse_critique_rejects_out_of_range_score():
    crit, err = parse_critique('{"score": 15}')
    assert crit is None
    assert "score must be int" in err


def test_parse_critique_rejects_missing_score():
    crit, err = parse_critique('{"matches": ["a"]}')
    assert crit is None
    assert "score must be int" in err


def test_parse_critique_no_json_at_all():
    crit, err = parse_critique("I could not render this properly.")
    assert crit is None
    assert err  # some parse error, exact wording is parse_llm_json's


def test_format_visual_signals_shape():
    critique = {"score": 8, "matches": ["base present"], "mismatches": ["head missing"],
                "suggestions": ["add a head shape"]}
    block = format_visual_signals(critique)
    assert block.startswith("<visual_signals>")
    assert block.endswith("</visual_signals>")
    assert "score: 8 / 10" in block
    assert "[match]      base present" in block
    assert "[mismatch]   head missing" in block
    assert "[suggestion] add a head shape" in block


def test_format_visual_signals_empty_lists_omit_lines():
    block = format_visual_signals({"score": 10, "matches": [], "mismatches": [], "suggestions": []})
    assert "[match]" not in block
    assert "[mismatch]" not in block
    assert "[suggestion]" not in block
    assert "score: 10 / 10" in block
