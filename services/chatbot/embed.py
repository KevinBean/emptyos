"""Embedding helper for the chatbot service.

Wraps OpenAI text-embedding-3-small with a content-hash disk cache so
re-embedding the same chunk is free across restarts. Falls back to BM25
when no API key is set, so the service still works (just with worse
retrieval) on a fresh self-host.

Pattern is stand-alone here, but parallel work in the EmptyOS daemon
(apps/search, apps/note, apps/journal) will want the same shape — when
two apps need it, extract to `emptyos/sdk/embeddings.py`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
from collections import Counter
from pathlib import Path

log = logging.getLogger("chatbot.embed")

EMBED_MODEL = "text-embedding-3-small"  # 1536-dim, $0.02/1M tokens
EMBED_DIM = 1536


def _sig(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


class Embedder:
    """Embedding pipeline with content-hash disk cache.

    Cache shape (one JSON file): {<sig>: [floats]}.
    """

    def __init__(self, cache_path: Path):
        self.cache_path = cache_path
        self.cache: dict[str, list[float]] = {}
        if cache_path.exists():
            try:
                self.cache = json.loads(cache_path.read_text(encoding="utf-8"))
                log.info("loaded %d cached embeddings from %s", len(self.cache), cache_path)
            except Exception:
                log.warning("embedding cache unreadable, starting fresh: %s", cache_path)
                self.cache = {}
        self._client = None  # lazy

    @property
    def available(self) -> bool:
        """Are embeddings actually usable? Requires OPENAI_API_KEY."""
        return bool(os.environ.get("OPENAI_API_KEY"))

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI()
        return self._client

    def _flush(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.cache), encoding="utf-8")
            tmp.replace(self.cache_path)
        except Exception:
            log.exception("failed to persist embedding cache")

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Returns vectors aligned to input.

        Uses cache for hits, batches misses 200-at-a-time. Persists cache
        after a batch refresh. Synchronous OpenAI SDK call inside a thread
        so it doesn't block the event loop.
        """
        import asyncio

        sigs = [_sig(t) for t in texts]
        missing = [(i, t) for i, (t, s) in enumerate(zip(texts, sigs)) if s not in self.cache]
        if missing and not self.available:
            log.warning(
                "no OPENAI_API_KEY — %d embeddings will be unavailable; falling back to lexical",
                len(missing),
            )
            # Return zero-vector for misses; callers must check `available` before relying on cosines.
            return [self.cache.get(s) or [0.0] * EMBED_DIM for s in sigs]

        if missing:
            client = self._get_client()
            for start in range(0, len(missing), 200):
                batch = missing[start : start + 200]
                batch_texts = [t for _, t in batch]

                def _call(bt=batch_texts):
                    return client.embeddings.create(model=EMBED_MODEL, input=bt)

                try:
                    resp = await asyncio.to_thread(_call)
                except Exception:
                    log.exception("embedding batch failed (size=%d)", len(batch_texts))
                    # Mark misses with zero-vec so caller can detect via `available`.
                    for idx, _ in batch:
                        self.cache[sigs[idx]] = [0.0] * EMBED_DIM
                    continue
                for j, item in enumerate(resp.data):
                    self.cache[sigs[batch[j][0]]] = item.embedding
            self._flush()

        return [self.cache.get(s, [0.0] * EMBED_DIM) for s in sigs]

    async def embed_one(self, text: str) -> list[float]:
        return (await self.embed_many([text]))[0]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


# ── BM25 fallback (used when no API key, or as a hybrid second source) ──

_WORD_RE = re.compile(r"[A-Za-z0-9']+")
_STOP = {
    "a","an","and","are","as","at","be","by","can","do","does","for","from",
    "has","have","how","i","if","in","is","it","its","of","on","or","s","t",
    "that","the","this","to","was","what","when","where","which","who","why",
    "will","with","would","you","your","my","me","we","our","us","they","them",
    "their","there","these","those","tell","about","get","got","use","using",
}


def _tokens(text: str) -> list[str]:
    return [w for w in (m.lower() for m in _WORD_RE.findall(text or ""))
            if w not in _STOP and len(w) > 1]


