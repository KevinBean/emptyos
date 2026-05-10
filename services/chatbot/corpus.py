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

from embed import cosine

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


def stuff_corpus(
    payload: dict,
    max_chars: int = _MAX_CORPUS_CHARS,
    selected_chunks: list[dict] | None = None,
) -> str:
    """Concatenate chunks into a system-prompt-shaped block, capped.

    Format:
      id: <chunk-id>
      title: TITLE — section
      url: <url>
      text...

    The chunk `id` is rendered explicitly (and first) so the model can cite
    it verbatim in the SOURCES block. Without this the model can only see
    title/section and would have to guess the canonical id format.

    If `selected_chunks` is provided (the retrieval path), stuffs only those
    in the given order. Otherwise falls back to "first-N from payload" — the
    legacy behavior, used when retrieval is disabled or unavailable.
    """
    chunks = selected_chunks if selected_chunks is not None else (payload.get("chunks", []) or [])
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
        block = f"id: {cid}\ntitle: {header}\nurl: {url}\n{text}"
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


# ── Embedding-based retrieval (preferred when OPENAI_API_KEY is set) ──
#
# These are drop-in upgrades over the Jaccard matchers above. They take the
# same inputs but use cosine similarity over precomputed embeddings. When
# embeddings are unavailable (no API key, embedding call failed, or chunks
# haven't been embedded yet), the higher-level caller falls back to the
# Jaccard versions — so this is purely additive.


def select_chunks_by_embedding(
    query_emb: list[float],
    chunks: list[dict],
    chunk_embs: list[list[float]],
    top_k: int = 8,
    min_score: float = 0.30,
) -> tuple[list[dict], float]:
    """Pick top-k chunks by cosine similarity. Returns (selected, max_score).

    `chunks` and `chunk_embs` must be index-aligned. `min_score` filters
    weakly-related results so the model isn't given junk context — but the
    decision to short-circuit on low max_score is the caller's, not ours.
    """
    if not chunks or not chunk_embs or len(chunks) != len(chunk_embs):
        return [], 0.0
    scored = [(i, cosine(query_emb, chunk_embs[i])) for i in range(len(chunks))]
    scored.sort(key=lambda x: -x[1])
    max_score = scored[0][1] if scored else 0.0
    selected = [chunks[i] for i, s in scored[:top_k] if s >= min_score]
    return selected, max_score


def match_faq_by_embedding(
    query_emb: list[float],
    faqs: list[dict],
    faq_embs: list[list[float]],
    threshold: float = 0.78,
) -> dict | None:
    """Embedding-cosine FAQ match. Threshold tuned for text-embedding-3-small:
    0.78 catches confident paraphrases without false positives on weak overlap.
    """
    if not faqs or not faq_embs or len(faqs) != len(faq_embs):
        return None
    best, best_score = None, 0.0
    for i, f in enumerate(faqs):
        s = cosine(query_emb, faq_embs[i])
        if s > best_score:
            best, best_score = f, s
    return best if best and best_score >= threshold else None


def match_curated_by_embedding(
    query_emb: list[float],
    entries: list[dict],
    entry_embs: list[list[float]],
    threshold: float = 0.85,
) -> dict | None:
    """Embedding-cosine curated-cache match. Stricter threshold than FAQs
    because curated entries are organic phrasings — we only want very
    confident hits to bypass the LLM.
    """
    if not entries or not entry_embs or len(entries) != len(entry_embs):
        return None
    best, best_score = None, 0.0
    for i, e in enumerate(entries):
        s = cosine(query_emb, entry_embs[i])
        if s > best_score:
            best, best_score = e, s
    return best if best and best_score >= threshold else None


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
