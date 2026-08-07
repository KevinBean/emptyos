"""A benchmark provider that returns NOTHING must not be scored as a success.

`execute_compare` set ``error=None`` for any provider whose ``execute()`` did not
raise — so a blank completion landed in the results as a pass with an empty
preview, and the compare view / leaderboard counted it as a working answer.

Real case (2026-07-20): a local reasoning model exhausted its token budget inside
its ``<think>`` block and returned ``content=""`` after ~6 minutes. ollama carries
reasoning in a separate field, so nothing raised and nothing was logged — the run
looked identical to a terse correct answer.

Empty is a failure here. `execute_compare` exists only for benchmarking, where
"the model said nothing" is never the right answer.
"""

from __future__ import annotations

import pytest

from emptyos.capabilities import Capability


class _Provider:
    """Minimal benchmark provider returning a canned value."""

    is_cloud = False
    at_capacity = False
    ability = "standard"

    def __init__(self, name: str, value):
        self.name = name
        self._value = value
        self._current_load = 0

    @property
    def variant_id(self) -> str:
        return self.name

    @property
    def variant_meta(self) -> dict:
        return {"provider": self.name}

    async def available(self) -> bool:
        return True

    async def execute(self, **kwargs):
        return self._value


class _Wrapped:
    """Stand-in for a CapabilityResult — carries the text on `.value`."""

    def __init__(self, value):
        self.value = value


def _capability(providers) -> Capability:
    cap = Capability.__new__(Capability)
    cap.name = "think"
    cap.providers = list(providers)
    cap._domains = {}
    cap._buckets = {}
    cap.consent_manager = None
    return cap


def _by_name(results) -> dict:
    return {r["provider"]: r for r in results}


@pytest.mark.asyncio
async def test_empty_string_is_reported_as_an_error():
    cap = _capability([_Provider("runaway", "")])
    got = _by_name(await cap.execute_compare(prompt="x"))
    assert got["runaway"]["error"] == "empty response (no content returned)"


@pytest.mark.asyncio
@pytest.mark.parametrize("blank", ["", "   ", "\n\n", None])
async def test_all_blank_shapes_are_errors(blank):
    cap = _capability([_Provider("p", blank)])
    got = _by_name(await cap.execute_compare(prompt="x"))
    assert got["p"]["error"] is not None


@pytest.mark.asyncio
async def test_blank_inside_a_result_wrapper_is_also_caught():
    """The value may be a CapabilityResult; unwrap before judging emptiness."""
    cap = _capability([_Provider("wrapped", _Wrapped("  "))])
    got = _by_name(await cap.execute_compare(prompt="x"))
    assert got["wrapped"]["error"] is not None


@pytest.mark.asyncio
async def test_real_answers_are_untouched():
    """No regression: a normal completion still scores clean, wrapped or not."""
    cap = _capability([
        _Provider("plain", "42"),
        _Provider("wrapped", _Wrapped("42")),
        _Provider("terse", "0"),  # falsy-looking but a real answer
    ])
    got = _by_name(await cap.execute_compare(prompt="x"))
    assert [got[n]["error"] for n in ("plain", "wrapped", "terse")] == [None, None, None]
    assert got["plain"]["response"] == "42"


@pytest.mark.asyncio
async def test_raising_provider_still_reports_its_own_error():
    """The pre-existing failure path must not be shadowed by the empty check."""

    class _Boom(_Provider):
        async def execute(self, **kwargs):
            raise RuntimeError("model refused")

    cap = _capability([_Boom("boom", None)])
    got = _by_name(await cap.execute_compare(prompt="x"))
    assert "model refused" in got["boom"]["error"]
