"""EmptyOS chatbot service — FastAPI app.

POST /chat                  — synchronous reply (JSON)
POST /chat/stream           — SSE token stream
GET  /health                — liveness check
GET  /sites/<id>/meta       — public site meta (name, starter_questions)
POST /admin/refresh/<id>    — invalidate corpus cache for one site (auth header)

Anti-abuse layers (in order):
  1. Origin lock        → 401
  2. Token caps         → 400
  3. Per-IP rate limit  → 429
  4. Per-site $ cap     → 429
  5. Topic gate         → in system prompt
  6. Logging            → SQLite ledger
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from .config import STUDIO_FIELDS, SYNCED_FIELDS, Config, SiteConfig, load_config, write_config_atomic
from .commerce import (
    cart_summary_reply,
    commerce_reply,
    demo_printer_products,
    ensure_catalog_feed,
    load_catalog,
    save_catalog,
)
from .corpus import (
    CorpusCache,
    match_curated,
    match_curated_by_embedding,
    match_faq,
    match_faq_by_embedding,
    select_chunks_by_embedding,
    stuff_corpus,
)
from .crawler import build_corpus, invalidate_corpus
from .embed import (
    BM25,
    Embedder,
    _tokens as _bm25_tokens,
    build_retrieval_query,
    chunk_text_for_embedding,
)
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from .ledger import Ledger, hash_ip
from .providers import get_provider
from pydantic import BaseModel, Field
from .security import validate_public_url
from .site_secrets import SiteSecrets

log = logging.getLogger("chatbot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


# ── Request / response models ──────────────────────────────────────


class ChatMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str


class ChatRequest(BaseModel):
    site_id: str
    messages: list[ChatMessage]
    session_id: str = ""


class ChatReply(BaseModel):
    reply: str
    sources: list[dict] = Field(default_factory=list)
    tokens_used: int
    cost_usd: float
    remaining_today_usd: float
    source: str  # "faq" | "model" | "cached" | "commerce" | "lead" | "low-confidence"
    blocks: list[dict] = Field(default_factory=list)


class CommerceActionBody(BaseModel):
    site_id: str
    session_id: str = ""
    action: str
    idempotency_key: str
    args: dict = Field(default_factory=dict)


# ── App + globals ──────────────────────────────────────────────────

CONFIG: Config | None = None
CORPUS: CorpusCache | None = None
LEDGER: Ledger | None = None
EMBEDDER: Embedder | None = None
SECRETS: SiteSecrets | None = None
ADMIN_TOKEN = os.environ.get("CHATBOT_ADMIN_TOKEN", "")

# Per-site retrieval cache. Rebuilt lazily on first use after corpus refresh,
# invalidated when CorpusCache.invalidate() is called.
#   {site_id: {"corpus_sig": str, "chunk_embs": [[...]], "bm25": BM25, "faq_embs": [[...]]}}
_RETRIEVAL: dict[str, dict] = {}


async def chatbot_init() -> None:
    """Load config, corpus cache, ledger, embedder, secrets into module globals.

    Exported so a host that MOUNTS this app (the External Lab Host) can drive the
    startup explicitly — Starlette does not propagate lifespan to mounted sub-apps.
    Idempotent: safe to call twice (re-reads config).
    """
    global CONFIG, CORPUS, LEDGER, EMBEDDER, SECRETS
    CONFIG = load_config()
    CORPUS = CorpusCache(ttl_seconds=CONFIG.defaults.corpus_ttl_seconds)
    LEDGER = Ledger()
    # Embedding cache lives next to the SQLite ledger so Docker volume covers both.
    cache_path = Path(os.environ.get("CHATBOT_DATA_DIR", "./data")) / "embeddings.json"
    EMBEDDER = Embedder(cache_path=cache_path)
    SECRETS = SiteSecrets()
    log.info(
        "loaded %d sites: %s | embeddings: %s",
        len(CONFIG.sites),
        list(CONFIG.sites.keys()),
        "on" if EMBEDDER.available else "off (no OPENAI_API_KEY — falling back to BM25)",
    )


async def chatbot_shutdown() -> None:
    """Tear down module globals. Ledger opens SQLite per-op (nothing long-lived to
    close), so this just drops references; kept explicit + exported for symmetry so
    a mounting host can drive shutdown."""
    global CONFIG, CORPUS, LEDGER, EMBEDDER, SECRETS, _RETRIEVAL
    CONFIG = CORPUS = LEDGER = EMBEDDER = SECRETS = None
    _RETRIEVAL = {}


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    await chatbot_init()
    try:
        yield
    finally:
        await chatbot_shutdown()


app = FastAPI(title="EmptyOS Chatbot Service", version="0.1.0", lifespan=lifespan)


# ── Helpers ────────────────────────────────────────────────────────

# Sentinel that separates the prose answer from the citation list. Chosen to
# be unlikely to occur in normal markdown content. Both the system prompt and
# parse_sources() reference this constant.
SOURCES_MARKER = "---SOURCES---"

SYSTEM_TEMPLATE = """{persona}

You are answering on behalf of {site_name}. Use only the context below to answer. If the user asks something the context doesn't cover, say "I can only help with topics on this site." and suggest they explore the site directly.

Keep answers to 2-3 sentences unless the user explicitly asks for more detail. Reference specific posts by their title when relevant. Do not invent facts.

After your answer, ALWAYS emit a citations block in EXACTLY this format (no other text after it):

{marker}
- <chunk-id-1>
- <chunk-id-2>

Each chunk-id must EXACTLY match an `id` field from the CONTEXT below — copy them verbatim. Cite 1-3 chunks that most directly support your answer. If you couldn't find any relevant chunks, still emit the block with a single line `- none`. Do not invent ids.

