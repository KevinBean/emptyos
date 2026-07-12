"""Research mode — multi-step browse + synthesize with citations.

Composes existing primitives: ``ddgs`` (DuckDuckGo lite API) for getting result
URLs, ``self.browse()`` (playwright provider) for per-source body extraction,
``self.think_stream()`` for the synthesized report. Streams stage events back
so the chat UI can show progress instead of a long blocking pause.

Search backend rationale: the public ``html.duckduckgo.com/html/`` endpoint
gates headless browsers behind an anti-bot challenge (anomaly.js), so any
attempt to scrape it via playwright hangs or returns an empty result set.
The ``ddgs`` package speaks the lite JSON API instead — same engine, designed
for programmatic use, no challenge — and is the path the library's own
maintainers recommend over HTML scraping.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

from emptyos.sdk.web_search import (
    ddg_search,
    read_web_source,
    site_label,
    source_fencer,
)

DEFAULT_TOP_N = 5
DEFAULT_PER_PAGE_CHARS = 8_000
DEFAULT_PAGE_TIMEOUT_S = 20

RESEARCH_SYSTEM = (
    "You are a research synthesizer. Given a user question plus excerpts from "
    "N web sources, write a structured report (5–8 short paragraphs) that "
    "answers the question.\n\n"
    "Rules:\n"
    "- Cite every claim with `[n]` referencing the numbered source list.\n"
    "- If sources contradict, surface the disagreement, don't paper over it.\n"
    "- If sources don't cover the question, say so explicitly — don't invent.\n"
    "- Don't repeat the source URLs in your prose; just use `[n]` markers.\n"
    "- Open with a 1-sentence answer summary, then the supporting paragraphs.\n"
    "- No preamble like 'Here is a report'; start with the answer."
)


async def run_research(
    app,
    query: str,
    *,
    top_n: int = DEFAULT_TOP_N,
    per_page_chars: int = DEFAULT_PER_PAGE_CHARS,
    page_timeout_s: float = DEFAULT_PAGE_TIMEOUT_S,
    is_cancelled=None,
) -> AsyncIterator[dict]:
    """Stream research events back to the assistant's WS loop.

    Event shapes (dicts yielded):
      {"type": "research-status", "stage": "searching"}
      {"type": "research-status", "stage": "found", "n": int}
      {"type": "research-status", "stage": "reading", "i": int, "n": int, "url": str, "title": str}
      {"type": "research-status", "stage": "read-failed", "i": int, "url": str, "error": str}
      {"type": "research-status", "stage": "synthesizing"}
      {"type": "research-text", "text": str}          # streaming model output
      {"type": "research-citations", "sources": [{n, url, title}, ...]}
      {"type": "research-error", "message": str}

    ``is_cancelled`` — optional zero-arg callable returning truthy when the
    caller (typically the assistant WS handler) wants the run to abort. Checked
    between every navigate / synthesis chunk so orphaned runs from a dropped
    WS don't keep burning cloud-LLM cost + holding playwright pages.
    """
    q = (query or "").strip()
    if not q:
        yield {"type": "research-error", "message": "Empty query — try `/research <question>`"}
        return

    def _cancelled() -> bool:
        return bool(is_cancelled and is_cancelled())

    # Per-research playwright context so search-engine cookies don't leak into
    # the next user's research turn.
    ctx_id = f"research-{uuid.uuid4().hex[:8]}"

    try:
        # 1. Search via the ddgs lite API (HTML endpoint is gated by an
        # anti-bot challenge). Runs in a thread so the daemon's event loop
        # stays free during the HTTP fetch.
        yield {"type": "research-status", "stage": "searching"}
        if _cancelled():
            return
        try:
            results = await asyncio.to_thread(ddg_search, q, top_n)
        except ImportError:
            yield {
                "type": "research-error",
                "message": (
                    "Search step failed: ddgs package missing. "
                    "Install with `pip install ddgs`."
                ),
            }
            return
        except Exception as e:
            yield {
                "type": "research-error",
                "message": f"Search step failed: {type(e).__name__}: {e}",
            }
            return
        if not results:
            yield {
                "type": "research-error",
                "message": "No results returned for the query.",
            }
            return
        yield {"type": "research-status", "stage": "found", "n": len(results)}

        # 2. Read each source.
        sources: list[dict] = []
        for i, hit in enumerate(results, start=1):
            if _cancelled():
                return
            yield {
                "type": "research-status",
                "stage": "reading",
                "i": i,
                "n": len(results),
                "url": hit["url"],
                "title": hit["title"],
            }
            # Shared reader: navigates + snapshots + clamps, and gates the URL
            # through is_public_web_url so a result can't steer the server-side
            # browser at loopback/private services. Passing ctx_id keeps the
            # one-context-per-research-run cookie isolation; teardown stays in
            # this function's finally.
            res = await read_web_source(
                app,
                hit["url"],
                per_page_chars=per_page_chars,
                timeout_s=page_timeout_s,
                context_id=ctx_id,
            )
            if not res.get("ok"):
                if res.get("error") != "no readable text":
                    yield {
                        "type": "research-status",
                        "stage": "read-failed",
                        "i": i,
                        "url": hit["url"],
                        "error": res.get("error", "read failed"),
                    }
                continue
            sources.append(
                {
                    "n": len(sources) + 1,
                    "url": hit["url"],
                    "title": res.get("title") or hit["title"],
                    "text": res["text"],
                }
            )

        if not sources:
            yield {
                "type": "research-error",
                "message": "Every result failed to load or returned empty text.",
            }
            return
        if _cancelled():
            return

        # 3. Synthesize. Dark-flagged injection hardening (source_fencer
        # no-ops with the flag off → prompts stay byte-identical).
        yield {"type": "research-status", "stage": "synthesizing"}
        fencer = source_fencer(app)
        system = fencer.system(RESEARCH_SYSTEM)
        prompt_parts = [f"User question: {q}", ""]
        prompt_parts.append("Sources:")
        for s in sources:
            label = site_label(s["url"])
            text = fencer.wrap(s["text"], label=label)
            prompt_parts.append(f"[{s['n']}] {s['title']} — {label}\n{text}")
        prompt_parts.append("")
        prompt_parts.append(
            "Write the structured report now. Cite using `[1]` `[2]` etc. matching the sources above."
        )
        prompt = "\n\n".join(prompt_parts)

        async for chunk in app.think_stream(
            prompt=prompt, system=system, domain="text", temperature=0.4
        ):
            if _cancelled():
                return
            if not isinstance(chunk, dict):
                continue
            if "text" in chunk and chunk.get("text"):
                yield {"type": "research-text", "text": chunk["text"]}

        # 4. Citations.
        yield {
            "type": "research-citations",
            "sources": [
                {"n": s["n"], "url": s["url"], "title": s["title"]} for s in sources
            ],
        }
    finally:
        # Best-effort context teardown. Always runs — even if the orchestrator
        # aborts via is_cancelled — so a stuck navigate can't leak a page.
        try:
            await asyncio.wait_for(app.browse("close", context_id=ctx_id), timeout=5)
        except Exception:
            pass
