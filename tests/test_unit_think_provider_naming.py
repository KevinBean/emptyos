"""Unit tests for think-provider naming in setup._build_think_provider_raw.

A provider's ``name`` is its identity everywhere downstream — billing
attribution, ability classification, the model pill, and `execute_compare`'s
variant dedup. The local-model branch used to hardcode ``provider_name="ollama"``,
which silently collapsed two distinct local models onto one identity.

Both directions are pinned: the existing "ollama" section must keep its name
(no regression for every shipped config, including emptyos.toml.example), and a
second local section must get a distinct one.
"""

import pytest

from emptyos.capabilities.setup import _build_think_provider_raw

OLLAMA_HOST = "http://localhost:11434"


class _Config:
    """Minimal Config stand-in — dotted-path lookups over a plain dict."""

    def __init__(self, raw: dict):
        self._raw = raw

    def get_section(self, path: str) -> dict:
        node = self._raw
        for part in path.split("."):
            node = node.get(part, {}) if isinstance(node, dict) else {}
        return node if isinstance(node, dict) else {}

    def get(self, path: str, default=None):
        node = self._raw
        for part in path.split("."):
            node = node.get(part) if isinstance(node, dict) else None
            if node is None:
                return default
        return node


def _cfg(**sections) -> _Config:
    return _Config({"capabilities": {"think": dict(sections)}})


def test_ollama_section_keeps_its_name():
    """No regression: the canonical section still resolves to "ollama"."""
    cfg = _cfg(ollama={"host": OLLAMA_HOST, "model": "qwen3.5-32k:latest"})
    p = _build_think_provider_raw("ollama", cfg)
    assert p is not None
    assert p.name == "ollama"
    assert p.model == "qwen3.5-32k:latest"


def test_second_local_model_gets_a_distinct_name():
    """Two local sections must not collapse onto one identity."""
    cfg = _cfg(
        ollama={"host": OLLAMA_HOST, "model": "qwen3.5-32k:latest"},
        qwythos={"host": OLLAMA_HOST, "model": "hf.co/some/other-gguf:Q4_K_M"},
    )
    a = _build_think_provider_raw("ollama", cfg)
    b = _build_think_provider_raw("qwythos", cfg)
    assert {a.name, b.name} == {"ollama", "qwythos"}
    assert a.model != b.model


def test_host_detected_local_section_uses_its_own_name():
    """A section detected by :11434 host alone is still named after the section."""
    cfg = _cfg(**{"local-alt": {"host": "http://127.0.0.1:11434", "model": "m"}})
    p = _build_think_provider_raw("local-alt", cfg)
    assert p is not None
    assert p.name == "local-alt"


@pytest.mark.parametrize("name", ["openai", "openai-mini", "openai-nano"])
def test_openai_tiers_keep_per_section_names(name):
    """The sibling convention this branch was aligned to — guards both at once."""
    cfg = _cfg(**{name: {"host": "https://api.openai.com", "model": "gpt-x"}})
    p = _build_think_provider_raw(name, cfg)
    assert p is not None
    assert p.name == name


def test_unknown_section_returns_none():
    """An unconfigured provider name builds nothing rather than a bogus default."""
    assert _build_think_provider_raw("not-configured", _cfg()) is None
