"""Unit tests for model-ability gating — pure, no daemon required.

Covers the taxonomy (classify/meets/normalize) and the chain reorder that
routes think(min_ability=...) to a sufficiently-able provider.
See .claude/rules/model-ability.md.
"""

import pytest

from emptyos.capabilities import Capability
from emptyos.capabilities.ability import (
    DEFAULT_ABILITY,
    classify,
    meets,
    normalize,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "name,model,expected",
    [
        ("openai-nano", "gpt-4.1-nano", "weak"),
        ("ollama", "qwen2.5:1.5b", "weak"),
        ("ollama", "phi3:mini", "weak"),          # "mini" but small → weak wins
        ("openai-mini", "gpt-4o-mini", "standard"),  # "gpt-4o" but mini → standard
        ("ollama", "qwen2.5:7b", "standard"),
        ("claude", "claude-haiku", "standard"),
        ("openai", "gpt-4o", "strong"),
        ("openai", "gpt-5", "strong"),
        ("claude-cli", "", "strong"),
        ("ollama", "qwen2.5:72b", "strong"),
        ("mystery", "totally-unknown-model", "standard"),  # safe default
    ],
)
def test_classify(name, model, expected):
    assert classify(name, model) == expected


def test_meets_ordering():
    assert meets("strong", "standard") is True
    assert meets("standard", "standard") is True
    assert meets("standard", "strong") is False
    assert meets("weak", "standard") is False
    assert meets("weak", None) is True       # no requirement → always met
    assert meets(None, "strong") is False    # unknown active → standard < strong


def test_normalize():
    assert normalize("STRONG") == "strong"
    assert normalize("") == DEFAULT_ABILITY
    assert normalize("nonsense") == DEFAULT_ABILITY


class _StubProvider:
    def __init__(self, name, ability):
        self.name = name
        self._a = ability

    @property
    def ability(self):
        return self._a


def test_reorder_prefers_meeting_providers_without_reshuffle():
    weak = _StubProvider("w", "weak")
    std = _StubProvider("s", "standard")
    strong = _StubProvider("x", "strong")

    # min=strong → only strong meets; it moves first, rest keep order.
    r = Capability._reorder_by_ability([weak, std, strong], "strong")
    assert r[0] is strong and r[1:] == [weak, std]

    # min=standard → std + strong both meet; chain order preserved among them,
    # weak demoted. Crucially does NOT upgrade std→strong ordering.
    r = Capability._reorder_by_ability([std, strong, weak], "standard")
    assert r == [std, strong, weak]

    # none meets → original order unchanged (soft fallback).
    assert Capability._reorder_by_ability([weak], "strong") == [weak]

    # no min_ability → identical list (no-op).
    chain = [weak, std, strong]
    assert Capability._reorder_by_ability(chain, None) is chain
