"""daily-brief — article inbox + reading digest.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: The saved-article inbox (ingest, read-state, summarize) and the hand-picked reading digest (add/remove/compile). Owns article id hashing + digest markdown assembly.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._locale (spine); shared._md_esc + DIGEST_SYSTEM + ARTICLE_SUMMARY_SYSTEM.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from emptyos.sdk import load_json, save_json, web_route
from .shared import _md_esc, DIGEST_SYSTEM, ARTICLE_SUMMARY_SYSTEM, _LANG
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import DailyBriefApp  # noqa: F401 — for type hints only


# ─── Bind to DailyBriefApp class as ────────────────────────────────
#   _digest_file           = _articles_mod._digest_file
#   _digest                = _articles_mod._digest
#   _save_digest           = _articles_mod._save_digest
#   api_action_targets     = _articles_mod.api_action_targets
#   api_article_save       = _articles_mod.api_article_save
#   api_digest             = _articles_mod.api_digest
#   api_digest_add         = _articles_mod.api_digest_add
#   api_digest_remove      = _articles_mod.api_digest_remove
#   _compile_digest        = _articles_mod._compile_digest
#   api_digest_compile     = _articles_mod.api_digest_compile
#   _articles_file         = _articles_mod._articles_file
#   _articles              = _articles_mod._articles
#   _save_articles         = _articles_mod._save_articles
#   _article_id            = _articles_mod._article_id  # @staticmethod
#   _ingest_articles       = _articles_mod._ingest_articles
#   headlines              = _articles_mod.headlines
#   get_articles           = _articles_mod.get_articles
#   api_articles           = _articles_mod.api_articles
#   api_article_read       = _articles_mod.api_article_read
#   api_articles_read_all  = _articles_mod.api_articles_read_all
#   api_article_summarize  = _articles_mod.api_article_summarize
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


# ── Per-article actions: save to read-later · keep in digest ───
# Both are user-initiated (a button click is the consent), so they
# dispatch directly — no review gate. Save hands off to the global
# bookmarks read-later app (feature-detected, hides when absent); Digest
# is an in-app curated shortlist this app owns (its own JSON file, so it
# never races the history state in load_state/save_state).
def _digest_file(self):
    return self.data_dir / "digest.json"


def _digest(self) -> list[dict]:
    items = load_json(self._digest_file(), [])
    return items if isinstance(items, list) else []


def _save_digest(self, items: list[dict]):
    save_json(self._digest_file(), items[:200])


@web_route("GET", "/api/action-targets")
async def api_action_targets(self, request):
    """Which per-article actions are available right now, so the UI only
    renders a button when its target exists."""
    try:
        enabled = set(self.kernel.apps.enabled_ids())
    except Exception:  # noqa: BLE001 — fail-soft, assume nothing extra
        enabled = set()
    return {"bookmarks": "bookmarks" in enabled, "digest": True}


@web_route("POST", "/api/article/save")
async def api_article_save(self, request):
    """Hand one article to the bookmarks read-later queue."""
    body = await request.json()
    url = str(body.get("url", "")).strip()
    title = str(body.get("title", "")).strip()
    if not url and not title:
        return {"ok": False, "error": "url or title required"}
    try:
        bm = await self.call_app(
            "bookmarks", "save_link", url=url, title=title,
            tags=["daily-brief"], notes=str(body.get("summary", "")).strip())
    except Exception as e:  # noqa: BLE001 — app missing / load failure
        self.log_activity({"event": "save_failed", "error": str(e)[:200]})
        return {"ok": False, "error": "read-later app unavailable"}
    return {"ok": True, "id": (bm or {}).get("id")}


@web_route("GET", "/api/digest")
async def api_digest(self, request):
    return {"items": self._digest()}


@web_route("POST", "/api/digest/add")
async def api_digest_add(self, request):
    """Digest is the superset of Save: stash to the read-later queue first
    (best-effort — skipped if bookmarks isn't installed), then add to the
    in-app digest shortlist."""
    body = await request.json()
    url = str(body.get("url", "")).strip()
    title = str(body.get("title", "")).strip()
    summary = str(body.get("summary", "")).strip()
    if not url and not title:
        return {"ok": False, "error": "url or title required"}
    items = self._digest()
    if url and any(it.get("url") == url for it in items):
        return {"ok": True, "count": len(items), "dup": True}
    # 1) save to read-later (best-effort, never blocks the digest add)
    saved = False
    try:
        if "bookmarks" in set(self.kernel.apps.enabled_ids()):
            await self.call_app("bookmarks", "save_link", url=url, title=title,
                                tags=["daily-brief", "digest"], notes=summary)
            saved = True
    except Exception as e:  # noqa: BLE001 — read-later is a bonus, not load-bearing
        self.log_activity({"event": "digest_save_failed", "error": str(e)[:200]})
    # 2) add to the digest shortlist
    items.insert(0, {
        "url": url,
        "title": title or url,
        "summary": summary[:600],
        "source": str(body.get("source", "")).strip(),
        "saved": saved,
        "added_at": datetime.now(UTC).isoformat(),
    })
    self._save_digest(items)
    await self.emit("daily-brief:digest_added", {"url": url, "title": title or url})
    return {"ok": True, "count": len(items), "saved": saved}


@web_route("POST", "/api/digest/remove")
async def api_digest_remove(self, request):
    body = await request.json()
    url = str(body.get("url", "")).strip()
    items = [it for it in self._digest() if it.get("url") != url]
    self._save_digest(items)
    return {"ok": True, "count": len(items)}


async def _compile_digest(self, items: list[dict]) -> str:
    """Turn the hand-picked shortlist into one AI roundup — mirrors the
    news-center.digest() headlines→think pattern, with this app's focus
    lens + locale conventions (same as _distill)."""
    loc = self._locale()
    lang_name, lang = _LANG[loc]
    focus = str(self._cfg("focus", "") or "").strip()
    focus_clause = (
        f"This reader cares about: {focus}. Frame each pick through that lens."
        if focus else "Frame each pick by general consequence; no special-interest lens."
    )
    system = DIGEST_SYSTEM.format(lang=lang, lang_name=lang_name, focus_clause=focus_clause)
    lines = []
    for it in items:
        src = f" [{it['source']}]" if it.get("source") else ""
        note = f" — {it['summary']}" if it.get("summary") else ""
        lines.append(f"{it.get('title', '')}{src} <{it.get('url', '')}>{note}")
    user = "The articles they kept:\n\n" + "\n".join(lines)
    out = await self.think(user, domain="reason", system=system, temperature=0.4)
    return (out if isinstance(out, str) else str(out)).strip()


@web_route("POST", "/api/digest/compile")
async def api_digest_compile(self, request):
    """Compile the digest shortlist into an AI roundup + file it to the vault."""
    items = self._digest()
    if not items:
        return {"ok": False, "error": "digest is empty — add articles with 🗂️ first"}
    try:
        md = await self._compile_digest(items)
    except Exception as e:  # noqa: BLE001 — surface to UI, don't crash
        return {"ok": False, "error": str(e)[:300]}
    if not md:
        return {"ok": False, "error": "could not compile a digest"}
    date = datetime.now().strftime("%Y-%m-%d")
    rel = f"30_Resources/EmptyOS/daily-brief/outputs/digest-{date}.md"
    body = (
        f"{md}\n\n---\n\n## Picked articles\n\n"
        + "\n".join(f"- [{_md_esc(it.get('title', ''))}]({it.get('url', '')})"
                    f"{(' · _' + it['source'] + '_') if it.get('source') else ''}"
                    for it in items)
        + f"\n\n<sub>{len(items)} articles · compiled {datetime.now().strftime('%H:%M')}</sub>\n"
    )
    self.vault_create_note(rel, {
        "tags": ["daily-brief", "digest"],
        "author": "ai",
        "date": date,
        "item_count": len(items),
    }, body)
    await self.emit("daily-brief:digest_compiled", {"note_path": rel, "item_count": len(items)})
    return {"ok": True, "digest_md": md, "note_path": rel,
            "item_count": len(items), "provenance": self.last_provenance()}


# ── Article inbox (the firehose — read/unread browse) ──────────
# generate() ingests every fetched item here so the Inbox view can browse
# the full feed, not just the curated brief. Own JSON file (no history race).
def _articles_file(self):
    return self.data_dir / "articles.json"


def _articles(self) -> list[dict]:
    items = load_json(self._articles_file(), [])
    return items if isinstance(items, list) else []


def _save_articles(self, items: list[dict]):
    # Newest first, cap 500. Archived/old naturally fall off the tail.
    items.sort(key=lambda a: a.get("fetched_at", ""), reverse=True)
    save_json(self._articles_file(), items[:500])


@staticmethod
def _article_id(url: str) -> str:
    return hashlib.md5((url or "").encode("utf-8")).hexdigest()[:12]


def _ingest_articles(self, fetched: list[dict]):
    """Upsert fetched feed items into the inbox store, preserving the
    read flag + any per-article summary on items already seen."""
    existing = {a.get("id"): a for a in self._articles() if a.get("id")}
    now = datetime.now(UTC).isoformat()
    for it in fetched:
        url = (it.get("url") or "").strip()
        if not url:
            continue
        aid = self._article_id(url)
        prev = existing.get(aid)
        existing[aid] = {
            "id": aid,
            "title": it.get("title") or url,
            "url": url,
            "source": it.get("source") or "",
            "category": it.get("category") or "general",
            "summary": (prev or {}).get("summary", ""),
            "read": bool((prev or {}).get("read", False)),
            "fetched_at": (prev or {}).get("fetched_at") or now,
        }
    self._save_articles(list(existing.values()))


async def headlines(self, limit: int = 5) -> list[dict]:
    """Latest fetched headlines for digest consumers (briefing)."""
    try:
        n = max(1, int(limit))
    except (TypeError, ValueError):
        n = 5
    return [
        {"title": a.get("title", ""), "source": a.get("source", ""),
         "url": a.get("url", "")}
        for a in self._articles()[:n]
    ]


async def get_articles(self, unread_only: bool = False, limit: int = 20) -> list[dict]:
    """Article rows for cross-app consumers (opportunity-radar).

    Rows carry the news-center-era alias keys consumers read —
    published (= fetched_at) and description (= summary)."""
    items = self._articles()
    if unread_only:
        items = [a for a in items if not a.get("read")]
    try:
        n = max(1, int(limit))
    except (TypeError, ValueError):
        n = 20
    return [
        {**a,
         "published": a.get("fetched_at", ""),
         "description": a.get("summary", "")}
        for a in items[:n]
    ]


@web_route("GET", "/api/articles")
async def api_articles(self, request):
    qp = request.query_params
    data = self._articles()
    if qp.get("unread") == "1":
        data = [a for a in data if not a.get("read")]
    cat = (qp.get("category") or "").strip()
    if cat:
        data = [a for a in data if a.get("category") == cat]
    src = (qp.get("source") or "").strip()
    if src:
        data = [a for a in data if a.get("source") == src]
    q = (qp.get("q") or "").strip().lower()
    if q:
        data = [a for a in data
                if q in (a.get("title", "") + " " + a.get("summary", "")).lower()]
    try:
        limit = max(1, min(200, int(qp.get("limit", "60"))))
    except (TypeError, ValueError):
        limit = 60
    # Facets for the filter chips, computed over the unfiltered store.
    alla = self._articles()
    cats = sorted({a.get("category", "general") for a in alla})
    srcs = sorted({a.get("source", "") for a in alla if a.get("source")})
    return {"items": data[:limit], "total": len(alla),
            "unread": sum(1 for a in alla if not a.get("read")),
            "categories": cats, "sources": srcs}


@web_route("POST", "/api/articles/{id}/read")
async def api_article_read(self, request):
    aid = request.path_params.get("id")
    items = self._articles()
    hit = next((a for a in items if a.get("id") == aid), None)
    if not hit:
        return {"ok": False, "error": "not found"}
    hit["read"] = True
    self._save_articles(items)
    return {"ok": True}


@web_route("POST", "/api/articles/read-all")
async def api_articles_read_all(self, request):
    items = self._articles()
    for a in items:
        a["read"] = True
    self._save_articles(items)
    return {"ok": True, "count": len(items)}


@web_route("POST", "/api/articles/{id}/summarize")
async def api_article_summarize(self, request):
    aid = request.path_params.get("id")
    items = self._articles()
    hit = next((a for a in items if a.get("id") == aid), None)
    if not hit:
        return {"ok": False, "error": "not found"}
    if hit.get("summary"):
        return {"ok": True, "summary": hit["summary"], "cached": True}
    loc = self._locale()
    lang_name, lang = _LANG[loc]
    system = ARTICLE_SUMMARY_SYSTEM.format(lang=lang, lang_name=lang_name)
    user = f"Title: {hit.get('title', '')}\nSource: {hit.get('source', '')}\nBlurb: {hit.get('summary', '') or '(none)'}"
    try:
        out = await self.think(user, domain="text", system=system, temperature=0.3)
    except Exception as e:  # noqa: BLE001 — surface, don't crash
        return {"ok": False, "error": str(e)[:300]}
    summary = (out if isinstance(out, str) else str(out)).strip()
    hit["summary"] = summary
    self._save_articles(items)
    return {"ok": True, "summary": summary, "provenance": self.last_provenance()}
