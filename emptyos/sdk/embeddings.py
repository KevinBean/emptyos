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

import array
import hashlib
import json
import logging
import math
import os
import struct
import sys
import threading
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


# One lock per store, shared by every Embedder on it. Two instances can point
# at the same cache (BaseApp keeps a shared one and per-app ones), and both
# loading and appending run on worker threads. The lock covers the whole
# load (read, torn-tail trim, JSON conversion) and every append, so a load
# never trims or overwrites an append that landed meanwhile. Re-entrant
# because a load that trims takes it again from inside.
_APPEND_LOCKS: dict[str, threading.RLock] = {}
_APPEND_LOCKS_GUARD = threading.Lock()


def _append_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _APPEND_LOCKS_GUARD:
        return _APPEND_LOCKS.setdefault(key, threading.RLock())


# Binary vector store: STORE_MAGIC, then records of
#   <u8 sig length> <u32 vector length> <sig utf-8> <float32 * length>
# little-endian. Float32 loses nothing for bge-m3, which already returns
# float32 values: cosine scores and top-10 orders were identical on 5,466
# real vectors (2026-09-24). A JSON float list costs ~5x the bytes and far
# more to parse. A torn trailing record is found by length and trimmed.
STORE_MAGIC = b"EOSVEC1\n"
_REC_HEAD = struct.Struct("<BI")


def _encode_record(sig: str, vec: list[float]) -> bytes:
    sig_b = sig.encode("utf-8")
    if len(sig_b) > 255:
        raise ValueError(f"embedding signature too long: {len(sig_b)} bytes")
    floats = array.array("f", vec)
    if sys.byteorder != "little":
        floats.byteswap()
    return _REC_HEAD.pack(len(sig_b), len(floats)) + sig_b + floats.tobytes()


_MAX_DIM = 65536
_MAX_RECORD = _REC_HEAD.size + 255 + 4 * _MAX_DIM


