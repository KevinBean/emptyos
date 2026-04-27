"""Per-site corpus fetcher + cache + simple FAQ matcher.

The corpus is the JSON file emitted by the EmptyOS publish app's builder
(apps/publish/builder.py:_emit_corpus). Shape:

    {
      "site_name": "...", "site_description": "...", "domain": "...",
      "generated_at": "...",
      "chunks": [{id, type, slug, title, section, tags, url, text}, ...],
      "faqs":   [{q, a}, ...]
    }

Slice 2 prompt-stuffs the whole chunks blob (capped) into the system prompt.
Slice 2.5 will swap retrieval to embedding-based RAG; the chunks shape is
already chunk-shaped to make that swap mechanical.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

import httpx


# Hard cap on how many chars of corpus we'll stuff into the system prompt.
# At ~4 chars/token, 32k chars ≈ 8k tokens — safe for prompt caching and
# leaves room for the user message + response. Truncate by recency-of-chunks
# (preserve insertion order; first chunks win) when exceeded.
_MAX_CORPUS_CHARS = 32_000


@dataclass
class CorpusEntry:
    payload: dict
    fetched_at: float = field(default_factory=time.time)

    def stale(self, ttl_seconds: int) -> bool:
        return (time.time() - self.fetched_at) > ttl_seconds


class CorpusCache:
    def __init__(self, ttl_seconds: int = 3600) -> None:
        self.ttl = ttl_seconds
        self._entries: dict[str, CorpusEntry] = {}

    async def get(self, site_id: str, url: str) -> dict:
        entry = self._entries.get(site_id)
        if entry and not entry.stale(self.ttl):
            return entry.payload
        payload = await self._fetch(url)
        self._entries[site_id] = CorpusEntry(payload=payload)
        return payload

    def invalidate(self, site_id: str) -> None:
        self._entries.pop(site_id, None)

    async def _fetch(self, url: str) -> dict:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()


def stuff_corpus(payload: dict, max_chars: int = _MAX_CORPUS_CHARS) -> str:
    """Concatenate chunks into a system-prompt-shaped block, capped.

    Format:
      id: <chunk-id>
      title: TITLE — section
      url: <url>
      text...

    The chunk `id` is rendered explicitly (and first) so the model can cite
    it verbatim in the SOURCES block. Without this the model can only see
    title/section and would have to guess the canonical id format.

    Truncation is first-N (preserves recency since builder emits posts newest-first).
    """
    chunks = payload.get("chunks", []) or []
    blocks: list[str] = []
    total = 0
    for c in chunks:
        cid = c.get("id", "")
        title = c.get("title", "")
        section = c.get("section", "")
        url = c.get("url", "")
        text = c.get("text", "")
        header_bits = [b for b in (title, section) if b]
        header = " — ".join(header_bits)
        block = (
            f"id: {cid}\n"
            f"title: {header}\n"
            f"url: {url}\n"
            f"{text}"
        )
        if total + len(block) > max_chars and blocks:
            break
        blocks.append(block)
        total += len(block) + 2  # blank line separator
    return "\n\n".join(blocks)


# ── FAQ matcher ─────────────────────────────────────────────────────

_WORD_RE = re.compile(r"[A-Za-z0-9']+")


def _tokens(text: str) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(text)}


def match_faq(query: str, faqs: list[dict], threshold: float = 0.6) -> dict | None:
    """Return the best FAQ match if Jaccard similarity ≥ threshold, else None.

    Pre-bake hit. Free, instant, catches repeat questions before the LLM call.
    Threshold of 0.6 is conservative — false positives would feel weird, false
    negatives just fall through to the model.
    """
    if not faqs:
        return None
    qtokens = _tokens(query)
    if not qtokens:
        return None
    best = None
    best_score = 0.0
    for faq in faqs:
        ftokens = _tokens(faq.get("q", ""))
        if not ftokens:
            continue
        inter = len(qtokens & ftokens)
        union = len(qtokens | ftokens)
        score = inter / union if union else 0.0
        if score > best_score:
            best_score = score
            best = faq
    if best and best_score >= threshold:
        return best
    return None


def match_curated(query: str, entries: list[dict], threshold: float = 0.7) -> dict | None:
    """Match query against a list of curated Q&A entries.

    Stricter threshold than FAQs (0.7 vs 0.6) because curated entries grow
    organically and may include narrower phrasings; we want only confident
    matches to bypass the LLM. Each entry is a qa_log row dict
    ({id, query, reply, sources, ...}); we score against `query`.
    """
    if not entries:
        return None
    qtokens = _tokens(query)
    if not qtokens:
        return None
    best = None
    best_score = 0.0
    for e in entries:
        etokens = _tokens(e.get("query", ""))
        if not etokens:
            continue
        inter = len(qtokens & etokens)
        union = len(qtokens | etokens)
        score = inter / union if union else 0.0
        if score > best_score:
            best_score = score
            best = e
    if best and best_score >= threshold:
        return best
    return None