CONTEXT:
{corpus}
"""

# Appended to the persona when a site declares avoid_topics — the prompt half
# of the two-lever deprecation contract (the other is `corpus: false`).
AVOID_TOPICS_CLAUSE = (
    "\n\nThe following products or pages are retired or unavailable — never "
    "recommend them; if asked, say they are no longer offered: {topics}"
)


def _build_system_prompt(
    site, corpus_payload: dict, selected_chunks: list[dict] | None = None
) -> str:
    persona = site.persona.strip() or f"You are a helpful guide to {site.name}."
    # Second deprecation lever (pairs with `corpus: false` in publish): a
    # retired page may stay live for SEO and still be crawlable, so the prompt
    # must exclude it too. Empty list = byte-identical prompt.
    avoid = [t.strip() for t in getattr(site, "avoid_topics", []) or [] if t.strip()]
    if avoid:
        persona += AVOID_TOPICS_CLAUSE.format(topics="; ".join(avoid))
    corpus_text = stuff_corpus(corpus_payload, selected_chunks=selected_chunks)
    return SYSTEM_TEMPLATE.format(
        persona=persona,
        site_name=site.name,
        marker=SOURCES_MARKER,
        corpus=corpus_text or "(no published content yet)",
    )


# ── Lead capture (content sites) ────────────────────────────────────
# The unattended equivalent of human handover: collect name/email/enquiry via
# a typed form (details go to the team, never the model) and answer by email
# later. Deterministic, free, and the business-critical outcome is never left
# to generation. Commerce sites keep their own handoff.create branch, which
# runs first.

LEAD_INTENT_TERMS = (
    "talk to a human", "speak to a human", "talk to someone", "speak to someone",
    "real person", "human", "helpdesk", "customer support", "support team",
    "sales team", "get in touch", "contact you", "contact the team", "reach out",
    "leave my details", "leave a message", "email me back", "call me back",
)


def _lead_form_reply(site) -> dict:
    return {
        "reply": (
            f"I can pass your details to the {site.name} team and they'll get back "
            "to you by email. Your contact details go directly to the team, not "
            "the language model."
        ),
        "blocks": [
            {
                "type": "secure_action_form",
                "action": "lead.create",
                "title": "Leave your details",
                "fields": [
                    {"name": "name", "label": "Name", "type": "text"},
                    {"name": "email", "label": "Email", "type": "email"},
                    {"name": "message", "label": "What would you like to ask?", "type": "text"},
                ],
            }
        ],
    }


def _match_lead_intent(site, message: str) -> dict | None:
    if not getattr(site, "lead_capture_enabled", False):
        return None
    q = message.lower()
    if any(term in q for term in LEAD_INTENT_TERMS):
        return _lead_form_reply(site)
    return None


# ── Retrieval pipeline ──────────────────────────────────────────────


def _corpus_sig(payload: dict) -> str:
    """Hash the chunk-id list. Cheap signal that the corpus changed; if it did,
    we drop the precomputed embeddings/BM25 for that site and rebuild lazily."""
    ids = "|".join(c.get("id", "") for c in payload.get("chunks", []) or [])
    return hashlib.sha1(ids.encode("utf-8")).hexdigest()[:12]


async def _ensure_retrieval(site_id: str, payload: dict) -> dict:
    """Lazy-build per-site retrieval indexes (embeddings + BM25). Cached in
    _RETRIEVAL until the corpus signature changes.
    """
    sig = _corpus_sig(payload)
    cur = _RETRIEVAL.get(site_id)
    if cur and cur.get("corpus_sig") == sig:
        return cur

    chunks = payload.get("chunks", []) or []
    faqs = payload.get("faqs", []) or []

    # BM25 index — always built; cheap and used as fallback.
    chunk_token_docs = [
        _bm25_tokens(
            (c.get("title", "") + " ") * 3
            + (c.get("section", "") + " ") * 2
            + c.get("text", "")
        )
        for c in chunks
    ]
    bm25 = BM25(chunk_token_docs) if chunks else None

    chunk_embs: list[list[float]] = []
    faq_embs: list[list[float]] = []
    if EMBEDDER and EMBEDDER.available:
        try:
            chunk_embs = await EMBEDDER.embed_many(
                [chunk_text_for_embedding(c) for c in chunks]
            )
            if faqs:
                faq_embs = await EMBEDDER.embed_many([f.get("q", "") for f in faqs])
        except Exception:
            log.exception("embedding precompute failed for site=%s — falling back to BM25", site_id)
            chunk_embs, faq_embs = [], []

    entry = {
        "corpus_sig": sig,
        "chunk_embs": chunk_embs,
        "faq_embs": faq_embs,
        "bm25": bm25,
    }
    _RETRIEVAL[site_id] = entry
    return entry


async def _retrieve_chunks(
    site_id: str, query: str, payload: dict, top_k: int = 8
) -> tuple[list[dict], float, str]:
    """Pick chunks for the system prompt. Returns (chunks, max_score, method).
    method ∈ {"embed", "bm25", "stuff-all"}.
    """
    chunks = payload.get("chunks", []) or []
    if not chunks:
        return [], 0.0, "stuff-all"

    idx = await _ensure_retrieval(site_id, payload)
    chunk_embs = idx.get("chunk_embs") or []

    if EMBEDDER and EMBEDDER.available and len(chunk_embs) == len(chunks):
        try:
            q_emb = await EMBEDDER.embed_one(query)
            selected, max_score = select_chunks_by_embedding(
                q_emb, chunks, chunk_embs, top_k=top_k, min_score=0.30
            )
            if selected:
                return selected, max_score, "embed"
        except Exception:
            log.exception("embedding retrieval failed; using BM25")

    bm25 = idx.get("bm25")
    if bm25:
        ranked = bm25.top_k(query, top_k)
        if ranked:
            selected = [chunks[i] for i, _ in ranked]
            max_score = ranked[0][1] if ranked else 0.0
            # BM25 scores are unbounded; normalize for confidence-floor by
            # comparing best vs second-best ratio. Loose proxy.
            return selected, max_score, "bm25"

    # Last resort: legacy "stuff first-N" (capped in stuff_corpus).
    return chunks, 0.0, "stuff-all"


CONFIDENCE_FLOOR = 0.32  # cosine; tuned from the bench
# At this floor, real paraphrase hits (which cluster around cos 0.40-0.60)
# pass through to the LLM, while genuinely off-topic queries (cos <0.30, e.g.
# "weather in Tokyo" against a software corpus) short-circuit to a free
# "I don't have a confident answer + nearest 3 posts" reply.
# Calibrated against services/chatbot/research/test_retrieval_e2e.py.


async def _resolve_query(
    site, last_msg: str, corpus_payload: dict, history: list[dict] | None = None
) -> dict:
    """One-shot lookup: FAQ → curated → retrieved chunks. Embedding-aware,
    falls back to Jaccard/BM25 when embeddings unavailable.

    `history` is the full message list (including the current user turn).
    For multi-turn conversations we build a context-augmented embedding
    query so follow-ups like "how does it work?" resolve to the right topic.
    FAQ and curated matches still use the bare last_msg — they're meant for
    repeat questions, not topical drift.

    Returns a dict with one of these shapes:
      {"hit": "faq",     "faq": {...}}
      {"hit": "curated", "curated": {...}}
      {"hit": "low_confidence", "selected": [chunks], "max_score": float}
      {"hit": "model",   "selected": [chunks], "method": str}
    """
    faqs = corpus_payload.get("faqs", []) or []
    curated_rows = await asyncio.to_thread(LEDGER.find_curated, site.id)

    # ── Build the retrieval query (multi-turn aware) ──
    history_dicts = []
    if history:
        # `history` items may be ChatMessage models or dicts
        for m in history[:-1]:  # exclude the current turn (it is `last_msg`)
            if hasattr(m, "model_dump"):
                history_dicts.append(m.model_dump())
            elif isinstance(m, dict):
                history_dicts.append(m)
    retrieval_query = build_retrieval_query(history_dicts, last_msg)

    # Compute query embedding once if available — reused for FAQ + curated + chunks.
    # FAQ/curated use the bare last_msg (intent: catch repeat questions).
    # Chunks use the multi-turn retrieval_query (intent: follow-up context).
    bare_emb: list[float] | None = None
    multi_emb: list[float] | None = None
    if EMBEDDER and EMBEDDER.available:
        try:
            if retrieval_query == last_msg:
                bare_emb = await EMBEDDER.embed_one(last_msg)
                multi_emb = bare_emb
            else:
                bare_emb, multi_emb = await EMBEDDER.embed_many([last_msg, retrieval_query])
        except Exception:
            log.exception("query embedding failed; falling back to lexical")
            bare_emb = multi_emb = None

    # ── FAQ (bare query — repeat-question matcher) ──
    faq_hit = None
    if bare_emb is not None and faqs:
        idx = await _ensure_retrieval(site.id, corpus_payload)
        faq_embs = idx.get("faq_embs") or []
        faq_hit = match_faq_by_embedding(bare_emb, faqs, faq_embs)
    if faq_hit is None:
        faq_hit = match_faq(last_msg, faqs)
    if faq_hit:
        return {"hit": "faq", "faq": faq_hit}

    # ── Curated (bare query — repeat-question matcher) ──
    curated_hit = None
    if bare_emb is not None and curated_rows:
        try:
            curated_qs = [r.get("query", "") for r in curated_rows]
            curated_embs = await EMBEDDER.embed_many(curated_qs)
            curated_hit = match_curated_by_embedding(bare_emb, curated_rows, curated_embs)
        except Exception:
            log.exception("curated embedding match failed; falling back to Jaccard")
    if curated_hit is None:
        curated_hit = match_curated(last_msg, curated_rows)
    if curated_hit:
        return {"hit": "curated", "curated": curated_hit}

    # ── Chunk retrieval (multi-turn query — follow-up aware) ──
    selected, max_score, method = await _retrieve_chunks(
        site.id, retrieval_query, corpus_payload, top_k=8
    )
    if method == "embed" and max_score < CONFIDENCE_FLOOR:
        return {"hit": "low_confidence", "selected": selected, "max_score": max_score}
    return {"hit": "model", "selected": selected, "method": method}


def _low_confidence_reply(site, selected: list[dict], max_score: float) -> str:
    """Built when retrieval came back too weak to trust the model on context.
    Shows up to 3 nearest titles as suggestions. Free, faster than an LLM call.
    """
    bullets = []
    for c in selected[:3]:
        title = (c.get("title") or "").strip() or c.get("slug") or c.get("id", "")
        url = c.get("url") or ""
        if url:
            bullets.append(f"- [{title}]({url})")
        else:
            bullets.append(f"- {title}")
    suffix = "\n\n" + "\n".join(bullets) if bullets else ""
    return (
        f"I don't have a confident answer to that on {site.name}. "
        f"You might find what you're looking for here:" + suffix
    )


def parse_sources(full_text: str, corpus_payload: dict) -> tuple[str, list[dict]]:
    """Split full_text on SOURCES_MARKER, look up each cited id in the corpus.

    Returns (clean_reply, sources). Hallucinated ids are dropped. If the
    marker never appeared (model didn't follow the format), return the text
    unchanged with sources=[].
    """
    if SOURCES_MARKER not in full_text:
        return full_text.strip(), []
    prose, _, tail = full_text.partition(SOURCES_MARKER)
    clean_reply = prose.rstrip()
    chunks_by_id = {c.get("id"): c for c in (corpus_payload.get("chunks") or [])}
    sources: list[dict] = []
    seen: set[str] = set()
    for line in tail.splitlines():
        line = line.strip()
        if not line:
            continue
        # Accept "- id", "* id", "1. id", or bare "id".
        if line.startswith(("-", "*")):
            line = line[1:].strip()
        else:
            # Numbered list: leading digits + "."
            i = 0
            while i < len(line) and line[i].isdigit():
                i += 1
            if i > 0 and i < len(line) and line[i] == ".":
                line = line[i + 1 :].strip()
        if not line or line.lower() == "none":
            continue
        # Strip any markdown link wrapping the id, e.g. "[id](url)".
        if line.startswith("[") and "]" in line:
            line = line[1 : line.index("]")]
        cid = line.strip("`'\" ").strip()
        if not cid or cid in seen:
            continue
        chunk = chunks_by_id.get(cid)
        if not chunk:
            continue
        seen.add(cid)
        sources.append(
            {
                "id": cid,
                "title": chunk.get("title", ""),
                "section": chunk.get("section", ""),
                "url": chunk.get("url", ""),
            }
        )
    return clean_reply, sources


def _client_ip(request: Request) -> str:
    # Caddy/Cloudflare set X-Forwarded-For; trust the first hop only.
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _gate_request(req_origin: str, body: ChatRequest, cfg: Config) -> tuple[Any, str]:
    """Run all per-request gates. Return (site_config, last_user_message_text)."""
    site = cfg.sites.get(body.site_id)
    if not site:
        raise HTTPException(status_code=404, detail=f"unknown site: {body.site_id}")
    if not site.enabled:
        raise HTTPException(status_code=503, detail="assistant is disabled")

    # 1. Origin lock — must match an entry in allowed_origins.
    if req_origin not in site.allowed_origins:
        raise HTTPException(status_code=401, detail="origin not allowed")

    # 2. Message validation + token cap.
    if not body.messages or body.messages[-1].role != "user":
        raise HTTPException(status_code=400, detail="last message must be from user")
    last = body.messages[-1].content
    if len(last) > cfg.defaults.max_input_chars:
        raise HTTPException(
            status_code=400, detail=f"message too long (>{cfg.defaults.max_input_chars} chars)"
        )
    # Cap conversation history depth.
    if len(body.messages) > 20:
        body.messages = body.messages[-20:]

    return site, last


def _check_rate_limits(site, ip: str, cfg: Config) -> None:
    state = LEDGER.check_limits(
        site_id=site.id,
        ip_hash=hash_ip(ip),
        site_daily_cap=site.daily_cap_usd,
        global_daily_cap=cfg.defaults.global_cap_usd,
        rate_per_hour=cfg.defaults.rate_limit_per_hour,
        rate_per_day=cfg.defaults.rate_limit_per_day,
    )
    if not state.allowed:
        raise HTTPException(
            status_code=429,
            detail={"reason": state.reason, "retry_after": state.retry_after_seconds},
            headers={"Retry-After": str(state.retry_after_seconds)},
        )


async def _site_corpus(site: SiteConfig) -> dict:
    if site.corpus_url:
        return await CORPUS.get(site.id, site.corpus_url)
    if site.knowledge_sources:
        return await build_corpus(
            site.id,
            site.knowledge_sources,
            ttl_seconds=CONFIG.defaults.corpus_ttl_seconds,
        )
    return {"chunks": [], "faqs": []}


async def _refresh_catalog_feed(site: SiteConfig) -> None:
    if not site.catalog_url:
        return
    try:
        await ensure_catalog_feed(
            site.id,
            site.catalog_url,
            stale_seconds=site.catalog_stale_seconds,
        )
    except Exception as exc:
        # Keep serving the last valid local snapshot; never replace it with a
        # partial/invalid remote response.
        log.warning("catalog refresh failed for %s: %s", site.id, exc)


# ── Routes ─────────────────────────────────────────────────────────


@app.get("/")
async def root(request: Request) -> dict:
    """Friendly landing so a bare visit isn't a raw 404. Prefix-aware, so it works
    at the service root (production) and under a mount (/chatbot/)."""
    base = request.scope.get("root_path", "") or ""
    return {
        "service": "emptyos-chatbot",
        "version": app.version,
        "status": "ok",
        "sites": list(CONFIG.sites.keys()) if CONFIG else [],
        "endpoints": {
            "health": f"{base}/health",
            "demo": f"{base}/demo-store/",
            "widget": f"{base}/widget/chatbot-widget.js",
        },
    }


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "sites": list(CONFIG.sites.keys()) if CONFIG else []}


@app.get("/widget/chatbot-widget.js")
async def widget_script():
    candidates = [
        Path(__file__).parent / "widget" / "chatbot-widget.js",
        Path(__file__).parents[1] / "apps" / "public" / "standard" / "publish" / "static" / "chatbot-widget.js",
        Path(__file__).parents[2] / "apps" / "public" / "standard" / "publish" / "static" / "chatbot-widget.js",
    ]
    path = next((candidate for candidate in candidates if candidate.exists()), None)
    if not path:
        raise HTTPException(404, "widget not installed")
    return FileResponse(path, media_type="application/javascript", headers={"Cache-Control": "public, max-age=300"})


@app.get("/demo-store")
@app.get("/demo-store/")
async def demo_store(request: Request):
    if os.environ.get("CHATBOT_DEMO_ENABLED") != "1":
        raise HTTPException(404, "demo store is disabled")
    path = Path(__file__).parent / "demo-store" / "index.html"
    if not path.exists():
        raise HTTPException(404, "demo store is not installed")
    # Prefix-aware: when this app is mounted under /chatbot, root_path is set and the
    # page's own asset URLs (catalog.json, widget, endpoint) must carry the prefix.
    base = request.scope.get("root_path", "") or ""
    html = path.read_text(encoding="utf-8").replace("{{BASE}}", base)
    return HTMLResponse(html)


@app.get("/demo-store/catalog.json")
async def demo_store_catalog():
    if os.environ.get("CHATBOT_DEMO_ENABLED") != "1":
        raise HTTPException(404, "demo store is disabled")
    return load_catalog("printer-pilot")


@app.get("/sites/{site_id}/meta")
async def site_meta(site_id: str, response: Response) -> dict:
    if not CONFIG or site_id not in CONFIG.sites:
        raise HTTPException(404, "unknown site")
    s = CONFIG.sites[site_id]
    # The widget fetches this once per pageview on every embedding site, so it
    # caches for the same 300s as the widget script itself — the two are read
    # together and there is no point revalidating one and not the other. The
    # cost is that a Studio config edit takes up to 5 minutes to reach visitors,
    # which already matched the widget's own propagation delay.
    response.headers["Cache-Control"] = "public, max-age=300"
    return {
        "id": s.id,
        "name": s.name,
        "starter_questions": s.starter_questions,
        "welcome_message": s.welcome_message,
        "brand_color": s.brand_color,
        "commerce_enabled": s.commerce_enabled,
    }


@app.post("/admin/refresh/{site_id}")
async def admin_refresh(site_id: str, x_admin_token: str = Header(default="")) -> dict:
    if not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(401, "unauthorized")
    if not CONFIG or site_id not in CONFIG.sites:
        raise HTTPException(404, "unknown site")
    CORPUS.invalidate(site_id)
    _RETRIEVAL.pop(site_id, None)
    return {"ok": True, "site": site_id}


# ── /admin/sites/{id} — sync mutable fields from publish app ────────


class SiteAdminBody(BaseModel):
    id: str | None = None
    name: str | None = None
    allowed_origins: list[str] | None = None
    corpus_url: str | None = None
    knowledge_sources: list[str] | None = None
    catalog_url: str | None = None
    catalog_stale_seconds: int | None = None
    commerce_enabled: bool | None = None
    allowed_actions: list[str] | None = None
    action_endpoint: str | None = None
    helpdesk_endpoint: str | None = None
    model: str | None = None
    persona: str | None = None
    daily_cap_usd: float | None = None
    starter_questions: list[str] | None = None
    enabled: bool | None = None
    brand_color: str | None = None
    welcome_message: str | None = None
    avoid_topics: list[str] | None = None
    lead_capture_enabled: bool | None = None
    connector_token: str | None = None


def _site_payload(site: SiteConfig) -> dict:
    data = {key: getattr(site, key) for key in STUDIO_FIELDS if hasattr(site, key)}
    data.update({"id": site.id, "managed_by": site.managed_by})
    data["connector_configured"] = bool(SECRETS and SECRETS.configured(site.id))
    catalog = load_catalog(site.id)
    data["catalog_products"] = len(catalog.get("products", []))
    data["catalog_generated_at"] = catalog.get("generated_at")
    return data


def _validate_site_payload(payload: dict, *, creating: bool = False) -> None:
    if creating:
        site_id = str(payload.get("id") or "")
        allowed = "abcdefghijklmnopqrstuvwxyz0123456789-_"
        if not site_id or any(ch not in allowed for ch in site_id):
            raise HTTPException(400, "id must contain only lowercase letters, numbers, hyphens, or underscores")
    origins = payload.get("allowed_origins")
    if origins is not None and (not origins or not all(isinstance(value, str) and value.startswith(("http://", "https://")) for value in origins)):
        raise HTTPException(400, "allowed_origins must contain HTTP(S) origins")
    allow_local = os.environ.get("CHATBOT_ALLOW_LOCAL_SOURCES") == "1"
    for key in ("corpus_url", "catalog_url", "action_endpoint", "helpdesk_endpoint"):
        value = str(payload.get(key) or "").strip()
        if value and not value.startswith("demo://"):
            try:
                validate_public_url(value, allow_local=allow_local)
            except ValueError as exc:
                raise HTTPException(400, f"{key}: {exc}") from exc
    for source in payload.get("knowledge_sources") or []:
        try:
            validate_public_url(str(source), allow_local=allow_local)
        except ValueError as exc:
            raise HTTPException(400, f"knowledge_sources: {exc}") from exc
    cap = payload.get("daily_cap_usd")
    if cap is not None and not 0 <= float(cap) <= 1000:
        raise HTTPException(400, "daily_cap_usd must be 0..1000")


def _apply_studio_fields(site: SiteConfig, payload: dict) -> list[str]:
    applied = []
    for key in STUDIO_FIELDS:
        if key in payload and payload[key] is not None:
            setattr(site, key, payload[key])
            applied.append(key)
    return sorted(applied)


@app.get("/admin/sites")
async def admin_sites(x_admin_token: str = Header(default="")) -> dict:
    _require_admin(x_admin_token)
    return {"sites": [_site_payload(site) for site in CONFIG.sites.values()]}


@app.post("/admin/sites/validate")
async def admin_site_validate(body: SiteAdminBody, x_admin_token: str = Header(default="")) -> dict:
    _require_admin(x_admin_token)
    payload = body.model_dump(exclude_none=True)
    _validate_site_payload(payload, creating=bool(payload.get("id")))
    return {"ok": True, "validated": sorted(payload)}


@app.post("/admin/sites")
async def admin_site_create(body: SiteAdminBody, x_admin_token: str = Header(default="")) -> dict:
    _require_admin(x_admin_token)
    payload = body.model_dump(exclude_none=True)
    _validate_site_payload(payload, creating=True)
    site_id = payload.pop("id")
    if site_id in CONFIG.sites:
        raise HTTPException(409, "site already exists")
    required = [key for key in ("name", "allowed_origins") if not payload.get(key)]
    if required:
        raise HTTPException(400, f"missing required fields: {', '.join(required)}")
    site = SiteConfig(
        id=site_id,
        name=payload.get("name", site_id),
        allowed_origins=payload.get("allowed_origins", []),
        corpus_url=payload.get("corpus_url", ""),
        daily_cap_usd=float(payload.get("daily_cap_usd", CONFIG.defaults.daily_cap_usd)),
        model=payload.get("model", CONFIG.defaults.model),
        persona=payload.get("persona", ""),
        managed_by="studio",
    )
    _apply_studio_fields(site, payload)
    CONFIG.sites[site_id] = site
    token = payload.get("connector_token")
    if token and SECRETS:
        SECRETS.set_bearer(site_id, token)
    try:
        await asyncio.to_thread(write_config_atomic, CONFIG)
    except Exception:
        CONFIG.sites.pop(site_id, None)
        raise
    return {"ok": True, "site": _site_payload(site)}


@app.post("/admin/demo/printer-store")
async def admin_demo_printer_store(x_admin_token: str = Header(default="")) -> dict:
    _require_admin(x_admin_token)
    if os.environ.get("CHATBOT_DEMO_ENABLED") != "1":
        raise HTTPException(403, "set CHATBOT_DEMO_ENABLED=1 to create the controlled pilot")
    site_id = "printer-pilot"
    site = CONFIG.sites.get(site_id)
    if site and site.managed_by != "studio":
        raise HTTPException(409, "printer-pilot id is owned by another manager")
    if not site:
        public_origin = os.environ.get("CHATBOT_PUBLIC_ORIGIN", "http://127.0.0.1:8000").rstrip("/")
        site = SiteConfig(
            id=site_id,
            name="Northstar Office Printer Store",
            allowed_origins=[public_origin],
            corpus_url="",
            daily_cap_usd=CONFIG.defaults.daily_cap_usd,
            model=CONFIG.defaults.model,
            persona="Help customers choose office printers and supplies. Never invent price, stock, or compatibility.",
            managed_by="studio",
            enabled=True,
            commerce_enabled=True,
            allowed_actions=["inventory.lookup", "cart.add", "order.lookup", "return.check", "handoff.create"],
            action_endpoint="demo://printer-store",
            starter_questions=["Which Wi-Fi printer is under $300?", "Compare colour laser printers", "Where is my order?"],
            welcome_message="Find the right printer for your home or small office. Tip: include the printer model or feature you care about.",
        )
        CONFIG.sites[site_id] = site
    catalog = await asyncio.to_thread(save_catalog, site_id, demo_printer_products())
    await asyncio.to_thread(write_config_atomic, CONFIG)
    return {"ok": True, "site": _site_payload(site), "products": len(catalog["products"]), "demo_url": "/demo-store/"}


@app.get("/admin/sites/{site_id}")
async def admin_site_get(site_id: str, x_admin_token: str = Header(default="")) -> dict:
    _require_admin(x_admin_token)
    site = CONFIG.sites.get(site_id)
    if not site:
        raise HTTPException(404, "unknown site")
    return {"site": _site_payload(site)}


@app.put("/admin/sites/{site_id}")
async def admin_site_put(site_id: str, body: SiteAdminBody, x_admin_token: str = Header(default="")) -> dict:
    _require_admin(x_admin_token)
    site = CONFIG.sites.get(site_id)
    if not site:
        raise HTTPException(404, "unknown site")
    if site.managed_by != "studio":
        raise HTTPException(409, "site is managed by Publish and is read-only in Chatbot Studio")
    payload = body.model_dump(exclude_none=True, exclude={"id"})
    _validate_site_payload(payload)
    applied = _apply_studio_fields(site, payload)
    if "connector_token" in payload and SECRETS:
        SECRETS.set_bearer(site_id, payload["connector_token"])
        applied.append("connector_token")
    await asyncio.to_thread(write_config_atomic, CONFIG)
    CORPUS.invalidate(site_id)
    await asyncio.to_thread(invalidate_corpus, site_id)
    _RETRIEVAL.pop(site_id, None)
    return {"ok": True, "applied": sorted(applied), "site": _site_payload(site)}


async def _set_site_enabled(site_id: str, enabled: bool, token: str) -> dict:
    _require_admin(token)
    site = CONFIG.sites.get(site_id)
    if not site:
        raise HTTPException(404, "unknown site")
    if site.managed_by != "studio":
        raise HTTPException(409, "site is managed by Publish")
    site.enabled = enabled
    await asyncio.to_thread(write_config_atomic, CONFIG)
    return {"ok": True, "site": site_id, "enabled": enabled}


@app.post("/admin/sites/{site_id}/enable")
async def admin_site_enable(site_id: str, x_admin_token: str = Header(default="")) -> dict:
    return await _set_site_enabled(site_id, True, x_admin_token)


@app.post("/admin/sites/{site_id}/disable")
async def admin_site_disable(site_id: str, x_admin_token: str = Header(default="")) -> dict:
    return await _set_site_enabled(site_id, False, x_admin_token)


class CatalogBody(BaseModel):
    products: list[dict]


@app.post("/admin/sites/{site_id}/catalog")
async def admin_catalog(site_id: str, body: CatalogBody, x_admin_token: str = Header(default="")) -> dict:
    _require_admin(x_admin_token)
    site = CONFIG.sites.get(site_id)
    if not site:
        raise HTTPException(404, "unknown site")
    if site.managed_by != "studio":
        raise HTTPException(409, "site is managed by Publish")
    try:
        payload = await asyncio.to_thread(save_catalog, site_id, body.products)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "site": site_id, "products": len(payload["products"]), "generated_at": payload["generated_at"]}


@app.post("/admin/sites/{site_id}/refresh")
async def admin_site_refresh(site_id: str, x_admin_token: str = Header(default="")) -> dict:
    return await admin_refresh(site_id, x_admin_token)


@app.get("/admin/sites/{site_id}/leads")
async def admin_site_leads(
    site_id: str,
    x_admin_token: str = Header(default=""),
    limit: int = 100,
    offset: int = 0,
) -> dict:
    _require_admin(x_admin_token)
    if not CONFIG or site_id not in CONFIG.sites:
        raise HTTPException(404, "unknown site")
    leads = await asyncio.to_thread(
        LEDGER.list_leads, site_id=site_id, limit=max(1, min(500, limit)), offset=max(0, offset)
    )
    return {"site": site_id, "leads": leads}


@app.get("/admin/sites/{site_id}/health")
async def admin_site_health(site_id: str, x_admin_token: str = Header(default="")) -> dict:
    _require_admin(x_admin_token)
    site = CONFIG.sites.get(site_id)
    if not site:
        raise HTTPException(404, "unknown site")
    catalog = load_catalog(site_id)
    return {"site_id": site_id, "enabled": site.enabled, "commerce_enabled": site.commerce_enabled, "catalog_products": len(catalog.get("products", [])), "catalog_generated_at": catalog.get("generated_at"), "connector_configured": bool(SECRETS and SECRETS.configured(site_id))}


@app.get("/admin/sites/{site_id}/analytics")
async def admin_site_analytics(site_id: str, x_admin_token: str = Header(default="")) -> dict:
    _require_admin(x_admin_token)
    if site_id not in CONFIG.sites:
        raise HTTPException(404, "unknown site")
    return {"site_id": site_id, **await asyncio.to_thread(LEDGER.analytics, site_id)}


class SiteTestBody(BaseModel):
    message: str = "What products do you recommend?"


@app.post("/admin/sites/{site_id}/test")
async def admin_site_test(site_id: str, body: SiteTestBody, x_admin_token: str = Header(default="")) -> dict:
    _require_admin(x_admin_token)
    site = CONFIG.sites.get(site_id)
    if not site:
        raise HTTPException(404, "unknown site")
    answer = commerce_reply(site_id, body.message) if site.commerce_enabled else None
    return {"ok": True, "site_id": site_id, **(answer or {"reply": "The content assistant is configured; use the website widget for a full model-backed test.", "blocks": []})}


class SiteSyncBody(BaseModel):
    """Mutable per-site fields. All optional — only fields present in the
    request body are applied; missing fields keep their current value."""

    model: str | None = None
    persona: str | None = None
    daily_cap_usd: float | None = None
    starter_questions: list[str] | None = None


@app.post("/admin/sites/{site_id}")
async def admin_site_sync(
    site_id: str,
    body: SiteSyncBody,
    x_admin_token: str = Header(default=""),
) -> dict:
    """Update mutable fields on an existing site, persist to sites.toml,
    hot-reload in-memory CONFIG. File-only fields (allowed_origins,
    corpus_url, name) are NOT touched — those require manual SSH edit.
    The site must already exist (this is sync, not create)."""
    _require_admin(x_admin_token)
    if not CONFIG:
        raise HTTPException(503, "config not loaded yet")
    site = CONFIG.sites.get(site_id)
    if not site:
        raise HTTPException(
            404,
            f"unknown site '{site_id}' — add a [sites.{site_id}] block to "
            "sites.toml on the VPS first (with allowed_origins + corpus_url)",
        )

    payload = body.model_dump(exclude_none=True)
    invalid = set(payload) - SYNCED_FIELDS
    if invalid:
        raise HTTPException(400, f"non-syncable fields rejected: {sorted(invalid)}")

    # Validate and apply
    if "model" in payload:
        m = str(payload["model"]).strip()
        if not m:
            raise HTTPException(400, "model must be non-empty")
        site.model = m
    if "persona" in payload:
        site.persona = str(payload["persona"])
    if "daily_cap_usd" in payload:
        cap = float(payload["daily_cap_usd"])
        if cap < 0 or cap > 1000:
            raise HTTPException(400, "daily_cap_usd must be 0..1000")
        site.daily_cap_usd = cap
    if "starter_questions" in payload:
        sq = payload["starter_questions"]
        if not isinstance(sq, list) or not all(isinstance(x, str) for x in sq):
            raise HTTPException(400, "starter_questions must be list[str]")
        site.starter_questions = [s.strip() for s in sq if s.strip()]

    # Persist + invalidate corpus cache so any persona-driven prompt change
    # takes effect on the next request without restart.
    try:
        path = await asyncio.to_thread(write_config_atomic, CONFIG)
    except Exception as e:
        log.exception("failed to write sites.toml")
        raise HTTPException(500, f"failed to persist: {e}") from e
    log.info("synced site=%s fields=%s wrote=%s", site_id, sorted(payload), path)
    return {
        "ok": True,
        "site": site_id,
        "applied": sorted(payload),
        "wrote": str(path),
    }


# ── Q&A admin endpoints (curate / reject / edit / promote) ─────────


def _require_admin(token: str) -> None:
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        raise HTTPException(401, "unauthorized")


_VALID_QA_STATUS = {"pending", "curated", "rejected"}


@app.get("/admin/qa-log/{site_id}")
async def admin_qa_list(
    site_id: str,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    x_admin_token: str = Header(default=""),
) -> dict:
    _require_admin(x_admin_token)
    if not CONFIG or site_id not in CONFIG.sites:
        raise HTTPException(404, "unknown site")
    if status and status not in _VALID_QA_STATUS:
        raise HTTPException(400, f"invalid status: {status}")
    limit = max(1, min(limit, 200))
    rows = await asyncio.to_thread(
        LEDGER.list_qa, site_id=site_id, status=status, limit=limit, offset=max(0, offset)
    )
    return {"site_id": site_id, "rows": rows, "count": len(rows)}


class QAUpdateBody(BaseModel):
    action: str  # curate | reject | edit | unreject | uncurate
    reply: str | None = None


@app.post("/admin/qa-log/{qa_id}")
async def admin_qa_update(
    qa_id: int,
    body: QAUpdateBody,
    x_admin_token: str = Header(default=""),
) -> dict:
    _require_admin(x_admin_token)
    row = await asyncio.to_thread(LEDGER.get_qa, qa_id)
    if not row:
        raise HTTPException(404, "qa entry not found")

    action = body.action
    new_status: str | None = None
    new_reply: str | None = None

    if action == "curate":
        new_status = "curated"
    elif action == "reject":
        new_status = "rejected"
    elif action == "uncurate" or action == "unreject":
        new_status = "pending"
    elif action == "edit":
        if not body.reply:
            raise HTTPException(400, "edit requires `reply` field")
        new_reply = body.reply
    else:
        raise HTTPException(400, f"unknown action: {action}")

    ok = await asyncio.to_thread(LEDGER.update_qa, qa_id, status=new_status, reply=new_reply)
    if not ok:
        raise HTTPException(500, "update failed")
    return {"ok": True, "id": qa_id, "status": new_status, "reply_changed": new_reply is not None}


@app.post("/admin/qa-log/{qa_id}/promote")
async def admin_qa_promote(
    qa_id: int,
    x_admin_token: str = Header(default=""),
) -> dict:
    """Return the {q, a} pair for the caller to append to faqs.toml.

    The chat service does not write to the vault — only EmptyOS does. We mark
    the row 'curated' here so it serves from cache while EmptyOS does the
    file-system write; the caller can pass back a confirmation if desired.
    """
    _require_admin(x_admin_token)
    row = await asyncio.to_thread(LEDGER.get_qa, qa_id)
    if not row:
        raise HTTPException(404, "qa entry not found")
    await asyncio.to_thread(LEDGER.update_qa, qa_id, status="curated")
    return {
        "id": qa_id,
        "site_id": row["site_id"],
        "q": row["query"],
        "a": row["reply"],
        "sources": row.get("sources") or [],
    }


def _origin_site(site_id: str, origin: str, *, require_commerce: bool = True) -> SiteConfig:
    site = CONFIG.sites.get(site_id) if CONFIG else None
    if not site:
        raise HTTPException(404, "unknown site")
    if not site.enabled:
        raise HTTPException(403, "assistant is disabled")
    if require_commerce and not site.commerce_enabled:
        raise HTTPException(403, "commerce is disabled")
    if origin not in site.allowed_origins:
        raise HTTPException(401, "origin not allowed")
    return site


async def _post_connector(site: SiteConfig, url: str, payload: dict, idempotency_key: str) -> dict:
    """Mechanical connector POST shared by the commerce action path and the
    lead helpdesk delivery: SSRF-validate the endpoint, attach the site bearer
    + idempotency header, POST, return the parsed JSON. Error SEMANTICS stay
    at the call site (commerce raises 502; lead marks delivery failed)."""
    validate_public_url(url, allow_local=os.environ.get("CHATBOT_ALLOW_LOCAL_SOURCES") == "1")
    import httpx

    headers = {"Content-Type": "application/json", "Idempotency-Key": idempotency_key}
    bearer = SECRETS.get_bearer(site.id) if SECRETS else ""
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        try:
            return response.json()
        except ValueError:
            return {}


async def _handle_lead(site: SiteConfig, body: CommerceActionBody) -> dict:
    """Store the lead first, then best-effort deliver to helpdesk_endpoint.
    The row is written before any network call so a lead is never lost."""
    name = str(body.args.get("name") or "").strip()
    email = str(body.args.get("email") or "").strip()
    message = str(body.args.get("message") or "").strip()
    if not email or "@" not in email or len(email) > 320:
        raise HTTPException(400, "a valid email is required")
    if not message:
        raise HTTPException(400, "message is required")
    lead_id = await asyncio.to_thread(
        LEDGER.add_lead,
        site_id=site.id,
        name=name,
        email=email,
        message=message,
        page_url=str(body.args.get("page_url") or ""),
    )
    if site.helpdesk_endpoint:
        try:
            await _post_connector(
                site,
                site.helpdesk_endpoint,
                {"site_id": site.id, "name": name, "email": email, "message": message},
                body.idempotency_key,
            )
            await asyncio.to_thread(LEDGER.set_lead_delivery, lead_id, "delivered")
        except Exception:
            log.exception("helpdesk delivery failed site=%s lead=%s", site.id, lead_id)
            await asyncio.to_thread(LEDGER.set_lead_delivery, lead_id, "failed")
    return {
        "ok": True,
        "action": "lead.create",
        "lead_id": lead_id,
        "message": "Thanks — the team has your details and will get back to you by email.",
    }


def _demo_action(site: SiteConfig, body: CommerceActionBody) -> dict:
    if os.environ.get("CHATBOT_DEMO_ENABLED") != "1":
        raise HTTPException(403, "demo connector is disabled")
    products = load_catalog(site.id).get("products", [])
    product_id = str(body.args.get("product_id") or "")
    product = next((p for p in products if p.get("id") == product_id), None)
    if body.action == "inventory.lookup":
        if not product:
            raise HTTPException(404, "product not found")
        return {"ok": True, "action": body.action, "product_id": product_id, "availability": product.get("availability", "unknown")}
    if body.action == "cart.add":
        if not product:
            raise HTTPException(404, "product not found")
        return {"ok": True, "action": body.action, "product_id": product_id, "quantity": max(1, min(10, int(body.args.get("quantity") or 1)))}
    if body.action == "order.lookup":
        if not body.args.get("order_number") or not body.args.get("email"):
            raise HTTPException(400, "order number and email are required")
        return {"ok": True, "action": body.action, "status": "Ready for collection", "updated": "Today"}
    if body.action == "return.check":
        if not body.args.get("order_number"):
            raise HTTPException(400, "order number is required")
        return {"ok": True, "action": body.action, "eligible": True, "next_step": "A team member must approve and create the return."}
    if body.action == "handoff.create":
        ticket = hashlib.sha1(f"{site.id}:{body.idempotency_key}".encode()).hexdigest()[:8].upper()
        return {"ok": True, "action": body.action, "ticket_id": ticket, "message": "A team member will follow up."}
    raise HTTPException(400, "unsupported demo action")


@app.post("/commerce/action")
async def commerce_action(body: CommerceActionBody, request: Request) -> dict:
    # lead.create is gated by lead_capture_enabled, not by commerce/allowed_actions —
    # it rides this route because the widget posts every secure_action_form here.
    is_lead = body.action == "lead.create"
    site = _origin_site(body.site_id, request.headers.get("origin", ""), require_commerce=not is_lead)
    if is_lead and not site.lead_capture_enabled:
        raise HTTPException(403, "lead capture is disabled")
    if not is_lead and body.action not in site.allowed_actions:
        raise HTTPException(403, "action is not allowed for this site")
    if not 8 <= len(body.idempotency_key) <= 128:
        raise HTTPException(400, "idempotency_key must be 8..128 characters")
    cached = await asyncio.to_thread(LEDGER.action_result, site.id, body.idempotency_key)
    if cached is not None:
        return {**cached, "idempotent_replay": True}
    if is_lead:
        result = await _handle_lead(site, body)
        await asyncio.to_thread(LEDGER.store_action_result, site.id, body.idempotency_key, body.action, result)
        await asyncio.to_thread(LEDGER.record_event, site_id=site.id, event_type="lead.create")
        return result
    if site.action_endpoint.startswith("demo://"):
        # Reads the catalogue file — keep it off the event loop.
        result = await asyncio.to_thread(_demo_action, site, body)
    else:
        if not site.action_endpoint:
            raise HTTPException(503, "commerce connector is not configured")
        try:
            result = await _post_connector(
                site,
                site.action_endpoint,
                {"action": body.action, "args": body.args},
                body.idempotency_key,
            )
        except Exception:
            log.exception("commerce connector failed site=%s action=%s", site.id, body.action)
            raise HTTPException(502, "commerce connector unavailable") from None
    await asyncio.to_thread(LEDGER.store_action_result, site.id, body.idempotency_key, body.action, result)
    await asyncio.to_thread(LEDGER.record_event, site_id=site.id, event_type=body.action, item_id=str(body.args.get("product_id") or ""))
    # Mirror the add into the session cart so chat can answer "check my cart".
    # Runs for the demo AND real connectors — the mirror is what this chat added,
    # never a claim about the site's authoritative cart. The idempotent-replay
    # guard above returns before here, so a repeated key can't double a quantity.
    product_id = str(body.args.get("product_id") or "")
    if body.action == "cart.add" and body.session_id and product_id:
        try:
            await asyncio.to_thread(
                LEDGER.cart_add,
                site_id=site.id,
                session_id=body.session_id,
                product_id=product_id,
                quantity=int(body.args.get("quantity") or 1),
            )
        except Exception:
            # A mirror failure must never fail the customer's add.
            log.exception("cart mirror failed site=%s product=%s", site.id, product_id)
    return result


async def _commerce_chat_reply(site: SiteConfig, last_msg: str, session_id: str) -> dict | None:
    """Deterministic commerce lane for both /chat and /chat/stream.

    `commerce_reply` is pure and ledger-free, so the cart intent comes back as a
    marker; this resolves it against the session-cart mirror + catalogue and
    composes the final reply. Returns None to fall through to lead/FAQ/model."""
    await _refresh_catalog_feed(site)
    # commerce_reply + load_catalog read the catalogue file (feeds are capped at
    # 5 MB), so they run off the event loop — a blocking read here would stall
    # every other in-flight request on this worker.
    commerce = await asyncio.to_thread(commerce_reply, site.id, last_msg)
    if not commerce:
        return None
    if commerce.get("intent") != "commerce.cart":
        return commerce
    # The site declared cart.add — without it there is no cart to speak of, so
    # let the question fall through rather than answer for a cart-less site.
    if "cart.add" not in site.allowed_actions:
        return None
    if not session_id:
        return cart_summary_reply([])
    rows = await asyncio.to_thread(LEDGER.cart_items, site_id=site.id, session_id=session_id)
    catalog = await asyncio.to_thread(load_catalog, site.id)
    products = {p["id"]: p for p in catalog.get("products", [])}
    items = []
    for row in rows:
        product = products.get(row["product_id"])
        if not product:
            continue  # dropped from the feed since it was added
        price = product.get("price", {})
        items.append({
            "product_id": row["product_id"],
            "title": product.get("title") or "",
            "quantity": row["quantity"],
            "amount": price.get("amount"),
            "currency": price.get("currency", "AUD"),
            "url": product.get("url") or "",
        })
    return cart_summary_reply(items)


@app.post("/chat", response_model=ChatReply)
async def chat(body: ChatRequest, request: Request) -> ChatReply:
    origin = request.headers.get("origin", "")
    site, last_msg = _gate_request(origin, body, CONFIG)
    ip = _client_ip(request)
    _check_rate_limits(site, ip, CONFIG)

    if site.commerce_enabled:
        commerce = await _commerce_chat_reply(site, last_msg, body.session_id)
        if commerce:
            await asyncio.to_thread(
                LEDGER.record,
                site_id=site.id,
                ip_hash=hash_ip(ip),
                session_id=body.session_id,
                tokens_in=0,
                tokens_out=0,
                cost_usd=0.0,
                model="commerce",
                intent=commerce.get("intent", "commerce.search"),
            )
            await asyncio.to_thread(LEDGER.record_event, site_id=site.id, event_type="product_search")
            return ChatReply(
                reply=commerce["reply"],
                blocks=commerce["blocks"],
                sources=[],
                tokens_used=0,
                cost_usd=0.0,
                remaining_today_usd=max(0.0, site.daily_cap_usd - LEDGER.site_today_cost(site.id)),
                source="commerce",
            )

    # Lead capture — deterministic, free, before any retrieval. Commerce sites'
    # handoff.create branch above takes precedence when both are enabled.
    lead = _match_lead_intent(site, last_msg)
    if lead:
        await asyncio.to_thread(
            LEDGER.record,
            site_id=site.id,
            ip_hash=hash_ip(ip),
            session_id=body.session_id,
            tokens_in=0,
            tokens_out=0,
            cost_usd=0.0,
            model="lead",
            intent="lead",
        )
        return ChatReply(
            reply=lead["reply"],
            blocks=lead["blocks"],
            sources=[],
            tokens_used=0,
            cost_usd=0.0,
            remaining_today_usd=max(0.0, site.daily_cap_usd - LEDGER.site_today_cost(site.id)),
            source="lead",
        )

    # 3. Fetch corpus + try FAQ pre-bake.
    try:
        corpus_payload = await _site_corpus(site)
    except Exception as e:
        log.warning("corpus fetch failed for %s: %s", site.id, e)
        corpus_payload = {"chunks": [], "faqs": []}

    resolved = await _resolve_query(site, last_msg, corpus_payload, history=body.messages)

    if resolved["hit"] == "faq":
        faq_hit = resolved["faq"]
        await asyncio.to_thread(
            LEDGER.record,
            site_id=site.id,
            ip_hash=hash_ip(ip),
            session_id=body.session_id,
            tokens_in=0,
            tokens_out=0,
            cost_usd=0.0,
            model="faq",
            intent="faq",
        )
        faq_sources: list[dict] = []
        if faq_hit.get("url"):
            faq_sources.append(
                {
                    "id": "faq:" + faq_hit.get("q", "")[:40],
                    "title": faq_hit.get("title") or "FAQ",
                    "section": "",
                    "url": faq_hit["url"],
                }
            )
        return ChatReply(
            reply=faq_hit["a"],
            sources=faq_sources,
            tokens_used=0,
            cost_usd=0.0,
            remaining_today_usd=site.daily_cap_usd - LEDGER.site_today_cost(site.id),
            source="faq",
        )

    if resolved["hit"] == "curated":
        curated_hit = resolved["curated"]
        await asyncio.to_thread(
            LEDGER.record,
            site_id=site.id,
            ip_hash=hash_ip(ip),
            session_id=body.session_id,
            tokens_in=0,
            tokens_out=0,
            cost_usd=0.0,
            model="cached",
            intent="cached",
        )
        return ChatReply(
            reply=curated_hit["reply"],
            sources=curated_hit.get("sources") or [],
            tokens_used=0,
            cost_usd=0.0,
            remaining_today_usd=site.daily_cap_usd - LEDGER.site_today_cost(site.id),
            source="cached",
        )

    if resolved["hit"] == "low_confidence":
        # Honest "I'm not sure" + nearest-3 — free, no LLM, no hallucination.
        selected = resolved["selected"]
        reply_text = _low_confidence_reply(site, selected, resolved["max_score"])
        sources = [
            {
                "id": c.get("id", ""),
                "title": c.get("title", ""),
                "section": c.get("section", ""),
                "url": c.get("url", ""),
            }
            for c in selected[:3]
        ]
        await asyncio.to_thread(
            LEDGER.record,
            site_id=site.id,
            ip_hash=hash_ip(ip),
            session_id=body.session_id,
            tokens_in=0,
            tokens_out=0,
            cost_usd=0.0,
            model="low-confidence",
            intent="low-confidence",
        )
        await asyncio.to_thread(
            LEDGER.log_qa_pending,
            site_id=site.id,
            query=last_msg,
            reply=reply_text,
            sources=sources,
        )
        return ChatReply(
            reply=reply_text,
            sources=sources,
            tokens_used=0,
            cost_usd=0.0,
            remaining_today_usd=site.daily_cap_usd - LEDGER.site_today_cost(site.id),
            source="low-confidence",
        )

    # ── LLM call with retrieved chunks ──
    selected_chunks = resolved["selected"]
    system = _build_system_prompt(site, corpus_payload, selected_chunks=selected_chunks)
    provider = get_provider(CONFIG.defaults.provider)
    try:
        result = await provider.complete(
            messages=[m.model_dump() for m in body.messages],
            system=system,
            model=site.model,
            max_tokens=CONFIG.defaults.max_output_tokens,
        )
    except Exception:
        log.exception("provider call failed for site=%s", site.id)
        # Don't leak provider internals; return 502 + opaque message.
        raise HTTPException(status_code=502, detail="upstream model unavailable") from None

    # 5. Parse sources, record, log Q&A as pending, reply.
    clean_reply, sources = parse_sources(result.text, corpus_payload)
    await asyncio.to_thread(
        LEDGER.record,
        site_id=site.id,
        ip_hash=hash_ip(ip),
        session_id=body.session_id,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        cost_usd=result.cost_usd,
        model=result.model,
        intent="model",
    )
    await asyncio.to_thread(
        LEDGER.log_qa_pending,
        site_id=site.id,
        query=last_msg,
        reply=clean_reply,
        sources=sources,
    )
    remaining = site.daily_cap_usd - LEDGER.site_today_cost(site.id)
    return ChatReply(
        reply=clean_reply,
        sources=sources,
        tokens_used=result.tokens_in + result.tokens_out,
        cost_usd=result.cost_usd,
        remaining_today_usd=max(0.0, remaining),
        source="model",
    )


@app.post("/chat/stream")
async def chat_stream(body: ChatRequest, request: Request):
    origin = request.headers.get("origin", "")
    site, last_msg = _gate_request(origin, body, CONFIG)
    ip = _client_ip(request)
    _check_rate_limits(site, ip, CONFIG)

    if site.commerce_enabled:
        commerce = await _commerce_chat_reply(site, last_msg, body.session_id)
        if commerce:
            await asyncio.to_thread(
                LEDGER.record,
                site_id=site.id,
                ip_hash=hash_ip(ip),
                session_id=body.session_id,
                tokens_in=0,
                tokens_out=0,
                cost_usd=0.0,
                model="commerce",
                intent=commerce.get("intent", "commerce.search"),
            )
            await asyncio.to_thread(LEDGER.record_event, site_id=site.id, event_type="product_search")

            async def commerce_stream():
                yield f"data: {json.dumps({'delta': commerce['reply']})}\n\n"
                done = {"done": True, "source": "commerce", "clean_reply": commerce["reply"], "sources": [], "blocks": commerce["blocks"]}
                yield f"data: {json.dumps(done)}\n\n"

            return StreamingResponse(commerce_stream(), media_type="text/event-stream")

    lead = _match_lead_intent(site, last_msg)
    if lead:
        await asyncio.to_thread(
            LEDGER.record,
            site_id=site.id,
            ip_hash=hash_ip(ip),
            session_id=body.session_id,
            tokens_in=0,
            tokens_out=0,
            cost_usd=0.0,
            model="lead",
            intent="lead",
        )

        async def lead_stream():
            yield f"data: {json.dumps({'delta': lead['reply']})}\n\n"
            done = {"done": True, "source": "lead", "clean_reply": lead["reply"], "sources": [], "blocks": lead["blocks"]}
            yield f"data: {json.dumps(done)}\n\n"

        return StreamingResponse(lead_stream(), media_type="text/event-stream")

    try:
        corpus_payload = await _site_corpus(site)
    except Exception as e:
        log.warning("corpus fetch failed for %s: %s", site.id, e)
        corpus_payload = {"chunks": [], "faqs": []}

    resolved = await _resolve_query(site, last_msg, corpus_payload, history=body.messages)

    if resolved["hit"] == "faq":
        faq_hit = resolved["faq"]
        await asyncio.to_thread(
            LEDGER.record,
            site_id=site.id,
            ip_hash=hash_ip(ip),
            session_id=body.session_id,
            tokens_in=0,
            tokens_out=0,
            cost_usd=0.0,
            model="faq",
            intent="faq",
        )
        faq_sources: list[dict] = []
        if faq_hit.get("url"):
            faq_sources.append(
                {
                    "id": "faq:" + faq_hit.get("q", "")[:40],
                    "title": faq_hit.get("title") or "FAQ",
                    "section": "",
                    "url": faq_hit["url"],
                }
            )

        async def faq_stream():
            yield f"data: {json.dumps({'delta': faq_hit['a']})}\n\n"
            yield f"data: {json.dumps({'done': True, 'source': 'faq', 'clean_reply': faq_hit['a'], 'sources': faq_sources})}\n\n"

        return StreamingResponse(faq_stream(), media_type="text/event-stream")

    if resolved["hit"] == "curated":
        curated_hit = resolved["curated"]
        await asyncio.to_thread(
            LEDGER.record,
            site_id=site.id,
            ip_hash=hash_ip(ip),
            session_id=body.session_id,
            tokens_in=0,
            tokens_out=0,
            cost_usd=0.0,
            model="cached",
            intent="cached",
        )
        cached_reply = curated_hit["reply"]
        cached_sources = curated_hit.get("sources") or []

        async def cached_stream():
            yield f"data: {json.dumps({'delta': cached_reply})}\n\n"
            yield f"data: {json.dumps({'done': True, 'source': 'cached', 'clean_reply': cached_reply, 'sources': cached_sources})}\n\n"

        return StreamingResponse(cached_stream(), media_type="text/event-stream")

    if resolved["hit"] == "low_confidence":
        selected = resolved["selected"]
        reply_text = _low_confidence_reply(site, selected, resolved["max_score"])
        sources_payload = [
            {
                "id": c.get("id", ""),
                "title": c.get("title", ""),
                "section": c.get("section", ""),
                "url": c.get("url", ""),
            }
            for c in selected[:3]
        ]
        await asyncio.to_thread(
            LEDGER.record,
            site_id=site.id,
            ip_hash=hash_ip(ip),
            session_id=body.session_id,
            tokens_in=0,
            tokens_out=0,
            cost_usd=0.0,
            model="low-confidence",
            intent="low-confidence",
        )
        await asyncio.to_thread(
            LEDGER.log_qa_pending,
            site_id=site.id,
            query=last_msg,
            reply=reply_text,
            sources=sources_payload,
        )

        async def low_conf_stream():
            yield f"data: {json.dumps({'delta': reply_text})}\n\n"
            yield f"data: {json.dumps({'done': True, 'source': 'low-confidence', 'clean_reply': reply_text, 'sources': sources_payload})}\n\n"

        return StreamingResponse(low_conf_stream(), media_type="text/event-stream")

    selected_chunks = resolved["selected"]
    system = _build_system_prompt(site, corpus_payload, selected_chunks=selected_chunks)
    provider = get_provider(CONFIG.defaults.provider)

    async def event_stream():
        # Buffer-and-detect: forward deltas progressively, but stop forwarding
        # at the SOURCES marker so the user never sees the citation block.
        # Hold back the last MARGIN chars in case a partial marker is forming.
        MARGIN = max(16, len(SOURCES_MARKER) + 4)
        buf = ""
        sent_so_far = 0
        sources_seen = False

        try:
            async for delta in provider.stream(
                messages=[m.model_dump() for m in body.messages],
                system=system,
                model=site.model,
                max_tokens=CONFIG.defaults.max_output_tokens,
            ):
                buf += delta
                if sources_seen:
                    continue  # keep accumulating into buf, don't forward
                idx = buf.find(SOURCES_MARKER)
                if idx >= 0:
                    # Emit final visible chunk up to the marker, rstripped.
                    visible_end = idx
                    # Trim trailing whitespace/newlines before marker.
                    while visible_end > sent_so_far and buf[visible_end - 1] in " \n\r\t":
                        visible_end -= 1
                    if visible_end > sent_so_far:
                        chunk = buf[sent_so_far:visible_end]
                        yield f"data: {json.dumps({'delta': chunk})}\n\n"
                        sent_so_far = visible_end
                    sources_seen = True
                    continue
                # No marker yet — forward up to (len(buf) - MARGIN).
                safe_end = len(buf) - MARGIN
                if safe_end > sent_so_far:
                    chunk = buf[sent_so_far:safe_end]
                    yield f"data: {json.dumps({'delta': chunk})}\n\n"
                    sent_so_far = safe_end
        except Exception:
            log.exception("stream failed")
            yield f"data: {json.dumps({'error': 'upstream model unavailable'})}\n\n"
            return

        # Stream finished — flush any held-back tail (only if marker never appeared).
        if not sources_seen and len(buf) > sent_so_far:
            chunk = buf[sent_so_far:]
            yield f"data: {json.dumps({'delta': chunk})}\n\n"
            sent_so_far = len(buf)

        # Parse sources from the full buffer + estimate cost.
        clean_reply, sources = parse_sources(buf, corpus_payload)
        tokens_out_est = max(1, len(buf) // 4)
        history_chars = sum(len(m.content) for m in body.messages) + len(system)
        tokens_in_est = max(1, history_chars // 4)
        from providers.openai_provider import _cost as _openai_cost

        cost = _openai_cost(tokens_in_est, tokens_out_est, site.model)

        await asyncio.to_thread(
            LEDGER.record,
            site_id=site.id,
            ip_hash=hash_ip(ip),
            session_id=body.session_id,
            tokens_in=tokens_in_est,
            tokens_out=tokens_out_est,
            cost_usd=cost,
            model=site.model,
            intent="model",
        )
        await asyncio.to_thread(
            LEDGER.log_qa_pending,
            site_id=site.id,
            query=last_msg,
            reply=clean_reply,
            sources=sources,
        )
        remaining = site.daily_cap_usd - LEDGER.site_today_cost(site.id)
        done_payload = {
            "done": True,
            "clean_reply": clean_reply,
            "sources": sources,
            "cost_usd": cost,
            "remaining_today_usd": max(0.0, remaining),
            "source": "model",
        }
        yield f"data: {json.dumps(done_payload)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── Error handler — turn HTTPException with dict detail into JSON ──


@app.exception_handler(HTTPException)
async def _http_exc_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict):
        return JSONResponse(detail, status_code=exc.status_code, headers=exc.headers or {})
    return JSONResponse({"error": detail}, status_code=exc.status_code, headers=exc.headers or {})
