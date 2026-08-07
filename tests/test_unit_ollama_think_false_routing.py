"""Which Ollama models must bypass the OpenAI-compat shim (think:false).

Ollama carries a reasoning model's ``<think>`` block in a field the
``/v1/chat/completions`` shim drops. If such a model spends its budget
reasoning, the response arrives with ``content=""`` — no exception, no error,
just an expensive silence. The provider therefore routes those models to the
native ``/api/chat`` endpoint with ``think:false``.

The gate used to be the literal substring ``"qwen3"``, which silently missed
``qwythos-32k`` — a Qwen3.5 finetune whose tag shares no substring with its
base. Measured on the model-bench code/js-exec prompt (2026-07-24):

    qwythos-32k via /v1/   ->  0 chars, 32,545 tokens, 387s
    qwythos-32k native     ->  632 chars (gradeable code), 4.3s

A trivial prompt does NOT reproduce it — short reasoning terminates and the
content survives — which is why it went unnoticed and why these tests assert on
the routing decision rather than on a live call.

Pure: constructs providers and reads a property. No network, no daemon, no
kernel import.
"""

from __future__ import annotations

import pytest

from emptyos.capabilities.providers.openai_compat import (
    _OLLAMA_REASONING_FAMILIES,
    OpenAICompatThinkProvider,
)

OLLAMA = "http://localhost:11434"


def _p(model: str, host: str = OLLAMA, name: str = "") -> OpenAICompatThinkProvider:
    return OpenAICompatThinkProvider(host=host, model=model, provider_name=name)


@pytest.mark.parametrize("model", [
    "qwythos-32k:latest",     # the regression this file exists for
    "qwen3.5-32k:latest",     # the configured local default
    "qwen3.5:latest",
    "qwen3:8b",
    "deepseek-r1:14b",
    "qwq:32b",
])
def test_reasoning_models_route_to_native(model):
    assert _p(model)._needs_think_false is True


@pytest.mark.parametrize("model", [
    "gemma4:e4b-it-q4_K_M",   # verified to return content fine over the shim
    "llama3.2:latest",
    "mistral:7b",
])
def test_non_reasoning_ollama_models_stay_on_compat(model):
    assert _p(model)._needs_think_false is False


@pytest.mark.parametrize("model,host", [
    ("gpt-5.4-mini", "https://api.openai.com"),
    ("deepseek/deepseek-v4-flash", "https://openrouter.ai/api"),
    # A cloud-hosted model whose NAME matches a reasoning family must still not
    # be routed to ollama's native endpoint — the endpoint would not exist.
    ("deepseek/deepseek-r1", "https://openrouter.ai/api"),
    ("qwen/qwen3.5-flash-02-23", "https://openrouter.ai/api"),
])
def test_non_ollama_hosts_never_route_to_native(model, host):
    assert _p(model, host=host)._needs_think_false is False


def test_ollama_detected_by_provider_name_not_only_port():
    """`_is_ollama` accepts either the 11434 port or the provider name."""
    p = _p("qwen3.5:latest", host="http://gpu-box.local:9999", name="ollama")
    assert p._is_ollama is True
    assert p._needs_think_false is True


def test_substring_matching_covers_version_suffixes():
    """`qwen3` must cover `qwen3.5-*`; that prefix behaviour is load-bearing."""
    assert "qwen3" in _OLLAMA_REASONING_FAMILIES
    assert _p("qwen3.5-32k:latest")._needs_think_false is True


def test_qwythos_is_registered_as_its_own_family():
    """It shares no substring with its Qwen3.5 base — the whole bug."""
    assert "qwythos" in _OLLAMA_REASONING_FAMILIES
    assert not any(
        fam in "qwythos-32k:latest"
        for fam in _OLLAMA_REASONING_FAMILIES if fam != "qwythos"
    ), "qwythos would have been caught by another family — the test is vacuous"


def test_empty_or_missing_model_does_not_crash():
    assert _p("")._needs_think_false is False
