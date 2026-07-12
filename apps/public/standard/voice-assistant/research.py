"""Aura — web research with spoken citations.

A voice-shaped wrapper around `apps/assistant/research.py`'s pipeline:
DuckDuckGo search → playwright per-source body extraction → LLM
synthesis with `[n]` citation markers. Returns a single `{say, card}`
intent payload (TTS-friendly short summary + a card carrying the full
report + numbered source list).

Extracted from `app.py` to keep the spine focused on lifecycle + chat
orchestration. Module-level functions are bound onto `VoiceAssistantApp`
in `app.py` per `.claude/rules/multi-module-apps.md`.

Reaches into other modules: `self.browse` (capability), `self.think_stream`
(capability). Do not import from `.app` (it imports us, which would cycle).

Reuses the search + reading + synthesis shape from
`apps/assistant/research.py`. Copy, not import — the assistant version
streams progress events to a websocket; Aura's surface is a single
intent return, so the integration shapes differ.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

from emptyos.sdk.web_search import ddg_search, site_label, source_fencer

if TYPE_CHECKING:
    from .app import VoiceAssistantApp  # noqa: F401 — for type hints only


# ─── Bind to VoiceAssistantApp class as ─────────────────────────────────
#   voice_research = _research.voice_research
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────────


DEFAULT_TOP_N = 3
DEFAULT_PER_PAGE_CHARS = 6_000
DEFAULT_PAGE_TIMEOUT_S = 20

# Shorter than assistant's report — TTS-friendly. The user hears the
# one-sentence summary spoken aloud; the full structured report lives
# in the card on screen.
RESEARCH_SYSTEM = (
    "You are a research synthesizer for a voice assistant. Given a user "
    "question + excerpts from N web sources, produce TWO outputs separated "
    "by a single line containing exactly `---`:\n\n"
    "1. A one-sentence spoken summary (max 28 words, plain prose, "
    "natural to hear aloud). Cite sources by saying 'source one', 'source two', "
    "etc. — never raw URLs.\n"
    "2. A structured written report (4–6 short paragraphs) for on-screen "
    "reading. Cite every claim with `[n]` referencing the numbered source list. "
    "If sources contradict, surface the disagreement. If sources don't cover "
    "the question, say so.\n\n"
    "Format strictly:\n"
    "SPOKEN_SENTENCE_HERE\n"
    "---\n"
    "STRUCTURED_REPORT_HERE\n"
)


async def voice_research(self, query: str = "") -> dict:
    """Voice intent — research a topic and return a TTS-friendly summary
    + on-screen card with numbered citations.

    The whole pipeline (search + read 3 sources + synthesize) blocks for
    ~10-15s. The orient preamble (`apps/voice-assistant/app.py::_orient`)
    speaks "Looking that up online" first so the user knows the wait is
    deliberate.
    """
    q = (query or "").strip()
    if not q:
        return {"say": "What would you like me to research?"}

    ctx_id = f"voice-research-{uuid.uuid4().hex[:8]}"
    try:
        # 1. Search.
        try:
            results = await asyncio.to_thread(ddg_search, q, DEFAULT_TOP_N)
        except ImportError:
            return {"say": "Research isn't available — the ddgs package is missing."}
        except Exception as e:
            return {"say": f"Couldn't search the web: {type(e).__name__}."}
        if not results:
            return {"say": "No results for that query."}

        # 2. Read each source.
        sources: list[dict] = []
        for i, hit in enumerate(results, start=1):
            try:
                await self.browse(
                    "navigate",
                    url=hit["url"],
                    context_id=ctx_id,
                    timeout_s=DEFAULT_PAGE_TIMEOUT_S,
                    wait="domcontentloaded",
                )
                page_snap = await self.browse("snapshot", context_id=ctx_id)
            except Exception:
                continue
            text = (page_snap.get("text") or "").strip()
            if not text:
                continue
            if len(text) > DEFAULT_PER_PAGE_CHARS:
                text = text[:DEFAULT_PER_PAGE_CHARS]
            sources.append({
                "n": len(sources) + 1,
                "url": hit["url"],
                "title": page_snap.get("title") or hit["title"],
                "text": text,
            })

        if not sources:
            return {"say": "I found results but couldn't read any of the pages."}

        # 3. Synthesize. Dark-flagged injection hardening (source_fencer
        # no-ops with the flag off → prompts stay byte-identical).
        fencer = source_fencer(self)
        system = fencer.system(RESEARCH_SYSTEM)
        prompt_parts = [f"User question: {q}", "", "Sources:"]
        for s in sources:
            label = site_label(s["url"])
            text = fencer.wrap(s["text"], label=label)
            prompt_parts.append(f"[{s['n']}] {s['title']} — {label}\n{text}")
        prompt_parts.append("")
        prompt_parts.append(
            "Write the spoken sentence then `---` then the structured report. "
            "Cite using `[1]` `[2]` etc. matching the sources above."
        )
        prompt = "\n\n".join(prompt_parts)

        full = ""
        try:
            async for chunk in self.think_stream(
                prompt=prompt, system=system, domain="text", temperature=0.4,
            ):
                if isinstance(chunk, dict) and chunk.get("text"):
                    full += chunk["text"]
        except Exception as e:
            return {"say": f"Couldn't synthesize: {type(e).__name__}."}

        spoken, _, report = full.partition("---")
        spoken = spoken.strip() or "Here's what I found."
        report = report.strip() or full.strip()

        card_data = {
            "query": q,
            "report": report,
            "sources": [
                {"n": s["n"], "url": s["url"], "title": s["title"]} for s in sources
            ],
        }
        return {
            "say": spoken,
            "card": {"renderer": "research-result", "title": q, "data": card_data},
        }
    finally:
        try:
            await asyncio.wait_for(self.browse("close", context_id=ctx_id), timeout=5)
        except Exception:
            pass
