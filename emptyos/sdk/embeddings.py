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
import tomllib
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("emptyos.sdk.embeddings")

# Default provider = OpenAI (cloud). Kept as the fallback default so the
# behaviour is byte-identical until [embeddings] is configured (dark by
# default). Switch to a local model with, in emptyos.toml:
#   [embeddings]
#   provider = "ollama"      # local, private, offline; grep-degrades when down
#   model = "bge-m3"         # 1024-dim, strong 中英 multilingual
# Env overrides: EOS_EMBED_PROVIDER / EOS_EMBED_MODEL / EOS_EMBED_BASE_URL / EOS_EMBED_DIM.
EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536

_OLLAMA_DEFAULT_URL = "http://127.0.0.1:11434/v1"

# Known embedding dimensions. An unlisted model needs an explicit `dim` in
# config (or EOS_EMBED_DIM) — a wrong dim silently breaks cosine.
MODEL_DIMS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "bge-m3": 1024,
    "nomic-embed-text": 768,
    "mxbai-embed-large": 1024,
    "snowflake-arctic-embed": 1024,
}


def _load_toml_embeddings() -> dict:
    """Read the [embeddings] table from emptyos.toml. Returns {} if absent.

    Located relative to the repo root (this file is emptyos/sdk/embeddings.py),
    then the cwd, so it resolves whether the daemon runs from root or a test
    runs elsewhere. Never raises.
    """
    candidates = []
    try:
        candidates.append(Path(__file__).resolve().parents[2] / "emptyos.toml")
    except Exception:
        pass
    candidates.append(Path("emptyos.toml"))
    for p in candidates:
        try:
            if p.exists():
                return dict(tomllib.loads(p.read_text(encoding="utf-8")).get("embeddings", {}) or {})
        except Exception:
            continue
    return {}


def _resolve_embed_config() -> dict:
    """Resolve provider/model/dim/base_url. Env > toml > defaults.

    Default is OpenAI text-embedding-3-small (current behaviour) so the whole
    change is dark until someone sets [embeddings] provider = "ollama".
    """
    toml = _load_toml_embeddings()
    provider = (os.environ.get("EOS_EMBED_PROVIDER") or toml.get("provider") or "openai").lower()
    if provider == "ollama":
        model = os.environ.get("EOS_EMBED_MODEL") or toml.get("model") or "bge-m3"
        base_url = os.environ.get("EOS_EMBED_BASE_URL") or toml.get("base_url") or _OLLAMA_DEFAULT_URL
    else:
        provider = "openai"
        model = os.environ.get("EOS_EMBED_MODEL") or toml.get("model") or EMBED_MODEL
        base_url = os.environ.get("EOS_EMBED_BASE_URL") or toml.get("base_url") or None
    dim_raw = os.environ.get("EOS_EMBED_DIM") or toml.get("dim")
    dim = int(dim_raw) if dim_raw else MODEL_DIMS.get(model, EMBED_DIM)
    return {"provider": provider, "model": model, "dim": dim, "base_url": base_url}


