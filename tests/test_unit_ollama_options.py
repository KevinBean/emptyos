"""Unit test for the Ollama options builder — num_ctx passthrough (the empty-output fix)."""

from __future__ import annotations

from emptyos.capabilities.providers.openai_compat import _ollama_options


def test_defaults_have_no_num_ctx():
    o = _ollama_options({})
    assert o["temperature"] == 0.7 and o["num_predict"] == 4096
    assert "num_ctx" not in o  # absent → Ollama uses the model default


def test_num_ctx_included_when_asked():
    o = _ollama_options({"num_ctx": 8192, "temperature": 0.3, "max_tokens": 2048})
    assert o["num_ctx"] == 8192 and o["temperature"] == 0.3 and o["num_predict"] == 2048


def test_num_ctx_coerced_to_int():
    assert _ollama_options({"num_ctx": "8192"})["num_ctx"] == 8192


def test_zero_or_none_num_ctx_omitted():
    assert "num_ctx" not in _ollama_options({"num_ctx": 0})
    assert "num_ctx" not in _ollama_options({"num_ctx": None})
