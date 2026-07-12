"""Deep-loop — STORM-style gap-driven deepening over retrieved evidence.

A first-pass answer is produced from retrieved evidence; a model generates
follow-up questions targeting the GAPS in that answer; those follow-ups run
more retrieval rounds (concurrently); the rounds' sources merge into one
synthesis over a deduped union. The "research path" (the questions asked, with
how many sources each pulled) is returned for surfacing.

This module is the **loop control flow only** — pure, no kernel, no I/O. Each
consumer injects its own *round* (how to retrieve+synthesize one pass) and its
own *merge synthesis*; the round differs per app (multi-channel web for
``explore``, single-channel web + KB-oriented synthesis for ``kb-gap-miner``),
but the loop that drives them is shared. ``FOLLOWUP_SYSTEM`` (gap-question
generation) lives here because it IS the loop's essence; per-app *synthesis*
prompts stay in the apps.

Extracted from ``apps/public/labs/explore`` on its second consumer
(``apps/extension/dev/kb-gap-miner`` gap-resolution) per CLAUDE.md rule 9.
Pure → unit-tested without a daemon by injecting fake callables
(``tests/test_sdk_deep_loop.py``).

Consumers:
- ``explore._run`` — interactive web research (multi-channel round).
- ``kb-gap-miner._research_gap`` — research a detected KB gap (single-channel
  round + KB-note synthesis), dark-flagged ``feature.deep-resolve.enabled``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable

# The gap-question prompt — generic to the loop, shared by every consumer.
# A consumer's ``followups_fn`` typically calls ``self.think(..., system=FOLLOWUP_SYSTEM)``.
FOLLOWUP_SYSTEM = """You read a first-pass research answer and propose follow-up
questions that deepen the research (the STORM multi-perspective move).

Reply with ONLY this JSON shape:
{"questions": ["...", "..."]}

Rules:
- 2 to 4 questions. Each must target a GAP, an unverified claim, or a
  contradiction the answer itself surfaced — never restate the original question.
- Each question must be independently web-searchable: concrete nouns and
  specifics, not "tell me more", "what else", or "how does it compare".
- If the answer already covers the topic thoroughly, return {"questions": []}.
- No commentary outside the JSON."""

DEFAULT_SOURCE_LIMIT = 12


# A "round" result is a plain dict: {ok: bool, query: str, answer: str,
# sources: list[dict], ...}. The loop only reads ``ok``/``answer``/``sources``/
# ``query`` — any extra keys (skipped, etc.) pass through untouched on ``first``.
RoundFn = Callable[[str], Awaitable[dict]]
FollowupsFn = Callable[[str, str], Awaitable[list]]
DedupeFn = Callable[[list], list]
MergeFn = Callable[[str, list], Awaitable[str]]


@dataclass
class DeepLoopResult:
    """Outcome of a deepening run.

    ``ok`` mirrors the first round — a failed first round short-circuits the
    loop and the caller returns ``first`` verbatim (preserving its error/skipped
    payload). On success: ``rounds`` is every successful round (first + the
    follow-up sub-rounds that returned readable sources), ``answer``/``sources``
    are the merged result (or the first round's when only one round ran), and
    ``followups`` is the research path — each asked question paired with how many
    sources it pulled (0 = ran but every page was unreadable / a dupe).
    """

    ok: bool
    first: dict
    rounds: list = field(default_factory=list)
    answer: str = ""
    sources: list = field(default_factory=list)
    followups: list = field(default_factory=list)  # [{"question": str, "sources": int}]


def dedupe_sources(rounds: list, *, limit: int = DEFAULT_SOURCE_LIMIT) -> list:
    """Union of every round's sources, deduped by url (then title), first
    occurrence wins, capped at ``limit``. Pure — unit-tested without a daemon."""
    out: list = []
    seen: set = set()
    for r in rounds:
        for s in (r.get("sources") if isinstance(r, dict) else None) or []:
            key = (s.get("url") or "").strip() or (s.get("title") or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(s)
            if len(out) >= limit:
                return out
    return out


async def deepen(
    query: str,
    *,
    round_fn: RoundFn,
    followups_fn: FollowupsFn,
    merge_fn: MergeFn,
    dedupe_fn: DedupeFn = dedupe_sources,
    depth: int,
    breadth: int,
) -> DeepLoopResult:
    """Run the gap-driven deepening loop.

    - ``round_fn(q)`` → one retrieval+synthesis pass, ``{ok, query, answer, sources}``.
    - ``followups_fn(query, prior_answer)`` → gap-targeting questions (``list[str]``).
    - ``merge_fn(query, union_sources)`` → a single synthesis over the merged sources.
    - ``dedupe_fn(rounds)`` → the deduped union of all rounds' sources.
    - ``depth`` extra rounds after the first (0 = single pass); ``breadth`` caps
      follow-ups fanned out per round.

    Behaviour is ``explore._run``'s loop verbatim: a failed first round returns
    immediately (``ok=False``); ``depth==0`` yields a single-pass result identical
    to the first round; multi-round runs one final ``merge_fn`` over the deduped
    union (falling back to the first round's answer if the merge raises, so a run
    is never lost). Sub-round failures are swallowed (the round just drops out).
    """
    first = await round_fn(query)
    if not isinstance(first, dict) or not first.get("ok"):
        return DeepLoopResult(ok=False, first=first if isinstance(first, dict) else {})

    rounds = [first]
    seen_qs = {query.lower()}
    followups_asked: list[str] = []
    for _ in range(max(0, depth)):
        prior = "\n\n".join(r.get("answer", "") for r in rounds)
        raw = await followups_fn(query, prior)
        followups = [
            q for q in (raw or [])
            if isinstance(q, str) and q.strip() and q.lower() not in seen_qs
        ][: max(1, breadth)]
        if not followups:
            break
        seen_qs.update(q.lower() for q in followups)
        followups_asked.extend(followups)
        subs = await asyncio.gather(
            *[round_fn(q) for q in followups],
            return_exceptions=True,
        )
        rounds.extend(r for r in subs if isinstance(r, dict) and r.get("ok"))

    # Single round → identical to the pre-loop path. Multi-round → one final
    # synthesis over the deduped union so [n] citations stay consistent against
    # the merged source list.
    if len(rounds) > 1:
        union = dedupe_fn(rounds)
        try:
            answer = await merge_fn(query, union)
            sources = union
        except Exception:
            answer, sources = first.get("answer", ""), first.get("sources", [])
    else:
        answer, sources = first.get("answer", ""), first.get("sources", [])

    # Research path: each asked follow-up + the readable sources it pulled.
    round_by_q = {(r.get("query") or "").lower(): r for r in rounds[1:]}
    followups_out = [
        {"question": q, "sources": len((round_by_q.get(q.lower()) or {}).get("sources") or [])}
        for q in followups_asked
    ]

    return DeepLoopResult(
        ok=True,
        first=first,
        rounds=rounds,
        answer=answer,
        sources=sources,
        followups=followups_out,
    )
