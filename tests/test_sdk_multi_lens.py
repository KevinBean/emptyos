"""Unit tests for emptyos.sdk.multi_lens — one-call multi-perspective analysis.

Pure unit tests — no daemon, no kernel. Uses a fake ``app`` with a captured
async ``think`` so we can assert on prompt shape + kwargs.
"""

from __future__ import annotations

import pytest

from emptyos.sdk.multi_lens import (
    _DEFAULT_SYSTEM,
    _DEFAULT_TEMPERATURE,
    _FOOTER,
    _SYNTHESIS_BLOCK,
    multi_lens_analyze,
)


class _FakeApp:
    """Captures the prompt + kwargs handed to ``think`` and returns a canned
    response, so tests can introspect exactly what the helper sent."""

    def __init__(self, response: str = "FAKE_ANALYSIS"):
        self.response = response
        self.last_prompt: str | None = None
        self.last_kwargs: dict | None = None
        self.call_count = 0

    async def think(self, prompt: str, **kwargs):
        self.last_prompt = prompt
        self.last_kwargs = kwargs
        self.call_count += 1
        return self.response


_TWO_LENSES = [
    {"name": "QA",       "focus": "test coverage and regressions"},
    {"name": "Security", "focus": "auth and data exposure"},
]


# ── Happy path ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_returns_analysis_and_metadata():
    app = _FakeApp()
    res = await multi_lens_analyze(app, "Ship it?", _TWO_LENSES)
    assert res == {
        "question": "Ship it?",
        "lenses": ["QA", "Security"],
        "analysis": "FAKE_ANALYSIS",
        "synthesized": True,
    }
    assert app.call_count == 1


@pytest.mark.asyncio
async def test_prompt_contains_question_and_uppercased_lens_names():
    app = _FakeApp()
    await multi_lens_analyze(app, "Ship it?", _TWO_LENSES)
    p = app.last_prompt or ""
    assert "Ship it?" in p
    # Lens names are upper-cased in the rendered block.
    assert "(1) QA" in p
    assert "(2) SECURITY" in p
    # Foci appear verbatim.
    assert "test coverage and regressions" in p
    assert "auth and data exposure" in p


@pytest.mark.asyncio
async def test_synthesis_block_present_by_default():
    app = _FakeApp()
    await multi_lens_analyze(app, "Q", _TWO_LENSES)
    assert _SYNTHESIS_BLOCK.strip() in (app.last_prompt or "")


@pytest.mark.asyncio
async def test_synthesis_block_omitted_when_synthesize_false():
    app = _FakeApp()
    res = await multi_lens_analyze(app, "Q", _TWO_LENSES, synthesize=False)
    assert res["synthesized"] is False
    assert "AGREEMENT" not in (app.last_prompt or "")
    assert "DISAGREEMENT" not in (app.last_prompt or "")


@pytest.mark.asyncio
async def test_footer_with_negative_clause_always_present():
    app = _FakeApp()
    await multi_lens_analyze(app, "Q", _TWO_LENSES, synthesize=False)
    assert _FOOTER.strip() in (app.last_prompt or "")
    # The Rule-12 negative-example clause must survive even minus synthesis.
    assert "Do not invent" in (app.last_prompt or "")


# ── Defaults ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_default_system_is_neutral_analyst():
    app = _FakeApp()
    await multi_lens_analyze(app, "Q", _TWO_LENSES)
    assert (app.last_kwargs or {}).get("system") == _DEFAULT_SYSTEM


@pytest.mark.asyncio
async def test_default_temperature_is_analysis_range():
    app = _FakeApp()
    await multi_lens_analyze(app, "Q", _TWO_LENSES)
    # 0.3-0.5 is the analysis band per CLAUDE.md Rule 12.
    temp = (app.last_kwargs or {}).get("temperature")
    assert temp == _DEFAULT_TEMPERATURE == 0.4


@pytest.mark.asyncio
async def test_domain_is_text():
    app = _FakeApp()
    await multi_lens_analyze(app, "Q", _TWO_LENSES)
    assert (app.last_kwargs or {}).get("domain") == "text"


@pytest.mark.asyncio
async def test_no_model_kwarg_when_not_provided():
    app = _FakeApp()
    await multi_lens_analyze(app, "Q", _TWO_LENSES)
    # Absent so the provider chain picks up its own default.
    assert "model" not in (app.last_kwargs or {})


# ── Overrides ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_caller_can_override_system_model_temperature():
    app = _FakeApp()
    await multi_lens_analyze(
        app, "Q", _TWO_LENSES,
        system="custom system",
        model="openai-mini",
        temperature=0.7,
    )
    kw = app.last_kwargs or {}
    assert kw["system"] == "custom system"
    assert kw["model"] == "openai-mini"
    assert kw["temperature"] == 0.7


# ── Validation ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rejects_empty_question():
    app = _FakeApp()
    res = await multi_lens_analyze(app, "", _TWO_LENSES)
    assert res.get("error")
    assert app.call_count == 0


@pytest.mark.asyncio
async def test_rejects_whitespace_only_question():
    app = _FakeApp()
    res = await multi_lens_analyze(app, "   \n  ", _TWO_LENSES)
    assert res.get("error")
    assert app.call_count == 0


@pytest.mark.asyncio
async def test_rejects_fewer_than_two_lenses():
    app = _FakeApp()
    res = await multi_lens_analyze(app, "Q", [_TWO_LENSES[0]])
    assert res.get("error") and "2 lenses" in res["error"]
    assert app.call_count == 0


@pytest.mark.asyncio
async def test_rejects_lens_missing_name():
    app = _FakeApp()
    res = await multi_lens_analyze(
        app, "Q", [{"focus": "x"}, {"name": "B", "focus": "y"}],
    )
    assert res.get("error")
    assert app.call_count == 0


@pytest.mark.asyncio
async def test_rejects_lens_missing_focus():
    app = _FakeApp()
    res = await multi_lens_analyze(
        app, "Q", [{"name": "A"}, {"name": "B", "focus": "y"}],
    )
    assert res.get("error")
    assert app.call_count == 0


@pytest.mark.asyncio
async def test_rejects_non_dict_lens_entry():
    app = _FakeApp()
    res = await multi_lens_analyze(
        app, "Q", ["just a string", {"name": "B", "focus": "y"}],  # type: ignore[list-item]
    )
    assert res.get("error")
    assert app.call_count == 0