def _model_tag(model: str) -> str:
    """Filesystem-safe tag for per-model cache namespacing (bge-m3, gpt etc)."""
    import re
    return re.sub(r"[^\w.-]", "_", model) or "default"


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

    Provider-aware: OpenAI (cloud, default) or a local OpenAI-compatible
    endpoint like ollama (``provider = "ollama"``, model ``bge-m3``). The
    cache is namespaced per-model so switching providers never mixes
    incompatible vector spaces / dimensions — a model switch just starts a
    fresh (or previously-built) per-model cache.

    Fallback discipline: there is NO cross-model query-time fallback (a
    bge-m3 index cannot be queried with OpenAI vectors). When a local
    provider is unreachable, ``available`` goes False and callers degrade to
    grep/keyword search — correct, private, and self-healing when the local
    server returns. OpenAI stays an opt-in *alternative primary* (set
    ``provider = "openai"``), never an automatic per-query backup.

    Cache file shape: {<sig>: [floats]}, at ``<cache_path>.<model>.json``.
    """

    def __init__(self, cache_path: Path):
        cfg = _resolve_embed_config()
        self.provider: str = cfg["provider"]
        self.model: str = cfg["model"]
        self.dim: int = cfg["dim"]
        self.base_url: str | None = cfg["base_url"]
        # Per-model cache namespace: shared.json -> shared.bge-m3.json.
        self.cache_path = cache_path.with_name(
            f"{cache_path.stem}.{_model_tag(self.model)}{cache_path.suffix}"
        )
        # The cache is loaded LAZILY — see _ensure_cache(). It used to be parsed
        # here in __init__, which made merely *constructing* an Embedder cost a
        # 265 MB json.loads: 2.4s of blocking event loop and 0.39 GB resident,
        # measured 2026-08-05. Nothing about `available` (the overwhelmingly
        # common call — it only reads provider config and pings reachability)
        # needs a single cached vector, so the whole cost was being paid for a
        # config check. journal's setup() did exactly that on every boot:
        # 208 samples, 2.65s median, 97s worst.
        self._cache: dict[str, list[float]] | None = None
        self._client = None
        self._reach_ok = False
        self._reach_at = 0.0

    @property
    def cache(self) -> dict[str, list[float]]:
        """The content-hash vector cache, loaded on first touch.

        A property rather than a plain attribute so every existing read/write
        site keeps working unchanged while paying the load only when it is
        genuinely needed.

        The None check is inline rather than delegated to `_ensure_cache` because
        `embed_many` evaluates `self.cache` once per element inside list
        comprehensions over the batch — the steady-state cost should be an
        identity test, not a function call per text.
        """
        if self._cache is None:
            self._ensure_cache()
        return self._cache  # type: ignore[return-value]

    @cache.setter
    def cache(self, value: dict[str, list[float]]) -> None:
        self._cache = value

    @property
    def cache_loaded(self) -> bool:
        """True once the on-disk cache has actually been read. Lets a caller
        (and the tests) assert that a cheap path stayed cheap."""
        return self._cache is not None

    def _ensure_cache(self) -> None:
        if self._cache is not None:
            return
        self._cache = {}
        if not self.cache_path.exists():
            return
        try:
            self._cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except Exception:
            log.warning("embedding cache unreadable, starting fresh: %s", self.cache_path)
            self._cache = {}

    @property
    def available(self) -> bool:
        if self.provider == "ollama":
            return self._local_reachable()
        return bool(os.environ.get("OPENAI_API_KEY"))

    def _local_reachable(self) -> bool:
        """Cheap cached reachability ping for a local endpoint (30s TTL).

        Off → callers grep-degrade rather than returning garbage. Cached so
        it isn't pinged on every ``available`` access.
        """
        import time
        import urllib.request

        now = time.monotonic()
        if now - self._reach_at < 30.0:
            return self._reach_ok
        self._reach_at = now
        try:
            url = (self.base_url or _OLLAMA_DEFAULT_URL).rstrip("/") + "/models"
            with urllib.request.urlopen(url, timeout=2.5) as r:
                self._reach_ok = 200 <= r.status < 300
        except Exception:
            self._reach_ok = False
        return self._reach_ok

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            if self.provider == "ollama":
                self._client = OpenAI(
                    base_url=self.base_url or _OLLAMA_DEFAULT_URL,
                    api_key="ollama",  # ollama ignores it; the client requires a value
                    timeout=120.0,  # survive first-call model load + large batches
                )
            elif self.base_url:
                self._client = OpenAI(base_url=self.base_url)
            else:
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

        # First real use pays the disk read — off the event loop, because a
        # 265 MB json.loads is 2.4s of wedged daemon (the sync-in-async class in
        # .claude/rules/debugging.md). Every later call sees it already loaded.
        if not self.cache_loaded:
            await asyncio.to_thread(self._ensure_cache)

        sigs = [_sig(t) for t in texts]
        missing = [(i, t) for i, (t, s) in enumerate(zip(texts, sigs)) if s not in self.cache]

        if missing and not self.available:
            log.warning("embeddings unavailable (%s) — %d texts unembedded",
                        self.provider, len(missing))
            return [self.cache.get(s) or [0.0] * self.dim for s in sigs]

        if missing:
            client = self._get_client()
            batch_size = 64 if self.provider == "ollama" else 200
            for start in range(0, len(missing), batch_size):
                batch = missing[start : start + batch_size]
                batch_texts = [t for _, t in batch]

                def _call(bt=batch_texts):
                    return client.embeddings.create(model=self.model, input=bt)

                try:
                    resp = await asyncio.to_thread(_call)
                except Exception:
                    log.exception("embedding batch failed (provider=%s size=%d)",
                                  self.provider, len(batch_texts))
                    for idx, _ in batch:
                        self.cache[sigs[idx]] = [0.0] * self.dim
                    continue
                for j, item in enumerate(resp.data):
                    self.cache[sigs[batch[j][0]]] = item.embedding
            self._flush()

        return [self.cache.get(s, [0.0] * self.dim) for s in sigs]

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
