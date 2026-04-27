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
import json
import logging
import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from config import Config, load_config
from corpus import CorpusCache, match_curated, match_faq, stuff_corpus
from ledger import Ledger, hash_ip
from providers import get_provider

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
    source: str  # "faq" | "model" | "cached"


# ── App + globals ──────────────────────────────────────────────────

app = FastAPI(title="EmptyOS Chatbot Service", version="0.1.0")

CONFIG: Config | None = None
CORPUS: CorpusCache | None = None
LEDGER: Ledger | None = None
ADMIN_TOKEN = os.environ.get("CHATBOT_ADMIN_TOKEN", "")


@app.on_event("startup")
async def _startup() -> None:
    global CONFIG, CORPUS, LEDGER
    CONFIG = load_config()
    CORPUS = CorpusCache(ttl_seconds=CONFIG.defaults.corpus_ttl_seconds)
    LEDGER = Ledger()
    log.info("loaded %d sites: %s", len(CONFIG.sites), list(CONFIG.sites.keys()))


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


def _build_system_prompt(site, corpus_payload: dict) -> str:
    persona = site.persona.strip() or f"You are a helpful guide to {site.name}."
    corpus_text = stuff_corpus(corpus_payload)
    return SYSTEM_TEMPLATE.format(
        persona=persona,
        site_name=site.name,
        marker=SOURCES_MARKER,
        corpus=corpus_text or "(no published content yet)",
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
                line = line[i + 1:].strip()
        if not line or line.lower() == "none":
            continue
        # Strip any markdown link wrapping the id, e.g. "[id](url)".
        if line.startswith("[") and "]" in line:
            line = line[1:line.index("]")]
        cid = line.strip("`'\" ").strip()
        if not cid or cid in seen:
            continue
        chunk = chunks_by_id.get(cid)
        if not chunk:
            continue
        seen.add(cid)
        sources.append({
            "id": cid,
            "title": chunk.get("title", ""),
            "section": chunk.get("section", ""),
            "url": chunk.get("url", ""),
        })
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

    # 1. Origin lock — must match an entry in allowed_origins.
    if req_origin not in site.allowed_origins:
        raise HTTPException(status_code=401, detail="origin not allowed")

    # 2. Message validation + token cap.
    if not body.messages or body.messages[-1].role != "user":
        raise HTTPException(status_code=400, detail="last message must be from user")
    last = body.messages[-1].content
    if len(last) > cfg.defaults.max_input_chars:
        raise HTTPException(status_code=400, detail=f"message too long (>{cfg.defaults.max_input_chars} chars)")
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


# ── Routes ─────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "sites": list(CONFIG.sites.keys()) if CONFIG else []}


@app.get("/sites/{site_id}/meta")
async def site_meta(site_id: str) -> dict:
    if not CONFIG or site_id not in CONFIG.sites:
        raise HTTPException(404, "unknown site")
    s = CONFIG.sites[site_id]
    return {
        "id": s.id,
        "name": s.name,
        "starter_questions": s.starter_questions,
    }


@app.post("/admin/refresh/{site_id}")
async def admin_refresh(site_id: str, x_admin_token: str = Header(default="")) -> dict:
    if not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(401, "unauthorized")
    if not CONFIG or site_id not in CONFIG.sites:
        raise HTTPException(404, "unknown site")
    CORPUS.invalidate(site_id)
    return {"ok": True, "site": site_id}


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

    ok = await asyncio.to_thread(
        LEDGER.update_qa, qa_id, status=new_status, reply=new_reply
    )
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


@app.post("/chat", response_model=ChatReply)
async def chat(body: ChatRequest, request: Request) -> ChatReply:
    origin = request.headers.get("origin", "")
    site, last_msg = _gate_request(origin, body, CONFIG)
    ip = _client_ip(request)
    _check_rate_limits(site, ip, CONFIG)

    # 3. Fetch corpus + try FAQ pre-bake.
    try:
        corpus_payload = await CORPUS.get(site.id, site.corpus_url)
    except Exception as e:
        log.warning("corpus fetch failed for %s: %s", site.id, e)
        corpus_payload = {"chunks": [], "faqs": []}

    faq_hit = match_faq(last_msg, corpus_payload.get("faqs", []) or [])
    if faq_hit:
        # Free reply — no LLM, no ledger debit beyond a zero-cost row.
        await asyncio.to_thread(
            LEDGER.record,
            site_id=site.id, ip_hash=hash_ip(ip), session_id=body.session_id,
            tokens_in=0, tokens_out=0, cost_usd=0.0, model="faq",
        )
        # FAQs may declare an optional `url` field; surface as a single source.
        faq_sources: list[dict] = []
        if faq_hit.get("url"):
            faq_sources.append({
                "id": "faq:" + faq_hit.get("q", "")[:40],
                "title": faq_hit.get("title") or "FAQ",
                "section": "",
                "url": faq_hit["url"],
            })
        return ChatReply(
            reply=faq_hit["a"],
            sources=faq_sources,
            tokens_used=0,
            cost_usd=0.0,
            remaining_today_usd=site.daily_cap_usd - LEDGER.site_today_cost(site.id),
            source="faq",
        )

    # 3b. Curated cache — owner-approved past replies. Inherits sources.
    curated_hit = match_curated(last_msg, await asyncio.to_thread(LEDGER.find_curated, site.id))
    if curated_hit:
        await asyncio.to_thread(
            LEDGER.record,
            site_id=site.id, ip_hash=hash_ip(ip), session_id=body.session_id,
            tokens_in=0, tokens_out=0, cost_usd=0.0, model="cached",
        )
        return ChatReply(
            reply=curated_hit["reply"],
            sources=curated_hit.get("sources") or [],
            tokens_used=0,
            cost_usd=0.0,
            remaining_today_usd=site.daily_cap_usd - LEDGER.site_today_cost(site.id),
            source="cached",
        )

    # 4. LLM call.
    system = _build_system_prompt(site, corpus_payload)
    provider = get_provider(CONFIG.defaults.provider)
    try:
        result = await provider.complete(
            messages=[m.model_dump() for m in body.messages],
            system=system,
            model=site.model,
            max_tokens=CONFIG.defaults.max_output_tokens,
        )
    except Exception as e:
        log.exception("provider call failed for site=%s", site.id)
        # Don't leak provider internals; return 502 + opaque message.
        raise HTTPException(status_code=502, detail="upstream model unavailable")

    # 5. Parse sources, record, log Q&A as pending, reply.
    clean_reply, sources = parse_sources(result.text, corpus_payload)
    await asyncio.to_thread(
        LEDGER.record,
        site_id=site.id, ip_hash=hash_ip(ip), session_id=body.session_id,
        tokens_in=result.tokens_in, tokens_out=result.tokens_out,
        cost_usd=result.cost_usd, model=result.model,
    )
    await asyncio.to_thread(
        LEDGER.log_qa_pending,
        site_id=site.id, query=last_msg, reply=clean_reply, sources=sources,
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

    try:
        corpus_payload = await CORPUS.get(site.id, site.corpus_url)
    except Exception as e:
        log.warning("corpus fetch failed for %s: %s", site.id, e)
        corpus_payload = {"chunks": [], "faqs": []}

    faq_hit = match_faq(last_msg, corpus_payload.get("faqs", []) or [])
    if faq_hit:
        await asyncio.to_thread(
            LEDGER.record,
            site_id=site.id, ip_hash=hash_ip(ip), session_id=body.session_id,
            tokens_in=0, tokens_out=0, cost_usd=0.0, model="faq",
        )
        faq_sources: list[dict] = []
        if faq_hit.get("url"):
            faq_sources.append({
                "id": "faq:" + faq_hit.get("q", "")[:40],
                "title": faq_hit.get("title") or "FAQ",
                "section": "",
                "url": faq_hit["url"],
            })
        async def faq_stream():
            yield f"data: {json.dumps({'delta': faq_hit['a']})}\n\n"
            yield f"data: {json.dumps({'done': True, 'source': 'faq', 'clean_reply': faq_hit['a'], 'sources': faq_sources})}\n\n"
        return StreamingResponse(faq_stream(), media_type="text/event-stream")

    # Curated-cache check — same lookup-order as /chat.
    curated_hit = match_curated(last_msg, await asyncio.to_thread(LEDGER.find_curated, site.id))
    if curated_hit:
        await asyncio.to_thread(
            LEDGER.record,
            site_id=site.id, ip_hash=hash_ip(ip), session_id=body.session_id,
            tokens_in=0, tokens_out=0, cost_usd=0.0, model="cached",
        )
        cached_reply = curated_hit["reply"]
        cached_sources = curated_hit.get("sources") or []
        async def cached_stream():
            yield f"data: {json.dumps({'delta': cached_reply})}\n\n"
            yield f"data: {json.dumps({'done': True, 'source': 'cached', 'clean_reply': cached_reply, 'sources': cached_sources})}\n\n"
        return StreamingResponse(cached_stream(), media_type="text/event-stream")

    system = _build_system_prompt(site, corpus_payload)
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
        except Exception as e:
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
            site_id=site.id, ip_hash=hash_ip(ip), session_id=body.session_id,
            tokens_in=tokens_in_est, tokens_out=tokens_out_est,
            cost_usd=cost, model=site.model,
        )
        await asyncio.to_thread(
            LEDGER.log_qa_pending,
            site_id=site.id, query=last_msg, reply=clean_reply, sources=sources,
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
