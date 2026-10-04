"""Unit tests for Capability._reorder_by_preference (prefer_provider routing).

A soft per-call hint: a device that can only play WAV asks for
prefer_provider=["kokoro"] and the chain tries kokoro first, without ever
making a call fail when the named provider is absent.
"""

from emptyos.capabilities import Capability


class _P:
    def __init__(self, name):
        self.name = name


def _names(ps):
    return [p.name for p in ps]


def _chain():
    return [_P("edge-tts"), _P("openai-tts"), _P("kokoro"), _P("human")]


def test_single_preference_moves_to_front():
    out = Capability._reorder_by_preference(_chain(), ["kokoro"])
    assert _names(out) == ["kokoro", "edge-tts", "openai-tts", "human"]


def test_string_preference_accepted():
    out = Capability._reorder_by_preference(_chain(), "kokoro")
    assert out[0].name == "kokoro"


def test_multiple_preference_order_preserved():
    out = Capability._reorder_by_preference(_chain(), ["kokoro", "openai-tts"])
    assert _names(out)[:2] == ["kokoro", "openai-tts"]


def test_unknown_preference_is_soft_noop():
    out = Capability._reorder_by_preference(_chain(), ["does-not-exist"])
    assert _names(out) == ["edge-tts", "openai-tts", "kokoro", "human"]


def test_none_and_empty_unchanged():
    assert _names(Capability._reorder_by_preference(_chain(), None)) == _names(_chain())
    assert _names(Capability._reorder_by_preference(_chain(), [])) == _names(_chain())


def test_no_providers_dropped():
    out = Capability._reorder_by_preference(_chain(), ["kokoro"])
    assert sorted(_names(out)) == sorted(_names(_chain()))
