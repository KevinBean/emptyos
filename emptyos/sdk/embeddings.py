"""Embedding helpers — daemon-side.

Mirror of services/chatbot/embed.py adapted for in-daemon use. Both files
exist on purpose: the chatbot service deploys standalone (Lane 1, no
EmptyOS imports), so the SDK can't be its source of truth. They share an
algorithm, not a module.

Usage from an app (via BaseApp helpers):

    # One-shot
    vec = await self.embed_text("how do I publish a blog post?")

    # Persistent per-app index
    index = await self.embedding_index(
        "vault-notes",
        items=[{"id": str(p), "text": p.read_text()} for p in vault_paths],
        text_fn=lambda it: it["text"][:1500],
    )
    hits = index.search(query, top_k=10)
    # hits → [(item, score), ...] sorted desc by cosine

The index persists embeddings keyed by content hash, so re-running on the
same items is free across restarts. Items whose `text_fn(item)` changes
get re-embedded on next build; removed items are GC'd.

Cost: text-embedding-3-small at $0.02/1M tokens. ~$0.03 to embed a
3000-note vault end-to-end; ~$5 per million queries.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("emptyos.sdk.embeddings")

EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536


def _sig(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


class Embedder:
    """Embedding pipeline with content-hash disk cache.

    Cache file shape: {<sig>: [floats]}.
    """

    def __init__(self, cache_path: Path):
        self.cache_path = cache_path
        self.cache: dict[str, list[float]] = {}
        if cache_path.exists():
            try:
                self.cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except Exception:
                log.warning("embedding cache unreadable, starting fresh: %s", cache_path)
                self.cache = {}
        self._client = None

    @property
    def available(self) -> bool:
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
        """Embed a batch. Returns vectors aligned to input. Uses cache for hits.

        On no-API-key or batch failure, returns zero-vectors for misses so
        callers can detect via `available`. Persists cache after refresh.
        """
        import asyncio

        sigs = [_sig(t) for t in texts]
        missing = [(i, t) for i, (t, s) in enumerate(zip(texts, sigs)) if s not in self.cache]

        if missing and not self.available:
            log.warning("no OPENAI_API_KEY — %d embeddings unavailable", len(missing))
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
                    for idx, _ in batch:
                        self.cache[sigs[idx]] = [0.0] * EMBED_DIM
                    continue
                for j, item in enumerate(resp.data):
                    self.cache[sigs[batch[j][0]]] = item.embedding
            self._flush()

        return [self.cache.get(s, [0.0] * EMBED_DIM) for s in sigs]

    async def embed_one(self, text: str) -> list[float]:
        return (await self.embed_many([text]))[0]


class EmbeddingIndex:
    """A list of (item, embedding) pairs queryable by cosine similarity.

    Build via `Embedder.build_index()` or `BaseApp.embedding_index()`. The
    index itself is stateless — it's just a snapshot of items + their
    embeddings. Recompute when items change.
    """

    def __init__(
        self,
        items: list[Any],
        embeddings: list[list[float]],
        embedder: Embedder | None = None,
    ):
        if len(items) != len(embeddings):
            raise ValueError("items and embeddings must align")
        self.items = items
        self.embeddings = embeddings
        self.embedder = embedder

    async def search(
        self,
        query: str,
        top_k: int = 10,
        min_score: float = 0.0,
    ) -> list[tuple[Any, float]]:
        """Embed the query, return top-k items by cosine. min_score filters
        weak hits — callers typically use 0.30 for "definitely related",
        0.0 for "best-effort".
        """
        if not self.embedder:
            raise RuntimeError("EmbeddingIndex needs embedder for search")
        q_emb = await self.embedder.embed_one(query)
        scored = [(self.items[i], cosine(q_emb, self.embeddings[i]))
                  for i in range(len(self.items))]
        scored.sort(key=lambda x: -x[1])
        return [(it, s) for it, s in scored[:top_k] if s >= min_score]

    def search_with_embedding(
        self, query_emb: list[float], top_k: int = 10, min_score: float = 0.0
    ) -> list[tuple[Any, float]]:
        """Variant when caller has already embedded the query (e.g. routing
        the same query through multiple indexes)."""
        scored = [(self.items[i], cosine(query_emb, self.embeddings[i]))
                  for i in range(len(self.items))]
        scored.sort(key=lambda x: -x[1])
        return [(it, s) for it, s in scored[:top_k] if s >= min_score]


_MD_LINK_RE = __import__("re").compile(r"\[([^\]]+)\]\([^)]+\)")
_FENCE_RE = __import__("re").compile(r"```[^`]*```", __import__("re").DOTALL)
_INLINE_CODE_PATH_RE = __import__("re").compile(r"`[^`]*[/.\\][^`]*`")
# Path token: optional drive (X:), then at least one slash before the extension.
# Avoids matching "e.g.", "i.e." while still catching "30_Resources/foo/bar.md"
# and "X:/Vault/notes/x.md".
_PATH_TOKEN_RE = __import__("re").compile(r"\b(?:[A-Za-z]:[/\\])?[\w-]+(?:[/\\][\w-]+)+\.[\w-]{1,8}\b")
_WS_RE = __import__("re").compile(r"\s+")


def _condense_assistant(text: str, max_chars: int = 200) -> str:
    """Strip citations / path mentions from a prior assistant turn so the
    retrieval query carries topical signal, not a list of files we already
    surfaced. Keeps the prose, drops:
      - markdown links [text](url) → text
      - fenced code blocks ``` … ```
      - inline-code containing path-ish chars
      - bare path tokens (foo/bar.md, D:/x/y.md)
    Then collapses whitespace and truncates to max_chars (head, since the
    topical lede usually comes first).
    """
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

    Retrieval against a bare follow-up like "how does it work?" loses topic.
    This concatenates the last N prior turns with the current message and
    tail-truncates at max_chars so recent context survives. Pass an empty
    list / None for first turns and you'll just get `current` back.

    Assistant turns are condensed (citations/paths stripped, capped at 200
    chars) before joining — they carry topical signal but raw cited paths
    drag retrieval back to the same notes turn after turn. User turns are
    kept verbatim because they're already short and on-topic.

    `messages` items are {role, content} dicts (history excluding current).
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


async def build_index(
    embedder: Embedder,
    items: list[Any],
    text_fn: Callable[[Any], str],
) -> EmbeddingIndex:
    """Embed `text_fn(item)` for each item, return a queryable index.

    Empty / falsy text_fn results get a zero-vector (will never match anything
    nonzero); they're kept so the items list aligns 1:1 with embeddings —
    simpler than filtering and re-mapping indices.
    """
    texts = [text_fn(it) or "" for it in items]
    embs = await embedder.embed_many(texts)
    return EmbeddingIndex(items, embs, embedder=embedder)