def _decode_store(data: bytes) -> tuple[dict[str, list[float]], int, bool]:
    """Parse a store.

    Returns (vectors, bytes up to the last whole record, tail_ok). tail_ok is
    False when parsing stopped at something a crash cannot produce: a
    vector length over _MAX_DIM, or a partial record followed by more bytes
    than one whole record would need. A crash only ever tears the LAST
    record, so trimming is safe exactly when tail_ok is True; otherwise the
    bytes after `pos` may be good records behind a damaged one.
    """
    out: dict[str, list[float]] = {}
    pos = len(STORE_MAGIC)
    size = len(data)
    prev_len = 0
    while pos + _REC_HEAD.size <= size:
        sig_len, n = _REC_HEAD.unpack_from(data, pos)
        if n > _MAX_DIM:
            return out, pos, False
        start = pos + _REC_HEAD.size
        end = start + sig_len + 4 * n
        if end > size:
            leftover = size - pos
            return out, pos, leftover < (prev_len or _MAX_RECORD)
        floats = array.array("f")
        floats.frombytes(data[start + sig_len:end])
        if sys.byteorder != "little":
            floats.byteswap()
        out[data[start:start + sig_len].decode("utf-8", "replace")] = floats.tolist()
        prev_len = end - pos
        pos = end
    return out, pos, True


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

    Cache on disk: ``<cache_path>.<model>.f32``, a binary append-only store
    (format beside STORE_MAGIC). Vectors are held at float32. For bge-m3
    that was measured lossless (5,466 real vectors, identical cosine scores
    and rankings), since ollama already returns float32 values. It has not
    been measured for OpenAI models.

    Older versions wrote ``<cache_path>.<model>.json`` plus a
    ``.append.jsonl`` log. When no store exists, those are read and
    converted once; they are left on disk and not read again.

    New vectors are appended, never saved by rewriting the cache. The old code
    re-serialised the whole cache after every embed, on the event loop. Aura
    embeds every new sentence, so its reply waited on the rewrite, and so did
    everything else the daemon was doing. Measured 2026-09-24 on a sandbox
    with a 124 MB cache: json.dumps alone took 1.6 s. The main daemon's cache
    was 777 MB, so roughly 10 s per rewrite there (extrapolated, not measured).

    A failed batch is cached and persisted as zero vectors, as it always was.
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
        self.append_path = self.cache_path.with_name(f"{self.cache_path.stem}.append.jsonl")
        self.store_path = self.cache_path.with_name(f"{self.cache_path.stem}.f32")
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
        # Held for the whole load. Two first loads (the shared Embedder and a
        # per-app one, or two threads) would otherwise both convert the JSON
        # and the later os.replace would drop appends made in between; and a
        # trim computed from a read taken before an append would cut it off.
        with _append_lock(self.store_path):
            if self._cache is not None:
                return
            if self.store_path.exists():
                self._cache = self._load_store()
                return
            cache = self._load_legacy()
            if cache:
                self._migrate(cache)
            self._cache = cache

    def _move_aside(self, reason: str) -> None:
        """Rename a damaged store out of the way. Never deletes, never
        overwrites an earlier aside copy."""
        n = 0
        while True:
            aside = self.store_path.with_name(
                f"{self.store_path.name}.unreadable" + (f".{n}" if n else ""))
            if not aside.exists():
                break
            n += 1
        try:
            os.replace(self.store_path, aside)
            log.warning("embedding store %s; moved to %s", reason, aside)
        except OSError:
            log.warning("embedding store %s: %s", reason, self.store_path)

    def _load_store(self) -> dict[str, list[float]]:
        """Read the store. Caller holds the store lock."""
        try:
            data = self.store_path.read_bytes()
        except OSError:
            log.warning("embedding store unreadable: %s", self.store_path)
            return {}
        if not data.startswith(STORE_MAGIC):
            self._move_aside("has no header")
            return {}
        cache, good, tail_ok = _decode_store(data)
        if good < len(data) and not tail_ok:
            # Damage before the end, not a torn tail: the bytes after it may
            # be good records. Keep them all for inspection and start fresh.
            self._move_aside(f"is damaged at byte {good}")
            return {}
        if good < len(data):
            # A crash mid-append. Trim the partial record so the next append
            # starts on a record boundary instead of being read as garbage.
            try:
                with open(self.store_path, "r+b") as f:
                    f.truncate(good)
                log.warning("trimmed %d torn byte(s) from %s", len(data) - good, self.store_path)
            except OSError:
                log.warning("could not trim torn tail of %s", self.store_path)
        return cache

    def _load_legacy(self) -> dict[str, list[float]]:
        """The JSON base plus its append log, as written before the binary store."""
        cache: dict[str, list[float]] = {}
        if self.cache_path.exists():
            try:
                loaded = json.loads(self.cache_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    cache = loaded
            except Exception:
                log.warning("embedding cache unreadable, starting fresh: %s", self.cache_path)
        if self.append_path.exists():
            skipped = 0
            try:
                with open(self.append_path, encoding="utf-8") as f:
                    for line in f:
                        try:
                            sig, vec = json.loads(line)
                        except (ValueError, TypeError):
                            # A crash mid-append leaves a torn last line.
                            skipped += 1
                            continue
                        if isinstance(sig, str) and isinstance(vec, list):
                            cache[sig] = vec
            except OSError:
                log.warning("embedding append log unreadable: %s", self.append_path)
            if skipped:
                log.warning("skipped %d unreadable line(s) in %s", skipped, self.append_path)
        return cache

    def _migrate(self, cache: dict[str, list[float]]) -> None:
        """Write the legacy cache out as a binary store, once.

        The JSON files are left in place; nothing reads them once the store
        exists. On failure no store is created, so appends keep going to the
        JSON append log and the next load tries again.
        """
        tmp = self.store_path.with_name(f"{self.store_path.name}.tmp")
        try:
            with open(tmp, "wb") as f:
                f.write(STORE_MAGIC)
                for sig, vec in cache.items():
                    f.write(_encode_record(sig, vec))
                # On disk before the rename. Otherwise a power loss could put
                # a half-written store in place; its missing tail would be
                # trimmed as torn and the rest of the JSON never converted.
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.store_path)
            log.info("migrated %d embedding(s) to %s", len(cache), self.store_path)
        except Exception:
            log.exception("embedding store migration failed; staying on JSON")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def _uses_store(self) -> bool:
        """The binary store is live once it exists, or from the start when
        there is no JSON cache to migrate."""
        return self.store_path.exists() or not (
            self.cache_path.exists() or self.append_path.exists()
        )

    def _append(self, entries: list[tuple[str, list[float]]]) -> None:
        """Persist newly embedded vectors by appending, never by rewriting.

        Runs on a worker thread (see embed_many). Never raises: a vector that
        fails to persist is simply embedded again after a restart.
        """
        if not entries:
            return
        try:
            # The store lock also covers the choice below and a legacy append,
            # so a vector cannot land in the JSON log while a conversion that
            # has already read that log is still running.
            with _append_lock(self.store_path):
                # Load (and so trim any torn tail) before the first append, or
                # the new records would sit behind a torn one and the next load
                # would read the store as damaged mid-file.
                self._ensure_cache()
                if self._uses_store():
                    self._append_store(entries)
                else:
                    self._append_legacy(entries)
        except Exception:
            log.exception("failed to persist %d embedding(s)", len(entries))

    def _append_store(self, entries: list[tuple[str, list[float]]]) -> None:
        """Caller holds the store lock."""
        records = b"".join(_encode_record(sig, vec) for sig, vec in entries)
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.store_path, "ab") as f:
            if f.tell() == 0:
                f.write(STORE_MAGIC)
            f.write(records)

    def _append_legacy(self, entries: list[tuple[str, list[float]]]) -> None:
        lines = "".join(
            json.dumps([sig, vec], separators=(",", ":")) + "\n" for sig, vec in entries
        )
        self.append_path.parent.mkdir(parents=True, exist_ok=True)
        with _append_lock(self.append_path):
            # A crash mid-append leaves a last line with no newline. Start
            # on a fresh line, or the next vector joins the torn one and is
            # lost with it.
            prefix = ""
            try:
                with open(self.append_path, "rb") as f:
                    f.seek(-1, os.SEEK_END)
                    if f.read(1) != b"\n":
                        prefix = "\n"
            except OSError:
                pass  # missing or empty file: nothing to terminate
            with open(self.append_path, "a", encoding="utf-8") as f:
                f.write(prefix + lines)

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

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch. Returns vectors aligned to input. Uses cache for hits.

        On no-API-key or batch failure, returns zero-vectors for misses so
        callers can detect via `available`. Appends each new batch to the
        store, off the event loop.
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

                fresh = []
                try:
                    resp = await asyncio.to_thread(_call)
                except Exception:
                    # Cached (and persisted) as zero vectors, as before the
                    # append log: the search indexer treats a cached sig as
                    # done, so leaving failures out would drop those notes
                    # from search instead of retrying them.
                    log.exception("embedding batch failed (provider=%s size=%d)",
                                  self.provider, len(batch_texts))
                    for idx, _ in batch:
                        zero = [0.0] * self.dim
                        self.cache[sigs[idx]] = zero
                        fresh.append((sigs[idx], zero))
                else:
                    for j, item in enumerate(resp.data):
                        sig = sigs[batch[j][0]]
                        # Held at the store's float32 precision so a vector
                        # reads the same before and after a restart.
                        vec = array.array("f", item.embedding).tolist()
                        self.cache[sig] = vec
                        fresh.append((sig, vec))
                await asyncio.to_thread(self._append, fresh)

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
