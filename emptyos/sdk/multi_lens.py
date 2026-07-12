"""Multi-lens analysis — one LLM call, N named lenses, optional synthesis.

The cheap default for "I want N perspectives on a question." Empirically a
single multi-lens prompt produces ~the same content as a 9-call persona
panel (`rooms.run_panel`) when the panel seats share an underlying model,
at ~1/9 the cost and far higher reliability. Use this whenever:

- You want multiple framings of one question
- All seats would run on the same model anyway
- You don't need the legible turn-by-turn conversational artifact

When you DO need a real conversation (heterogeneous models per seat, or a
debate you can watch unfold in a room UI), use `rooms.run_panel` instead.

Example::

    from emptyos.sdk.multi_lens import multi_lens_analyze
    res = await multi_lens_analyze(self,
        question="Is this release ready to ship?",
        lenses=[
            {"name": "QA",       "focus": "test coverage, regressions, edge cases"},
            {"name": "Security", "focus": "auth, data exposure, dependency CVEs"},
            {"name": "Product",  "focus": "user-visible impact, rollback story"},
        ],
        synthesize=True,
    )
    print(res["analysis"])
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from emptyos.sdk.base_app import BaseApp  # noqa: F401


_DEFAULT_SYSTEM = (
    "You are a neutral analyst. You can voice multiple disciplinary "
    "perspectives in turn without favouring any. You give concrete, "
    "decisive judgments and you do not flatter."
)

_PROMPT_TEMPLATE = (
    "{question}\n\n"
    "Analyze through these lenses — keep each lens brief and concrete:\n\n"
    "{lens_block}"
    "{synthesis_block}"
    "{footer}"
)

_SYNTHESIS_BLOCK = (
    "\n\nThen synthesize:\n"
    "- AGREEMENT across the lenses\n"
    "- KEY DISAGREEMENT (and which lens is on which side)\n"
    "- A single concrete RECOMMENDATION\n"
)

_FOOTER = (
    "\nBe decisive and brief. No flattery. "
    "Do not invent facts you cannot ground in the question or the lens framings."
)

# Analysis-range default per CLAUDE.md §Development Rules 12.
# Parsing 0.1–0.3, analysis 0.3–0.5, creative 0.6–0.8.
_DEFAULT_TEMPERATURE = 0.4


async def multi_lens_analyze(
    app: "BaseApp",
    question: str,
    lenses: list[dict],
    *,
    synthesize: bool = True,
    system: str = "",
    model: str = "",
    temperature: float | None = None,
) -> dict:
    """Ask one model to analyze ``question`` through every lens, then optionally
    synthesize agreement / disagreement / recommendation.

    Args:
        app: The calling app (any ``BaseApp`` — uses ``app.think``).
        question: The thing to analyze.
        lenses: List of ``{"name": <str>, "focus": <str>}``. ``name`` is the
            lens label ("A&R", "Security", "Marcus Bell"). ``focus`` is one
            short line telling the model what that lens cares about.
        synthesize: If True (default), the model also produces an
            agreement/disagreement/recommendation block after the per-lens
            analyses. Set False for raw lens-by-lens output only.
        system: Optional system prompt. Default is a neutral analyst framing.
        model: Optional provider override (passed through to ``app.think``).
        temperature: Optional temperature override.

    Returns: ``{"question", "lenses": [names], "analysis": <str>,
                "synthesized": bool}``.
    """
    if not question or not question.strip():
        return {"error": "question required"}
    if not isinstance(lenses, list) or len(lenses) < 2:
        return {"error": "at least 2 lenses required (otherwise just call think)"}

    # Validate + normalize lens entries.
    normalized: list[dict] = []
    for i, L in enumerate(lenses):
        if not isinstance(L, dict):
            return {"error": f"lens {i} must be a dict with name + focus"}
        name = (L.get("name") or "").strip()
        focus = (L.get("focus") or "").strip()
        if not name or not focus:
            return {"error": f"lens {i} missing name or focus"}
        normalized.append({"name": name, "focus": focus})

    lens_block = "\n".join(
        f"({i + 1}) {L['name'].upper()} — {L['focus']}"
        for i, L in enumerate(normalized)
    )

    prompt = _PROMPT_TEMPLATE.format(
        question=question,
        lens_block=lens_block,
        synthesis_block=_SYNTHESIS_BLOCK if synthesize else "",
        footer=_FOOTER,
    )

    kwargs: dict = {
        "domain": "text",
        "system": system or _DEFAULT_SYSTEM,
        "temperature": temperature if temperature is not None else _DEFAULT_TEMPERATURE,
    }
    if model:
        kwargs["model"] = model

    analysis = await app.think(prompt, **kwargs)
    return {
        "question": question,
        "lenses": [L["name"] for L in normalized],
        "analysis": analysis,
        "synthesized": synthesize,
    }