class BM25:
    """Okapi BM25 over a fixed document set. Cheap, no deps."""

    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.N = len(docs)
        self.dl = [len(d) for d in docs]
        self.avgdl = (sum(self.dl) / max(1, self.N)) or 1.0
        df: Counter = Counter()
        for d in docs:
            for t in set(d):
                df[t] += 1
        self.idf = {t: math.log((self.N - n + 0.5) / (n + 0.5) + 1) for t, n in df.items()}
        self.tf = [Counter(d) for d in docs]

    def score(self, q_tokens: list[str], i: int) -> float:
        s = 0.0
        for t in q_tokens:
            if t not in self.tf[i]:
                continue
            f = self.tf[i][t]
            denom = f + self.k1 * (1 - self.b + self.b * self.dl[i] / self.avgdl)
            s += self.idf.get(t, 0.0) * (f * (self.k1 + 1)) / denom
        return s

    def top_k(self, query: str, k: int) -> list[tuple[int, float]]:
        qt = _tokens(query)
        if not qt:
            return []
        scored = [(i, self.score(qt, i)) for i in range(self.N)]
        scored.sort(key=lambda x: -x[1])
        return [(i, s) for i, s in scored[:k] if s > 0]


_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_FENCE_RE = re.compile(r"```[^`]*```", re.DOTALL)
_INLINE_CODE_PATH_RE = re.compile(r"`[^`]*[/.\\][^`]*`")
_PATH_TOKEN_RE = re.compile(r"\b(?:[A-Za-z]:[/\\])?[\w-]+(?:[/\\][\w-]+)+\.[\w-]{1,8}\b")
_WS_RE = re.compile(r"\s+")


def _condense_assistant(text: str, max_chars: int = 200) -> str:
    """Strip citations / path mentions from a prior assistant turn so the
    retrieval query carries topical signal, not a list of files we already
    surfaced. See emptyos/sdk/embeddings.py for the canonical docstring."""
    t = _MD_LINK_RE.sub(lambda m: m.group(1), text)
    t = _FENCE_RE.sub(" ", t)
    t = _INLINE_CODE_PATH_RE.sub(" ", t)
    t = _PATH_TOKEN_RE.sub(" ", t)
    t = _WS_RE.sub(" ", t).strip()
    return t[:max_chars]


def build_retrieval_query(
    messages: list[dict],
    current: str,
    max_chars: int = 800,
    history_turns: int = 3,
) -> str:
    """Build a multi-turn embedding query.

    The retrieval pass needs context for follow-ups like "how does it work?"
    or "and the second one?" — the bare current message has no signal. We
    concatenate the last N turns (excluding the current message itself if
    it appears at the tail) with " | " separators, then tail-truncate at
    max_chars so the most recent context survives.

    Assistant turns are condensed (citations/paths stripped, capped at 200
    chars) so retrieval doesn't keep snapping back to whatever notes turn 1
    cited. User turns pass through verbatim.

    `messages` is the raw history (each item: {role, content}); `current`
    is the message being resolved. Pass an empty list / None for first turns
    — the function just returns `current` in that case.
    """
    cur = (current or "").strip()
    if not messages:
        return cur
    parts: list[str] = []
    for m in messages[-history_turns:]:
        content = (m.get("content") or "").strip()
        if not content or content == cur:
            continue
        if (m.get("role") or "").lower() == "assistant":
            content = _condense_assistant(content)
            if not content:
                continue
        parts.append(content)
    parts.append(cur)
    combined = " | ".join(p for p in parts if p)
    if len(combined) > max_chars:
        combined = combined[-max_chars:]
    return combined


def chunk_text_for_embedding(chunk: dict, max_chars: int = 1500) -> str:
    """Build the text-to-embed for a chunk. Title weight is implicit (it's prefixed)."""
    title = (chunk.get("title") or "").strip()
    section = (chunk.get("section") or "").strip()
    body = (chunk.get("text") or "").strip()[:max_chars]
    return f"{title}\n{section}\n{body}".strip()
