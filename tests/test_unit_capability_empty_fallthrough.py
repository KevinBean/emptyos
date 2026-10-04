"""Unit tests for the blank-completion fallthrough in Capability.execute.

A reasoning model that exhausts its token budget inside <think> returns EMPTY
content (ollama carries reasoning in a separate channel). The `think` chain must
treat that as a provider failure and fall through — not hand the caller "".
Other capabilities keep returning empty results (an empty file read is valid).

Pure — constructs a Capability with fake providers; no kernel, no daemon.
"""

from __future__ import annotations

import pytest

from emptyos.capabilities import Capability, Provider


class _Fake(Provider):
    def __init__(self, name, value, *, raises=False):
        self.name = name
        self._v = value
        self._raises = raises

    @property
    def is_cloud(self):
        return False

    async def available(self):
        return True

    async def execute(self, **kw):
        if self._raises:
            raise RuntimeError("boom")
        return self._v


def _think(providers):
    cap = Capability(providers=providers)
    cap.name = "think"
    return cap


@pytest.mark.asyncio
async def test_empty_completion_falls_through_to_next_provider():
    cap = _think([_Fake("runaway", ""), _Fake("good", "the answer")])
    r = await cap.execute(prompt="x")
    assert r.provider == "good"
    assert r.value == "the answer"


@pytest.mark.asyncio
async def test_whitespace_only_completion_is_also_blank():
    cap = _think([_Fake("runaway", "   \n\t "), _Fake("good", "real")])
    r = await cap.execute(prompt="x")
    assert r.provider == "good"


@pytest.mark.asyncio
async def test_all_empty_raises_not_silent_empty():
    cap = _think([_Fake("a", ""), _Fake("b", "")])
    with pytest.raises(RuntimeError) as ei:
        await cap.execute(prompt="x")
    assert "empty content" in str(ei.value)


@pytest.mark.asyncio
async def test_exception_then_empty_then_good_all_fall_through():
    cap = _think([_Fake("err", None, raises=True), _Fake("blank", ""), _Fake("good", "yes")])
    r = await cap.execute(prompt="x")
    assert r.provider == "good" and r.value == "yes"


@pytest.mark.asyncio
async def test_non_think_capability_returns_empty_normally():
    # An empty result from e.g. `read` (empty file) is valid — only `think`
    # treats blank as failure.
    cap = Capability(providers=[_Fake("fs", "")])
    cap.name = "read"
    r = await cap.execute(prompt="x")
    assert r.provider == "fs" and r.value == ""


@pytest.mark.asyncio
async def test_first_non_empty_wins():
    cap = _think([_Fake("good", "first"), _Fake("also", "second")])
    r = await cap.execute(prompt="x")
    assert r.provider == "good" and r.value == "first"
