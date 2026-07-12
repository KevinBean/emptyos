"""Unit tests for emptyos.sdk.text_cleanup — pure, no daemon.

clean_dictation takes a think_fn closure, so we exercise every branch with a
fake think_fn (no model, no kernel). Run: python -m pytest tests/test_sdk_text_cleanup.py
"""

import asyncio

from emptyos.sdk.text_cleanup import CLEANUP_PROMPT, build_cleanup_system, clean_dictation


def _run(coro):
    return asyncio.run(coro)


def test_empty_returns_empty_without_calling_model():
    called = {"n": 0}

    async def fake(text, *, system="", domain="", temperature=0):
        called["n"] += 1
        return "SHOULD NOT RUN"

    assert _run(clean_dictation(fake, "   ")) == ""
    assert called["n"] == 0


def test_passthrough_strips_whitespace():
    async def fake(text, *, system="", domain="", temperature=0):
        return "  Cleaned up.  "

    assert _run(clean_dictation(fake, "raw text here")) == "Cleaned up."


def test_quote_wrapped_reply_unwrapped():
    async def fake(text, *, system="", domain="", temperature=0):
        return '"Quoted reply."'

    assert _run(clean_dictation(fake, "x")) == "Quoted reply."


def test_empty_model_reply_falls_back_to_input():
    async def fake(text, *, system="", domain="", temperature=0):
        return "   "

    assert _run(clean_dictation(fake, "keep me")) == "keep me"


def test_model_raise_falls_back_to_input():
    async def fake(text, *, system="", domain="", temperature=0):
        raise RuntimeError("model exploded")

    assert _run(clean_dictation(fake, "keep me")) == "keep me"


def test_vocabulary_injected_into_system_prompt():
    captured = {}

    async def fake(text, *, system="", domain="", temperature=0):
        captured["system"] = system
        return "ok"

    _run(clean_dictation(fake, "mention CAB-700502 and HDD", vocabulary="CAB-700502, HDD"))
    assert "CAB-700502" in captured["system"]
    assert "HDD" in captured["system"]


def test_think_fn_called_with_text_domain_temperature():
    captured = {}

    async def fake(text, *, system="", domain="", temperature=0):
        captured.update(text=text, domain=domain, temperature=temperature)
        return "ok"

    _run(clean_dictation(fake, "hello world"))
    assert captured["text"] == "hello world"
    assert captured["domain"] == "text"
    assert captured["temperature"] == 0.2


def test_build_cleanup_system_no_vocab_is_base_prompt():
    assert build_cleanup_system("") == CLEANUP_PROMPT
    assert build_cleanup_system("   ") == CLEANUP_PROMPT


def test_build_cleanup_system_with_vocab_extends_base():
    s = build_cleanup_system("Foo,\nBar")
    assert s.startswith(CLEANUP_PROMPT)
    assert "Foo" in s and "Bar" in s
    assert "PRESERVE" in s
